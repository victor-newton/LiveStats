"""INFRA-CFG-*: verificacoes estaticas do docker-compose.yml (sem Docker)."""

from __future__ import annotations

import re

import pytest

SERVICES = {"web", "api", "worker-lives", "worker-inscritos"}
WORKERS = {"worker-lives", "worker-inscritos"}


def env_of(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) if "=" in item else (item, None) for item in env)
    return env


def networks_of(service: dict) -> set:
    nets = service.get("networks") or []
    return set(nets.keys() if isinstance(nets, dict) else nets)


def mounts_of(service: dict) -> list:
    """[(origem, destino, modo)] dos volumes (sintaxe curta)."""
    out = []
    for vol in service.get("volumes") or []:
        assert isinstance(vol, str), f"use a sintaxe curta para volumes: {vol!r}"
        parts = vol.split(":")
        out.append((parts[0], parts[1], parts[2] if len(parts) > 2 else "rw"))
    return out


def test_infra_cfg_01_compose_estrutura(compose):
    assert compose["name"] == "livestats"
    assert set(compose["services"]) == SERVICES


@pytest.mark.parametrize("svc", sorted(SERVICES))
def test_infra_cfg_02_endurecimento(compose, svc):
    s = compose["services"][svc]
    user = str(s["user"])
    assert re.fullmatch(r"\d+(:\d+)?", user), f"user deve ser numerico: {user}"
    assert int(user.split(":")[0]) != 0
    assert s["read_only"] is True
    assert "ALL" in s["cap_drop"]
    assert "cap_add" not in s
    assert "no-new-privileges:true" in s["security_opt"]
    assert not s.get("privileged")
    assert s.get("network_mode") != "host"
    assert s.get("pid") != "host"
    assert "devices" not in s
    for src, dst, _ in mounts_of(s):
        assert "docker.sock" not in src and "docker.sock" not in dst


def test_infra_cfg_03_redes(compose):
    nets = compose["networks"]
    assert nets["internal"]["internal"] is True
    members = {n: {s for s, d in compose["services"].items() if n in networks_of(d)} for n in nets}
    assert members["internal"] == {"web", "api"}
    assert members["egress"] == WORKERS
    assert "web" in members["edge"]
    assert networks_of(compose["services"]["api"]) == {"internal"}
    assert "egress" not in networks_of(compose["services"]["web"])
    for w in WORKERS:
        assert networks_of(compose["services"][w]) == {"egress"}


def test_infra_cfg_04_secrets(compose):
    owners = {}
    for svc, d in compose["services"].items():
        for sec in d.get("secrets") or []:
            owners.setdefault(sec, set()).add(svc)
    assert owners == {
        "youtube_api_key": {"worker-lives"},
        "cookie_private_key": {"worker-inscritos"},
        "cookie_public_key": {"api", "worker-inscritos"},  # D37 (C14): o worker cifra a sessao renovada
        "admin_password_hash": {"api"},
        "admin_tls_cert": {"web"},  # D33: TLS proprio da 8081, so no web
        "admin_tls_key": {"web"},
    }
    assert sorted(compose["services"]["web"]["secrets"]) == ["admin_tls_cert", "admin_tls_key"]
    for name, d in compose["secrets"].items():
        ext = "pem" if name.startswith("admin_tls_") else "txt"
        assert d["file"] == f"./secrets/{name}.{ext}"


def test_infra_cfg_05_sem_segredo_em_environment(compose, compose_text):
    sensitive = re.compile(r"KEY|PASSWORD|TOKEN|COOKIE|SECRET|HASH")
    for svc, d in compose["services"].items():
        for name, value in env_of(d).items():
            if sensitive.search(name):
                assert name.endswith("_FILE"), f"{svc}: {name} deve ser *_FILE"
                assert str(value).startswith("/run/secrets/"), f"{svc}: {name}={value}"
    assert not re.search(r"AIza[0-9A-Za-z_-]{20,}", compose_text)
    # as variaveis antigas de segredo nao existem em lugar nenhum
    for svc, d in compose["services"].items():
        assert "YOUTUBE_API_KEY" not in env_of(d)


