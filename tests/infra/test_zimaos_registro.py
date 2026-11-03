"""INFRA-ZIM-*: registro do LiveStats no painel (tela inicial) do ZimaOS (C17).

Os scripts REAIS de deploy/ rodam num container `docker:27-cli` (alpine, sh de verdade, `docker compose` de verdade,
usuario root do container) com o repositorio montado somente leitura. O que e substituido, e so isto: o `docker`
do PATH e um embrulho (`/shim/docker`) que repassa tudo ao docker real (`docker compose config` roda de verdade),
EXCETO o que precisaria de um daemon (`info`, `inspect`, `up`, `down`), que ele apenas registra num log e simula
(`up -f X` passa a fazer o label config_files valer X, como o Docker faz). A logica testada (geracao, checagem de
segredos, instalacao, idempotencia, `--remover`, escolha do arquivo no `up`/`down`) e a dos scripts, sem copia.
Em producao: LIVESTATS_ZIMAOS_APPS=/var/lib/casaos/apps e sudo; aqui /apps e sem sudo (LIVESTATS_SUDO vazio).
"""

from __future__ import annotations

import json
import re
import subprocess

import docker_helpers as dh
import pytest

IMAGE = "docker:27-cli"
REPO = dh.REPO

SEGREDOS = {
    "youtube_api_key.txt": "FAKEYTKEY-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n",
    "cookie_private_key.txt": "FAKEPRIVKEY-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\n",
    "cookie_public_key.txt": "FAKEPUBKEY-cccccccccccccccccccccccccccccccccccc\n",
    "admin_password_hash.txt": "FAKEHASH-dddddddddddddddddddddddddddddddddddddd\n",
    "admin_tls_cert.pem": "-----BEGIN CERTIFICATE-----\nFAKECERTLINE-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\n-----END CERTIFICATE-----\n",
    "admin_tls_key.pem": "-----BEGIN " + "PRIVATE KEY-----\nFAKEKEYLINE-ffffffffffffffffffffffffffffffff\n-----END " + "PRIVATE KEY-----\n",
}
# uma linha caracteristica de cada arquivo (a que sera plantada no .env nos testes de vazamento)
LINHA_DE = {
    "youtube_api_key.txt": "FAKEYTKEY-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "cookie_private_key.txt": "FAKEPRIVKEY-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "cookie_public_key.txt": "FAKEPUBKEY-cccccccccccccccccccccccccccccccccccc",
    "admin_password_hash.txt": "FAKEHASH-dddddddddddddddddddddddddddddddddddddd",
    "admin_tls_cert.pem": "FAKECERTLINE-eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
    "admin_tls_key.pem": "FAKEKEYLINE-ffffffffffffffffffffffffffffffff",
}

ENV_BASE = """ADMIN_USERNAME="Vitex"
YOUTUBE_CHANNEL_ID="UCxxxxxxxxxxxxxxxxxxxxxx"
PUBLIC_BASE_URL="https://lives.exemplo.test"
ADMIN_BIND="0.0.0.0"
ADMIN_ALLOW_CIDRS="192.168.15.0/24 100.64.0.0/10"
TRUSTED_PROXY_CIDR="172.29.80.11/32"
"""

SHIM = r"""#!/bin/sh
echo "$*" >> /log/docker.log
case "$*" in
  info*) exit 0 ;;
  *" up "*|*" up")
    f=""; prev=""
    for a in "$@"; do [ "$prev" = -f ] && f=$a; prev=$a; done
    printf '%s' "$f" > /log/label
    exit 0 ;;
  *" down"*) exit 0 ;;
  *"network connect"*|*"network disconnect"*) exit 0 ;;
  *inspect*)
    case "$*" in
      *Health*) echo healthy ;;
      *config_files*) if [ -f /log/label ]; then cat /log/label; echo; else echo "${FAKE_LABEL:-}"; fi ;;
    esac
    exit 0 ;;
esac
exec /usr/local/bin/docker "$@"
"""

