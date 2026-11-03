"""Testes `docker` que dependem de arquivos das outras tarefas (T2 a T6) ou das imagens da api e dos workers.

Escritos completos; nao sao pulados. Com o repositorio ja mesclado (V1/T8) rodam de ponta a ponta; num
worktree da T7 sozinho eles FALHAM com a mensagem do que falta (requirements, .env.example, web/static,
codigo da api e dos workers).

Cobertura: INFRA-CFG-01 (docker compose config), INFRA-CFG-06/12/13 (.env.example), INFRA-DF-01/02 (build
das 4 imagens e permissoes reais), INFRA-DF-04 (requirements com == e hashes), INFRA-ZOS-01 (subida do zero).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

import pytest

import docker_helpers as dh
from tls_support import make_tls_pair

pytestmark = pytest.mark.docker

REPO = dh.REPO
PROJECT = "livestats-t7test"
IMAGES = {
    "api": "livestats/api:local",
    "worker-lives": "livestats/worker-lives:local",
    "worker-inscritos": "livestats/worker-inscritos:local",
    "web": "livestats/web:local",
}
REQUIREMENTS = {
    "api": "api/requirements.txt",
    "worker-lives": "worker_lives/requirements.txt",
    "worker-inscritos": "worker_inscritos/requirements.txt",
}
BASE_ENV = {"ADMIN_USERNAME": "t7test", "YOUTUBE_CHANNEL_ID": "UCxxxxxxxxxxxxxxxxxxxxxx", "COMPOSE_PROJECT_NAME": PROJECT}


def compose_cmd(*args, env=None, check=True, timeout=900):
    full_env = {**os.environ, **BASE_ENV, **(env or {})}
    r = subprocess.run(
        ["docker", "compose", "-f", str(REPO / "docker-compose.yml"), *args],
        capture_output=True, text=True, timeout=timeout, env=full_env, encoding="utf-8", cwd=REPO,
    )
    if check and r.returncode != 0:
        raise AssertionError(f"docker compose {' '.join(args)} falhou:\n{r.stdout}\n{r.stderr}")
    return r


def test_infra_cfg_01_docker_compose_config_valido():
    dh.require_docker()
    out = compose_cmd("config", "--format", "json").stdout
    config = json.loads(out)
    assert config["name"] == PROJECT or config["name"] == "livestats"
    assert set(config["services"]) == {"web", "api", "worker-lives", "worker-inscritos"}
    web_ports = {(p["published"], p["target"], p.get("host_ip")) for p in config["services"]["web"]["ports"]}
    assert web_ports == {("8080", 8080, None), ("8081", 8081, "127.0.0.1")}


def test_infra_cfg_06_12_13_env_example():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    values = dict(re.findall(r"(?m)^([A-Z_]+)=(.*)$", text))
    assert values["ADMIN_BIND"].strip() == "127.0.0.1"
    value = values["TRUSTED_PROXY_CIDR"].strip()
    if value:
        assert re.fullmatch(r"\d+\.\d+\.\d+\.(\d+)(/32)?", value), "TRUSTED_PROXY_CIDR: so um /32"
        assert not value.split("/")[0].endswith(".1"), "TRUSTED_PROXY_CIDR: nunca o gateway"
    # D33: lista de permissao do admin (padrao so o loopback); a variavel do proxy do admin deixou de existir
    assert values["ADMIN_ALLOW_CIDRS"].strip() == "127.0.0.1/32"
    assert "ADMIN_TRUSTED_PROXY_IP" not in text
    assert "AIza" not in text and "benja" not in text.lower()


@pytest.mark.parametrize("svc", sorted(REQUIREMENTS))
def test_infra_df_04_requirements_com_versao_exata_e_hashes(svc):
    path = REPO / REQUIREMENTS[svc]
    assert path.exists(), f"{path} (tarefa dona ainda nao entregue)"
    text = path.read_text(encoding="utf-8")
    joined = re.sub(r"\\\s*\n", " ", text)
    entries = [l.strip() for l in joined.splitlines() if l.strip() and not l.strip().startswith(("#", "-"))]
    assert entries
    for line in entries:
        assert re.match(r"^[A-Za-z0-9_.\-\[\]]+==[^\s;]+", line), f"sem == : {line[:60]}"
        assert "--hash=sha256:" in line, f"sem hash: {line[:60]}"
    assert not re.search(r">=|~=|<=|\*", " ".join(l.split(" ")[0] for l in entries))
    if svc == "worker-inscritos":
        assert re.search(r"(?mi)^playwright==\d", text), "Playwright precisa de versao exata (fixa o Chromium)"


@pytest.fixture(scope="module")
def built_images():
    dh.require_docker()
    compose_cmd("build", env={})
    return IMAGES


@pytest.mark.parametrize("svc", sorted(IMAGES))
def test_infra_df_01_imagem_construida_nao_root_com_healthcheck(built_images, svc):
    info = json.loads(dh.docker("image", "inspect", built_images[svc]).stdout)[0]
    user = info["Config"]["User"]
    assert re.fullmatch(r"\d+(:\d+)?", user) and int(user.split(":")[0]) != 0
    assert info["Config"]["Healthcheck"]["Test"][0] == "CMD"
    env = info["Config"].get("Env") or []
    assert not dh.sensitive_env_vars(env), env
    history = dh.docker("history", "--no-trunc", "--format", "{{.CreatedBy}}", built_images[svc]).stdout
    assert "secrets/" not in history and "AIza" not in history


@pytest.mark.parametrize("svc", ["api", "worker-lives", "worker-inscritos"])
def test_infra_df_02_data_root_10001_2770(built_images, svc):
    out = dh.docker("run", "--rm", "--entrypoint", "stat", built_images[svc], "-c", "%u:%g %a", "/data").stdout.strip()
    assert out == "0:10001 2770"


def test_infra_df_02_admin_state_so_na_imagem_da_api(built_images):
    out = dh.docker("run", "--rm", "--entrypoint", "stat", built_images["api"], "-c", "%u:%g %a", "/admin-state").stdout.strip()
    assert out == "10001:10001 700"
    for svc in ("worker-lives", "worker-inscritos", "web"):
        r = dh.docker("run", "--rm", "--entrypoint", "stat", built_images[svc], "/admin-state", check=False)
        assert r.returncode != 0, f"{svc} nao pode ter /admin-state"


def test_infra_df_01_imagem_do_worker_inscritos_tem_chromium_fixado_pela_biblioteca(built_images):
    code = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as p:\n"
        "    b = p.chromium.launch(args=['--no-sandbox'])\n"
        "    print(b.version)\n"
        "    b.close()\n"
    )
    r = dh.docker(
        "run", "--rm", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--tmpfs", "/tmp", "--tmpfs", "/home/app:uid=10001,gid=10001", "--shm-size", "512m",
        "--entrypoint", "python", built_images["worker-inscritos"], "-c", code,
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", r.stdout.strip())


# --------------------------------------------------------------------------------------- INFRA-ZOS-01
def _gen_secrets(api_image: str) -> list[Path]:
    """Segredos de TESTE gerados na hora (nunca a chave real) com as bibliotecas da propria imagem da api."""
    code = (
        "import base64, nacl.public, argon2\n"
        "k = nacl.public.PrivateKey.generate()\n"
        "print(base64.b64encode(bytes(k)).decode())\n"
        "print(base64.b64encode(bytes(k.public_key)).decode())\n"
        "print(argon2.PasswordHasher().hash('t7test-password'))\n"
    )
    priv, pub, hashed = dh.docker("run", "--rm", "--entrypoint", "python", api_image, "-c", code).stdout.split()
    folder = REPO / "secrets"
    folder.mkdir(exist_ok=True)
    files = {
        "cookie_private_key.txt": priv,
        "cookie_public_key.txt": pub,
        "admin_password_hash.txt": hashed,
        "youtube_api_key.txt": "t7test-sentinel-not-a-real-key",
    }
    created = []
    # D33: o web nao sobe sem o par TLS do admin (certificado de teste gerado na hora)
    for pem in ("admin_tls_cert.pem", "admin_tls_key.pem"):
        assert not (folder / pem).exists(), f"{folder / pem} ja existe: o teste nao sobrescreve segredos reais"
    cert, key = make_tls_pair(folder, ips=("127.0.0.1",))
    for path in (cert, key):
        assert path.stat().st_mtime > time.time() - 30
        created.append(path)
    for name, value in files.items():
        path = folder / name
        assert not path.exists(), f"{path} ja existe: o teste nao sobrescreve segredos reais"
        path.write_text(value + "\n", encoding="utf-8")
        os.chmod(path, 0o644)
        created.append(path)
    return created


def test_infra_zos_01_subida_do_zero_com_volumes_novos(built_images):
    created = _gen_secrets(built_images["api"])
    try:
        compose_cmd("down", "-v", "--remove-orphans", check=False)
        compose_cmd("up", "-d", "--no-build")
        deadline = time.time() + 120
        states = {}
        while time.time() < deadline:
            ps = json.loads("[" + ",".join(compose_cmd("ps", "--format", "json", "-a").stdout.split("\n")[:-1]) + "]")
            states = {c["Service"]: c.get("Health") for c in ps}
            if len(states) == 4 and all(h == "healthy" for h in states.values()):
                break
            time.sleep(3)
        assert states and all(h == "healthy" for h in states.values()) and len(states) == 4, states
        for svc in ("api", "worker-lives", "worker-inscritos"):
            uid = compose_cmd("exec", "-T", svc, "id", "-u").stdout.strip()
            assert uid == "10001"
        assert compose_cmd("exec", "-T", "web", "id", "-u").stdout.strip() == "101"
    finally:
        compose_cmd("down", "-v", "--remove-orphans", check=False)
        for path in created:
            path.unlink(missing_ok=True)
        try:
            (REPO / "secrets").rmdir()
        except OSError:
            pass
