"""INFRA-DF-*: verificacoes estaticas dos Dockerfiles e do .dockerignore (sem Docker)."""

from __future__ import annotations

import re
import shlex

import pytest

DOCKERFILES = {
    "api": "api/Dockerfile",
    "worker-lives": "worker_lives/Dockerfile",
    "worker-inscritos": "worker_inscritos/Dockerfile",
    "web": "web/Dockerfile",
}
PYTHON = ("api", "worker-lives", "worker-inscritos")
SECRET_NAME = re.compile(r"KEY|PASSWORD|PASSWD|TOKEN|SECRET|CREDENTIAL|COOKIE|HASH", re.I)
FORBIDDEN_COPY = ("secrets", ".env", "codigo-original", "tests", ".git", "docs")


def instructions(repo, svc):
    """[(INSTRUCAO, argumentos)] com continuacoes de linha unidas e comentarios removidos."""
    text = (repo / DOCKERFILES[svc]).read_text(encoding="utf-8")
    joined = re.sub(r"\\\s*\n", " ", text)
    out = []
    for line in joined.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, _, rest = line.partition(" ")
        out.append((name.upper(), rest.strip()))
    return out


def stages(repo, svc):
    """Lista de listas de instrucoes, uma por estagio (FROM inclusive)."""
    result = []
    for name, rest in instructions(repo, svc):
        if name == "FROM":
            result.append([])
        result[-1].append((name, rest))
    return result


@pytest.mark.parametrize("svc", sorted(DOCKERFILES))
def test_infra_df_01_multistage_digest_usuario_healthcheck(repo, svc):
    st = stages(repo, svc)
    assert len(st) >= 2, "multi-stage exige ao menos 2 FROM"
    for stage in st:
        image = stage[0][1].split()[0]
        assert re.search(r"@sha256:[0-9a-f]{64}$", image), f"FROM sem digest: {image}"
        assert not re.search(r":(latest|current)\b", image)
    final = st[-1]
    users = [rest for name, rest in final if name == "USER"]
    assert users, "estagio final sem USER"
    assert re.fullmatch(r"\d+(:\d+)?", users[-1]) and int(users[-1].split(":")[0]) != 0
    assert any(name == "HEALTHCHECK" for name, _ in final)
    # o USER nao-root vem depois de qualquer RUN que precise de root e antes do fim
    assert final[-1][0] in {"HEALTHCHECK", "CMD", "EXPOSE", "ENTRYPOINT"}


@pytest.mark.parametrize("svc", sorted(DOCKERFILES))
def test_infra_df_01_sem_segredo_nem_copy_indevido(repo, svc):
    for name, rest in instructions(repo, svc):
        if name in {"ARG", "ENV"}:
            for token in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=", rest) or [rest.split()[0]]:
                assert not SECRET_NAME.search(token), f"{name} com nome de segredo: {token}"
        if name in {"COPY", "ADD"}:
            parts = [p for p in shlex.split(rest) if not p.startswith("--")]
            sources, _dest = parts[:-1], parts[-1]
            if any(p.startswith("--from=") for p in shlex.split(rest)):
                continue
            for src in sources:
                norm = src.lstrip("./")
                assert norm not in {"", "."}, f"COPY do contexto inteiro: {rest}"
                assert not norm.startswith(FORBIDDEN_COPY), f"COPY proibido: {rest}"
                assert not src.startswith("http"), "ADD/COPY de URL"
        assert name != "ADD", "use COPY"


@pytest.mark.parametrize("svc", PYTHON)
def test_infra_df_01_pip_com_hashes(repo, svc):
    installs = [rest for name, rest in instructions(repo, svc) if name == "RUN" and "pip install" in rest]
    assert installs, "nenhum pip install"
    for cmd in installs:
        assert "--require-hashes" in cmd
        assert re.search(r"-r\s+\S*requirements\.txt", cmd)


@pytest.mark.parametrize("svc", PYTHON)
def test_infra_df_01_usuario_unico_10001(repo, svc):
    text = (repo / DOCKERFILES[svc]).read_text(encoding="utf-8")
    assert "--uid 10001" in text and "--gid 10001" in text
    assert [rest for name, rest in stages(repo, svc)[-1] if name == "USER"][-1] == "10001:10001"


@pytest.mark.parametrize("svc", PYTHON)
def test_infra_df_02_data_root_10001_2770(repo, svc):
    run = " ".join(rest for name, rest in stages(repo, svc)[-1] if name == "RUN")
    assert "mkdir /data" in run
    assert "chown root:10001 /data" in run
    assert "chmod 2770 /data" in run