# Monta /work (compose REAL do repo + .env + secrets), /apps (destino) e o embrulho do docker.
SETUP = r"""
mkdir -p /work/secrets /apps /shim /log
cp /repo/docker-compose.yml /work/docker-compose.yml
cat > /work/.env <<'ENVEOF'
@@ENV@@
ENVEOF
@@SEGREDOS@@
cat > /shim/docker <<'SHIMEOF'
@@SHIM@@
SHIMEOF
chmod +x /shim/docker
export PATH=/shim:$PATH
reg() { sh /repo/deploy/registrar_zimaos.sh --dir /work "$@"; }
ARQ=/apps/livestats/docker-compose.yml
"""


def montar_setup(env_text: str = ENV_BASE) -> str:
    segs = []
    for nome, conteudo in SEGREDOS.items():
        segs.append(f"cat > /work/secrets/{nome} <<'SEGEOF'\n{conteudo.rstrip(chr(10))}\nSEGEOF")
    return (
        SETUP.replace("@@ENV@@", env_text.rstrip("\n"))
        .replace("@@SEGREDOS@@", "\n".join(segs))
        .replace("@@SHIM@@", SHIM.rstrip("\n"))
    )


def rodar(corpo: str, env_text: str = ENV_BASE, timeout: int = 240):
    dh.require_docker()
    cmd = [
        "docker", "run", "--rm", "-v", f"{REPO}:/repo:ro",
        "-e", "HOME=/root", "-e", "LIVESTATS_ZIMAOS_APPS=/apps", "-e", "LIVESTATS_SUDO=",
        IMAGE, "sh", "-c", montar_setup(env_text) + corpo,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=timeout)
    return r


def secoes(stdout: str) -> dict[str, str]:
    """Texto entre marcadores '@@NOME' (linha inteira) ate o proximo marcador."""
    out: dict[str, list[str]] = {}
    atual = None
    for linha in stdout.splitlines():
        m = re.match(r"^@@([A-Z0-9_]+)(?: (.*))?$", linha)
        if m:
            atual = m.group(1)
            out[atual] = [m.group(2)] if m.group(2) is not None else []
        elif atual:
            out[atual].append(linha)
    return {k: "\n".join(v) for k, v in out.items()}


DOCKER_REAL = "/usr/local/bin/docker"
JSON_ORIG = f"{DOCKER_REAL} compose --project-directory /work -f /work/docker-compose.yml --env-file /work/.env config --format json"
JSON_GER = f"{DOCKER_REAL} compose -f $ARQ config --format json"

CORPO_GERA = rf"""
reg > /log/out1 2>&1; echo "@@RC1 $?"
echo "@@OUT1"; cat /log/out1
echo "@@STAT $(stat -c '%a %U %G' $ARQ 2>&1)"
echo "@@GERADO"; cat $ARQ
echo "@@JSON_GER"; {JSON_GER}; echo
echo "@@JSON_ORIG"; {JSON_ORIG}; echo
echo "@@DOCKERLOG"; cat /log/docker.log
"""


@pytest.fixture(scope="module")
def geracao():
    r = rodar(CORPO_GERA)
    assert r.returncode == 0, r.stdout + r.stderr
    s = secoes(r.stdout)
    assert s["RC1"].strip() == "0", s["OUT1"]
    return s


pytestmark = pytest.mark.docker


def test_infra_zim_01_arquivo_resolvido_do_compose_real(geracao):
    texto = geracao["GERADO"]
    assert re.search(r"^name: livestats$", texto, re.M), "projeto precisa se chamar livestats"
    assert re.search(r"^x-casaos:", texto, re.M), "bloco x-casaos (cartao do painel) precisa sobreviver"
    assert "pt_br: LiveStats" in texto and "port_map:" in texto
    # caminhos absolutos: nada relativo (./secrets, context: .)
    assert "./secrets" not in texto
    assert re.search(r"^\s+context: /work$", texto, re.M), "build context absoluto"
    for nome in SEGREDOS:
        assert f"source: /work/secrets/{nome}" in texto or f"/work/secrets/{nome}" in texto, nome
    assert not re.search(r"^\s+context: \.\s*$", texto, re.M)
    # .env interpolado
    assert "192.168.15.0/24 100.64.0.0/10" in texto
    assert "172.29.80.11/32" in texto
    assert "${" not in texto