def test_infra_cfg_06_portas(compose, compose_text):
    ports = compose["services"]["web"]["ports"]
    assert len(ports) == 2
    assert "8080:8080" in ports
    assert "${ADMIN_BIND:-127.0.0.1}:8081:8081" in ports
    for svc, d in compose["services"].items():
        if svc != "web":
            assert "ports" not in d and "expose" not in d
    for banned in ("9222", ":8000", ":8001"):
        assert not any(banned in str(p) for p in ports)
    assert "0.0.0.0" not in " ".join(str(p) for p in ports)


def test_infra_cfg_07_volumes(compose):
    data = {s for s, d in compose["services"].items() if any(dst == "/data" for _, dst, _ in mounts_of(d))}
    assert data == {"api", "worker-lives", "worker-inscritos"}
    for s in data:
        assert ("livestats-data", "/data", "rw") in mounts_of(compose["services"][s])
    assert not compose["services"]["web"].get("volumes")
    for svc, d in compose["services"].items():
        for src, _, _ in mounts_of(d):
            assert not src.startswith((".", "/", "~")), f"{svc}: bind-mount {src}"


def test_infra_cfg_08_chromium(compose):
    w = compose["services"]["worker-inscritos"]
    m = re.fullmatch(r"(\d+)\s*(m|mb|g|gb)", str(w["shm_size"]).lower())
    assert m, f"shm_size inesperado: {w['shm_size']}"
    mb = int(m.group(1)) * (1024 if m.group(2).startswith("g") else 1)
    assert mb >= 256
    assert any(str(t).split(":")[0] == "/tmp" for t in w["tmpfs"])


def test_infra_cfg_08_sem_porta_de_debug_no_repositorio(repo):
    needle = "--remote-" + "debugging-port"
    targets = [repo / "docker-compose.yml", repo / ".dockerignore"]
    for folder in ("web", "api", "worker_lives", "worker_inscritos"):
        base = repo / folder
        if base.exists():
            targets += [p for p in base.rglob("*") if p.is_file() and p.suffix not in {".woff2", ".woff", ".png", ".ico"}]
    for path in targets:
        text = path.read_text(encoding="utf-8", errors="ignore")
        assert needle not in text, f"{path} contem a porta de debug do Chrome"


def test_infra_cfg_09_healthcheck_e_depends_on(compose):
    for svc, d in compose["services"].items():
        assert d.get("healthcheck", {}).get("test"), f"{svc} sem healthcheck"
    for svc in ("web", "worker-lives", "worker-inscritos"):
        assert compose["services"][svc]["depends_on"]["api"]["condition"] == "service_healthy"


def test_infra_cfg_10_metadados_zimaos(compose, compose_text):
    x = compose["x-casaos"]
    assert x["title"]["en_us"] == "LiveStats"
    assert x["title"]["pt_br"] == "LiveStats"
    assert x["main"] == "web"
    assert x["port_map"] in {"8080", "8081"}
    assert x["icon"].startswith("https://")
    assert "benja" not in compose_text.lower()
    assert not re.search(r"AIza|BEGIN [A-Z ]*PRIVATE KEY", compose_text)


def test_infra_cfg_11_volume_admin_exclusivo(compose):
    assert compose["volumes"]["livestats-admin"]["name"] == "livestats-admin"
    owners = {s for s, d in compose["services"].items() if any("livestats-admin" in src for src, _, _ in mounts_of(d))}
    assert owners == {"api"}
    assert ("livestats-admin", "/admin-state", "rw") in mounts_of(compose["services"]["api"])
    env = env_of(compose["services"]["api"])
    assert env["ADMIN_DB_PATH"].startswith("/admin-state/")
    assert env["DB_PATH"].startswith("/data/")
    for w in WORKERS:
        assert "ADMIN_DB_PATH" not in env_of(compose["services"][w])


