"""INFRA-ZIM-S*: verificacoes estaticas do registro no painel do ZimaOS (C17), sem Docker.

Garantem que os scripts de deploy respeitam o registro (nenhum recria container "por fora" do arquivo registrado)
e que `enviar.sh` so usa terminal (ssh -t) onde o sudo pode pedir senha, sem mexer no caminho com segredos.
O comportamento em execucao esta em test_zimaos_registro.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"


def ler(nome: str) -> str:
    return (DEPLOY / nome).read_text(encoding="utf-8")


def ramo(texto: str, modo: str) -> str:
    """Corpo do ramo `modo)` do `case $MODO` do enviar.sh (ate o `;;` seguinte)."""
    m = re.search(rf"^  {re.escape(modo)}\)\n(.*?)^    ;;", texto, re.M | re.S)
    assert m, f"ramo {modo} nao encontrado no enviar.sh"
    return m.group(1)


def test_infra_zim_s01_enviar_modo_novo_usa_ssh_t_e_roda_o_registrar():
    env = ler("enviar.sh")
    assert re.search(r"^SSH_OPTS_TTY=\(-t ", env, re.M), "opcoes com terminal (-t)"
    assert re.search(r"^SSH_OPTS=\(-T ", env, re.M), "o caminho padrao (segredos por stdin) continua sem terminal (-T)"
    corpo = ramo(env, "registrar-zimaos")
    assert '"${SSH_OPTS_TTY[@]}"' in corpo and '"${SSH_OPTS[@]}"' not in corpo
    assert "sh $DEST/deploy/registrar_zimaos.sh --dir $DEST" in corpo
    for proibido in ("YT_KEY", "ADMIN_PW", "git archive", "tar "):
        assert proibido not in corpo, f"o modo registrar-zimaos nao pode mexer com {proibido}"
    assert "--remover" in env and "--recriar-web" in env, "flags repassadas ao script remoto"
    assert "registrar-zimaos" in env.split("set -euo pipefail")[0], "documentado no cabecalho do enviar.sh"


def test_infra_zim_s02_instalar_segue_sem_terminal_e_com_segredos_por_stdin():
    env = ler("enviar.sh")
    corpo = ramo(env, "instalar")
    assert '"${SSH_OPTS[@]}"' in corpo and "SSH_OPTS_TTY" not in corpo
    assert "printf '%s\\n' \"$YT_KEY\"" in corpo and "printf '%s\\n' \"$ADMIN_PW\"" in corpo
    assert "--secrets-stdin" in corpo
    # os modos com sudo possivel usam terminal; os de so leitura nao
    assert "SSH_OPTS_TTY" in ramo(env, "rollback") and "SSH_OPTS_TTY" in ramo(env, "conectar-npm")
    assert "SSH_OPTS_TTY" not in ramo(env, "status") and "SSH_OPTS_TTY" not in ramo(env, "preflight")


def test_infra_zim_s03_install_para_antes_de_recriar_containers_quando_registrado():
    inst = ler("install.sh")
    ini = inst.index('titulo "Subindo os servicos"')
    up = inst.index("docker_ compose up -d", ini)
    reg = inst.index("if zimaos_registrado; then", ini)
    assert ini < reg < up, "a checagem do registro vem ANTES do up -d"
    bloco = inst[reg:up]
    assert "exit 2" in bloco and "registrar-zimaos" in bloco
    assert "docker_ compose" not in bloco and "compose_registrado" not in bloco, "nao sobe nada nesse ramo"
    assert "--recriar-web" in bloco, "certificado novo precisa mandar recriar o web"
    assert "--force-recreate web" in inst[up:], "sem registro, o comportamento antigo continua"


def test_infra_zim_s04_connect_npm_valida_antes_de_mexer_e_recria_pelo_registro():
    c = ler("connect_npm.sh")
    chk = c.index("zimaos_registrado && ! zimaos_sudo_ok")
    assert chk < c.index("docker_ network connect") and chk < c.index("docker_ network disconnect")
    assert chk < c.index("env_set"), "aborta antes de qualquer alteracao"
    m = re.search(r"if zimaos_registrado; then\n(.*?)\nelse\n(.*?)\nfi", c[c.index("env_set"):], re.S)
    assert m, "ramo registrado/nao registrado do recriar web"
    assert 'registrar_zimaos.sh" --dir "$DIR" --recriar-web' in m.group(1)
    assert "docker_ compose up -d web" in m.group(2) and "docker_ compose up -d web" not in m.group(1)


def test_infra_zim_s05_rollback_valida_antes_e_derruba_pelo_arquivo_registrado():
    r = ler("rollback.sh")
    chk = r.index("zimaos_registrado && ! zimaos_sudo_ok")
    assert chk < r.index("network disconnect"), "valida o terminal antes de soltar qualquer rede"
    m = re.search(r"if zimaos_registrado; then\n\s+compose_registrado down\n.*?else\n\s+docker_ compose down\nfi", r, re.S)
    assert m, "down pelo registrado quando existe; senao o de sempre"
    assert " -v" not in re.sub(r"\(sem -v[^)]*\)", "", r.split("docker compose down")[0] + m.group(0)), "sem down -v"


def test_infra_zim_s06_lib_define_registro_com_padroes_de_producao():
    lib = ler("lib.sh")
    assert "ZIMAOS_APPS=${LIVESTATS_ZIMAOS_APPS:-/var/lib/casaos/apps}" in lib
    assert "ZIMAOS_SUDO=${LIVESTATS_SUDO-sudo}" in lib
    assert 'ZIMAOS_ARQ="$ZIMAOS_PASTA/docker-compose.yml"' in lib
    assert 'ZIMAOS_PASTA="$ZIMAOS_APPS/livestats"' in lib
    assert re.search(r'compose_registrado\(\) \{\n.*?\$ZIMAOS_SUDO docker compose -f "\$ZIMAOS_ARQ" "\$@"', lib, re.S)


def test_infra_zim_s07_registrar_usa_sudo_modo_600_root_e_nunca_apaga_volumes():
    s = ler("registrar_zimaos.sh")
    assert 'config > "$TMP"' in s and '--env-file "$DIR/.env"' in s and "--project-directory" in s
    assert '$ZIMAOS_SUDO install -d -m 755 "$ZIMAOS_PASTA"' in s
    assert '$ZIMAOS_SUDO install -m 600 -o root -g root "$TMP" "$ZIMAOS_ARQ.novo"' in s
    assert '$ZIMAOS_SUDO mv -f "$ZIMAOS_ARQ.novo" "$ZIMAOS_ARQ"' in s
    assert '$ZIMAOS_SUDO cmp -s "$TMP" "$ZIMAOS_ARQ"' in s
    assert '$ZIMAOS_SUDO rm -rf "$ZIMAOS_PASTA"' in s
    assert "mktemp" in s and "trap " in s and "umask 077" in s
    assert "grep -Fq -f" in s, "comparacao literal dos segredos"
    assert "compose_registrado up -d" in s
    for proibido in ("down", "volume rm", "prune", " -v "):
        assert not re.search(rf"^[^#\n]*docker[^\n]*{re.escape(proibido)}", s, re.M), f"{proibido!r} proibido"
    # ordem: segredos conferidos ANTES de instalar
    assert s.index("grep -Fq -f") < s.index("$ZIMAOS_SUDO install -m 600")


def test_infra_zim_s08_status_so_le():
    st = ler("status.sh")
    assert not re.search(r"compose\s+(up|down|-f)|registrar_zimaos|install -m", st), "status.sh nao recria nada"


@pytest.mark.parametrize("nome", ["lib.sh", "registrar_zimaos.sh", "install.sh", "connect_npm.sh", "rollback.sh"])
def test_infra_zim_s09_scripts_sh_sem_crlf_e_sem_bashismos_obvios(nome):
    t = ler(nome)
    assert "\r" not in t
    assert t.startswith("#!/bin/sh") or nome == "lib.sh"
    assert not re.search(r"\[\[(?!:)", t) and "function " not in t