def test_infra_zim_02_protecoes_sobrevivem_semanticamente(geracao):
    gerado = json.loads(geracao["JSON_GER"])
    original = json.loads(geracao["JSON_ORIG"])
    assert gerado["name"] == original["name"] == "livestats"
    for chave in ("services", "networks", "secrets", "volumes"):
        assert gerado[chave] == original[chave], f"{chave} divergem entre o gerado e o original"
    # Confere os valores de fato (nao so a igualdade: gerado e original poderiam estar ambos errados)
    svcs = gerado["services"]
    assert set(svcs) == {"web", "api", "worker-lives", "worker-inscritos"}
    for nome, svc in svcs.items():
        assert svc["read_only"] is True, nome
        assert svc["cap_drop"] == ["ALL"], nome
        assert "no-new-privileges:true" in svc["security_opt"], nome
        assert svc["user"] not in ("", "0", "root", None), nome
    assert svcs["web"]["user"] == "101:101"
    assert {s["source"] for s in svcs["worker-inscritos"]["secrets"]} >= {"cookie_private_key", "cookie_public_key"}
    assert "cookie_private_key" not in {s["source"] for s in svcs["api"]["secrets"]}
    assert gerado["networks"]["internal"]["internal"] is True
    publicadas = {(p["published"], p["host_ip"] if "host_ip" in p else "") for p in svcs["web"]["ports"]}
    assert publicadas == {("8080", ""), ("8081", "0.0.0.0")}
    for nome in ("api", "worker-lives", "worker-inscritos"):
        assert not svcs[nome].get("ports"), f"{nome} nao publica portas"


def test_infra_zim_03_instalado_como_root_modo_600_e_sobe_pelo_arquivo(geracao):
    assert geracao["STAT"].strip() == "600 root root"
    log = geracao["DOCKERLOG"]
    # primeira vez: o label ainda nao aponta para o registrado, entao RECRIA (o hash nao muda ao trocar de pasta)
    assert "compose -f /apps/livestats/docker-compose.yml up -d --force-recreate" in log
    assert "down" not in log and " -v" not in log, "volumes nunca sao tocados"


@pytest.mark.parametrize("nome", sorted(SEGREDOS))
def test_infra_zim_04_segredo_no_yaml_aborta_e_nao_instala(nome):
    # ADMIN_USERNAME vai literal para o environment da api: o conteudo do arquivo de segredo cai no YAML gerado.
    env = ENV_BASE.replace('ADMIN_USERNAME="Vitex"', f'ADMIN_USERNAME="{LINHA_DE[nome]}"')
    corpo = r"""
reg > /log/out1 2>&1; echo "@@RC1 $?"
echo "@@OUT1"; cat /log/out1
echo "@@EXISTE $(ls /apps/livestats 2>&1 | tr '\n' ' ')"
echo "@@DOCKERLOG"; cat /log/docker.log
"""
    r = rodar(corpo, env_text=env)
    s = secoes(r.stdout)
    assert s["RC1"].strip() != "0", r.stdout
    assert f"secrets/{nome}" in s["OUT1"] and "ABORTADO" in s["OUT1"], s["OUT1"]
    assert LINHA_DE[nome] not in r.stdout + r.stderr, "o script nao pode imprimir o conteudo do segredo"
    assert "No such file" in s["EXISTE"], "nada pode ter sido instalado"
    assert " up" not in s["DOCKERLOG"], "e nenhum container pode ter sido recriado"