def test_infra_df_02_admin_state_so_na_api(repo):
    api_run = " ".join(rest for name, rest in stages(repo, "api")[-1] if name == "RUN")
    assert "/admin-state" in api_run
    assert "chown 10001:10001 /admin-state" in api_run
    assert "chmod 0700 /admin-state" in api_run
    for svc in ("worker-lives", "worker-inscritos", "web"):
        assert "admin-state" not in " ".join(f"{n} {r}" for n, r in instructions(repo, svc))


def test_infra_df_02_api_copia_os_esquemas_sql(repo):
    copies = [rest for name, rest in instructions(repo, "api") if name == "COPY"]
    joined = " ".join(copies)
    assert "db/schema.sql" in joined and "db/admin_schema.sql" in joined


def test_infra_df_03_dockerignore(repo):
    lines = {
        line.strip()
        for line in (repo / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    }
    for required in ("secrets/", ".git", "tests/", "codigo-original/", "*.tar.gz", "data/"):
        assert required in lines, f".dockerignore sem {required}"
    assert any(line.startswith(".env") for line in lines)
    assert not any(line.startswith("!") for line in lines), "nada de excecoes (!) no .dockerignore"


def test_infra_df_04_chromium_fixado_pela_biblioteca(repo):
    text = "\n".join(rest for name, rest in instructions(repo, "worker-inscritos") if name == "RUN")
    installs = re.findall(r"playwright install ([^&\n]*?)\s*(?:&&|$)", text)
    assert installs, "o Dockerfile precisa instalar o Chromium pelo Playwright"
    for args in installs:
        assert args.split()[-1] == "chromium"
        assert not re.search(r"chromium[@=]", args), "versao do Chromium so pode vir da biblioteca"
    assert "playwright==" in text, "o build deve exigir playwright== no requirements"
    assert not re.search(r"google-chrome|chromium-browser|apt-get install[^\n]*chrom", text, re.I)
    assert not re.search(r":(latest|current)\b", text)


def test_infra_df_01_web_script_de_ip_real_e_executavel_na_imagem(repo):
    """O entrypoint do nginx so le (source) arquivos .envsh COM bit de execucao; o bit nao vem do Git no Windows."""
    copies = [rest for name, rest in instructions(repo, "web") if name == "COPY" and "15-livestats-realip.envsh" in rest]
    origin = [c for c in copies if "--from=" not in c]
    assert len(origin) == 1 and "--chmod=0755" in origin[0]
    # o estagio final copia do estagio validado (preserva o modo)
    assert any("--from=confcheck" in c for c in copies)


@pytest.mark.parametrize(
    "svc,compose_svc",
    [("api", "api"), ("worker-lives", "worker-lives"), ("worker-inscritos", "worker-inscritos"), ("web", "web")],
)
def test_infra_df_01_healthcheck_igual_ao_do_compose(repo, compose, svc, compose_svc):
    hc = [rest for name, rest in stages(repo, svc)[-1] if name == "HEALTHCHECK"][-1]
    flags = dict(re.findall(r"--(\w[\w-]*)=(\S+)", hc))
    cmd_json = hc[hc.index("CMD") + 3 :]
    import json

    docker_cmd = json.loads(cmd_json)
    spec = compose["services"][compose_svc]["healthcheck"]
    assert docker_cmd == spec["test"][1:], "comando do HEALTHCHECK difere do compose"
    assert spec["test"][0] == "CMD"
    assert flags["interval"] == spec["interval"]
    assert flags["timeout"] == spec["timeout"]
    assert flags["retries"] == str(spec["retries"])
    assert flags["start-period"] == spec["start_period"]


def test_infra_df_01_varredura_de_env_aceita_so_gpg_key_herdada():
    import docker_helpers as dh

    assert dh.sensitive_env_vars(["GPG_KEY=0000000000000000000000000000000000000000", "PATH=/usr/bin"]) == []
    assert dh.sensitive_env_vars(["YOUTUBE_API_KEY=x"]) == ["YOUTUBE_API_KEY=x"]
    assert dh.sensitive_env_vars(
        ["GPG_KEY=1", "ADMIN_PASSWORD=x", "MY_GPG_KEY=1", "COOKIE_HASH=y", "API_TOKEN=z", "APP_SECRET=s"]
    ) == ["ADMIN_PASSWORD=x", "MY_GPG_KEY=1", "COOKIE_HASH=y", "API_TOKEN=z", "APP_SECRET=s"]