def test_infra_cfg_12_confianca_do_proxy_publico(compose):
    value = env_of(compose["services"]["web"])["TRUSTED_PROXY_CIDR"]
    assert value == "${TRUSTED_PROXY_CIDR:-}", "o padrao tem que ser VAZIO"


def test_infra_cfg_13_lista_de_ips_do_admin_e_nenhum_proxy_confiavel(compose, compose_text):
    env = env_of(compose["services"]["web"])
    assert env["ADMIN_ALLOW_CIDRS"] == "${ADMIN_ALLOW_CIDRS:-127.0.0.1/32}", "o padrao tem que ser so o loopback"
    assert "ADMIN_TRUSTED_PROXY_IP" not in compose_text, "D33: a 8081 nao confia em cabecalho de IP"
    assert "${ADMIN_BIND:-127.0.0.1}:8081:8081" in compose["services"]["web"]["ports"]
    for svc, d in compose["services"].items():
        if svc != "web":
            assert "ADMIN_ALLOW_CIDRS" not in env_of(d)


def test_infra_cfg_14_segredos_tls_do_admin_so_no_web(compose, compose_text):
    assert compose["secrets"]["admin_tls_cert"] == {"file": "./secrets/admin_tls_cert.pem"}
    assert compose["secrets"]["admin_tls_key"] == {"file": "./secrets/admin_tls_key.pem"}
    for svc, d in compose["services"].items():
        mounted = set(d.get("secrets") or [])
        assert (svc == "web") == ({"admin_tls_cert", "admin_tls_key"} <= mounted), svc
        assert svc == "web" or not {s for s in mounted if s.startswith("admin_tls")}
    assert not re.search(r"BEGIN [A-Z ]*(PRIVATE KEY|CERTIFICATE)", compose_text)


def test_infra_cfg_15_chaves_dos_cookies_d37(compose):
    """D37 (C14): a publica vai a api e ao worker-inscritos (que so cifra a renovacao); a privada continua so no worker."""
    svcs = compose["services"]
    mounted = {s: set(d.get("secrets") or []) for s, d in svcs.items()}
    assert mounted["worker-inscritos"] >= {"cookie_private_key", "cookie_public_key"}
    assert "cookie_private_key" not in mounted["api"] | mounted["web"] | mounted["worker-lives"]
    assert "cookie_public_key" in mounted["api"]
    assert not {"cookie_private_key", "cookie_public_key"} & mounted["worker-lives"]
    assert not {"cookie_private_key", "cookie_public_key"} & mounted["web"]
    env = env_of(svcs["worker-inscritos"])
    assert env["COOKIE_PUBLIC_KEY_FILE"] == "/run/secrets/cookie_public_key"
    assert env["COOKIE_PRIVATE_KEY_FILE"] == "/run/secrets/cookie_private_key"
    assert "COOKIE_PUBLIC_KEY_FILE" not in env_of(svcs["worker-lives"])
    assert "COOKIE_PRIVATE_KEY_FILE" not in env_of(svcs["api"])


def test_infra_cfg_15_override_e2e_mantem_o_bind_do_admin_do_compose_real(compose, repo):
    """O override de teste troca a lista de portas (`!override`); o host do admin tem que ser o mesmo do compose real,
    senao a SEC-21 testa o arquivo de teste e nao a regra de producao (ADMIN_BIND)."""
    real = [p for p in compose["services"]["web"]["ports"] if p.endswith(":8081")]
    assert real == ["${ADMIN_BIND:-127.0.0.1}:8081:8081"]
    override = (repo / "tests" / "e2e" / "compose.e2e.yml").read_text(encoding="utf-8")
    admin = re.findall(r'(?m)^\s*-\s*"([^"]*):\$\{E2E_ADMIN_PORT:-8081\}:8081"', override)
    assert admin == ["${ADMIN_BIND:-127.0.0.1}"], admin