def test_infra_zim_05_segredo_que_nao_aparece_nao_bloqueia_e_aborta_sem_x_casaos_ou_name():
    corpo = r"""
sed -i '/^x-casaos:/,/^$/d' /work/docker-compose.yml
reg > /log/out1 2>&1; echo "@@RC1 $?"
echo "@@OUT1"; cat /log/out1
echo "@@EXISTE $(ls /apps/livestats 2>&1 | tr '\n' ' ')"
cp /repo/docker-compose.yml /work/docker-compose.yml
sed -i '/^name: livestats$/d' /work/docker-compose.yml
reg > /log/out2 2>&1; echo "@@RC2 $?"
echo "@@OUT2"; cat /log/out2
echo "@@EXISTE2 $(ls /apps/livestats 2>&1 | tr '\n' ' ')"
"""
    r = rodar(corpo)
    s = secoes(r.stdout)
    assert s["RC1"].strip() != "0" and "x-casaos" in s["OUT1"], s["OUT1"]
    assert "No such file" in s["EXISTE"]
    assert s["RC2"].strip() != "0" and "name: livestats" in s["OUT2"], s["OUT2"]
    assert "No such file" in s["EXISTE2"]


def test_infra_zim_06_idempotente_so_reescreve_se_mudou():
    corpo = r"""
reg > /log/out1 2>&1; echo "@@RC1 $?"
echo "@@S1 $(stat -c '%i %Y' $ARQ)"
sleep 2
reg > /log/out2 2>&1; echo "@@RC2 $?"
echo "@@S2 $(stat -c '%i %Y' $ARQ)"
echo "@@OUT2"; cat /log/out2
sleep 2
sed -i 's#^TRUSTED_PROXY_CIDR=.*#TRUSTED_PROXY_CIDR="172.29.80.12/32"#' /work/.env
reg > /log/out3 2>&1; echo "@@RC3 $?"
echo "@@S3 $(stat -c '%i %Y' $ARQ)"
echo "@@GERADO"; cat $ARQ
echo "@@DOCKERLOG"; grep ' up ' /log/docker.log
echo "@@RESTOS $(ls -A /apps/livestats | tr '\n' ' ')"
"""
    r = rodar(corpo)
    s = secoes(r.stdout)
    assert [s["RC1"].strip(), s["RC2"].strip(), s["RC3"].strip()] == ["0", "0", "0"], r.stdout
    assert s["S1"] == s["S2"], "segunda execucao com o mesmo conteudo nao pode reescrever o arquivo"
    assert "ja esta igual" in s["OUT2"]
    assert s["S3"] != s["S2"], "mudou o .env: o arquivo precisa ser reescrito"
    assert "172.29.80.12/32" in s["GERADO"] and "172.29.80.11/32" not in s["GERADO"]
    ups = [l for l in s["DOCKERLOG"].splitlines() if l.strip()]
    assert "--force-recreate" in ups[0]
    # depois que o label aponta para o registrado, o up e comum (so recria o que mudou)
    assert all("--force-recreate" not in u for u in ups[1:]), ups
    assert s["RESTOS"].strip() == "docker-compose.yml", "nenhum arquivo temporario sobra no destino"


def test_infra_zim_07_recriar_web_forca_so_o_web():
    corpo = r"""
reg > /log/out1 2>&1
reg --recriar-web > /log/out2 2>&1; echo "@@RC2 $?"
echo "@@DOCKERLOG"; grep ' up ' /log/docker.log
"""
    s = secoes(rodar(corpo).stdout)
    assert s["RC2"].strip() == "0"
    ups = [l for l in s["DOCKERLOG"].splitlines() if l.strip()]
    assert ups[-1].endswith("up -d --force-recreate web"), ups


def test_infra_zim_08_remover_apaga_so_o_registro_e_volta_ao_compose_de_dir():
    corpo = r"""
mkdir -p /apps/outro-app && echo x > /apps/outro-app/docker-compose.yml
reg > /log/out1 2>&1; echo "@@RC1 $?"
: > /log/docker.log
reg --remover > /log/out2 2>&1; echo "@@RC2 $?"
echo "@@OUT2"; cat /log/out2
echo "@@APPS $(ls /apps | tr '\n' ' ')"
echo "@@DOCKERLOG"; cat /log/docker.log
echo "@@LABEL $(cat /log/label)"
reg --remover > /log/out3 2>&1; echo "@@RC3 $?"
echo "@@OUT3"; cat /log/out3
"""
    s = secoes(rodar(corpo).stdout)
    assert s["RC1"].strip() == "0" and s["RC2"].strip() == "0", s
    assert s["APPS"].strip() == "outro-app", "so a pasta livestats some; o resto de apps/ fica"
    log = s["DOCKERLOG"]
    assert "-f /work/docker-compose.yml" in log and "up -d --force-recreate" in log
    assert "/apps/livestats" not in log, "depois de remover, nada sobe pelo arquivo registrado"
    assert not re.search(r"\bdown\b|\s-v\b|volume rm", log), "volumes nunca sao removidos"
    assert s["LABEL"].strip() == "/work/docker-compose.yml"
    assert s["RC3"].strip() == "0" and "Nao estava registrado" in s["OUT3"]


def test_infra_zim_09_lib_e_rollback_usam_o_arquivo_registrado():
    corpo = r"""
. /repo/deploy/lib.sh
zimaos_registrado && echo "@@ANTES registrado" || echo "@@ANTES nao"
reg > /log/out0 2>&1
zimaos_registrado && echo "@@DEPOIS registrado" || echo "@@DEPOIS nao"
: > /log/docker.log
sh /repo/deploy/rollback.sh --dir /work --yes > /log/rb1 2>&1; echo "@@RC_REG $?"
echo "@@LOG_REG"; grep -E ' down' /log/docker.log
echo "@@MSG_REG"; cat /log/rb1
rm -rf /apps/livestats
: > /log/docker.log
sh /repo/deploy/rollback.sh --dir /work --yes > /log/rb2 2>&1; echo "@@RC_SEM $?"
echo "@@LOG_SEM"; grep -E ' down' /log/docker.log
"""
    s = secoes(rodar(corpo).stdout)
    assert s["ANTES"].strip() == "nao" and s["DEPOIS"].strip() == "registrado"
    assert s["RC_REG"].strip() == "0", s["MSG_REG"]
    assert s["LOG_REG"].strip() == "compose -f /apps/livestats/docker-compose.yml down"
    assert s["RC_SEM"].strip() == "0"
    assert s["LOG_SEM"].strip() == "compose down", "sem registro, o rollback segue como antes"


# ---- sem terminal para o sudo: usuario NAO root, stdin sem tty, sudo falso que registra e falha ----------------
# Registra como root (sem sudo), depois roda os scripts REAIS como `nobody`. O `sudo` falso (/shim/sudo) anota a
# chamada em /log/sudo.log e falha. Com LIVESTATS_SUDO=/shim/sudo e sem tty, `zimaos_sudo_ok` precisa barrar.
PREPARO_NOBODY = r"""
reg > /log/out0 2>&1; echo "@@RC0 $?"
cat > /shim/sudo <<'SUDOEOF'
#!/bin/sh
echo "$*" >> /log/sudo.log
exit 1
SUDOEOF
chmod +x /shim/sudo
touch /log/docker.log /log/sudo.log
chmod 777 /log; chmod 666 /log/docker.log /log/sudo.log
chmod 777 /work; chmod 666 /work/.env; chmod -R a+rX /work
: > /log/docker.log
ENV_ANTES=$(md5sum < /work/.env); REG_ANTES=$(md5sum < $ARQ)
como_nobody() { su -s /bin/sh nobody -c "$1"; }
"""

def _estado_final():
    return r"""
echo "@@ENV_IGUAL $([ "$ENV_ANTES" = "$(md5sum < /work/.env)" ] && echo sim || echo nao)"
echo "@@REG_IGUAL $([ "$REG_ANTES" = "$(md5sum < $ARQ)" ] && echo sim || echo nao)"
echo "@@SUDOLOG"; cat /log/sudo.log
echo "@@DOCKERLOG"; cat /log/docker.log
"""

CN = "sh /repo/deploy/connect_npm.sh --dir /work --npm fake-npm --ip 172.29.80.11"
RB = "sh /repo/deploy/rollback.sh --dir /work --yes"
RG = "sh /repo/deploy/registrar_zimaos.sh --dir /work"


def _roda_nobody(cmd: str, sudo: str) -> dict[str, str]:
    corpo = PREPARO_NOBODY + (
        f"como_nobody 'LIVESTATS_SUDO={sudo} PATH=/shim:$PATH {cmd} </dev/null > /log/o 2>&1; echo $? > /log/rc'\n"
        'echo "@@RC $(cat /log/rc)"\necho "@@OUT"; cat /log/o\n'
        'echo "@@QUEM $(su -s /bin/sh nobody -c "id -u")"\n'
    ) + _estado_final()
    r = rodar(corpo)
    s = secoes(r.stdout)
    assert s["RC0"].strip() == "0", r.stdout + r.stderr
    assert s["QUEM"].strip() != "0", "o script tem que rodar sem ser root"
    return s


def test_infra_zim_10_connect_npm_sem_terminal_aborta_antes_de_mexer():
    s = _roda_nobody(CN, "/shim/sudo")
    assert s["RC"].strip() != "0", s["OUT"]
    assert "terminal para o sudo" in s["OUT"] and "Nada foi alterado" in s["OUT"], s["OUT"]
    assert "network connect" not in s["DOCKERLOG"] and "network disconnect" not in s["DOCKERLOG"]
    assert " up" not in s["DOCKERLOG"]
    assert s["ENV_IGUAL"].strip() == "sim", ".env nao pode mudar"
    assert s["SUDOLOG"].strip() == "", "o sudo nem deve ser tentado"


def test_infra_zim_11_rollback_sem_terminal_aborta_antes_do_down():
    s = _roda_nobody(RB, "/shim/sudo")
    assert s["RC"].strip() != "0", s["OUT"]
    assert "terminal para o sudo" in s["OUT"] and "Nada foi alterado" in s["OUT"], s["OUT"]
    assert " down" not in s["DOCKERLOG"] and "network disconnect" not in s["DOCKERLOG"]
    assert s["ENV_IGUAL"].strip() == "sim", ".env nao pode mudar"
    assert s["SUDOLOG"].strip() == ""


def test_infra_zim_12_registrar_sem_terminal_aborta_antes_de_gerar():
    s = _roda_nobody(RG, "/shim/sudo")
    assert s["RC"].strip() != "0", s["OUT"]
    assert "terminal para a senha" in s["OUT"], s["OUT"]
    assert " config" not in s["DOCKERLOG"], "nem chega a gerar o compose"
    assert s["REG_IGUAL"].strip() == "sim" and s["ENV_IGUAL"].strip() == "sim"
    assert s["SUDOLOG"].strip() == ""


def test_infra_zim_13_mesmo_cenario_sem_sudo_passa_da_guarda():
    """Controle: com LIVESTATS_SUDO vazio os mesmos scripts, como nao root e sem tty, passam da guarda."""
    s = _roda_nobody(CN, "")
    assert "terminal para o sudo" not in s["OUT"], s["OUT"]
    assert "network connect --ip 172.29.80.11 livestats-edge fake-npm" in s["DOCKERLOG"], s["DOCKERLOG"]
    s = _roda_nobody(RB, "")
    assert "terminal para o sudo" not in s["OUT"], s["OUT"]
    assert "compose -f /apps/livestats/docker-compose.yml down" in s["DOCKERLOG"], s["DOCKERLOG"]
    s = _roda_nobody(RG, "")
    assert "terminal para a senha" not in s["OUT"], s["OUT"]
    assert " config" in s["DOCKERLOG"], "passou da guarda e foi gerar o compose"
