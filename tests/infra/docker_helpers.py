"""Ajudantes para os testes `docker` de tests/infra.

Sobe o servico `web` DO compose real (endurecimento, tmpfs, healthcheck, portas) com a imagem construida do
`web/Dockerfile` real, contra um upstream de teste que responde em api:8000 (tests/infra/fixtures/fake_api.py).
Nada daqui entra em imagem de producao.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import random
import re
import shutil
import subprocess
import tempfile
import textwrap
import time
from pathlib import Path

import pytest
import yaml
from tls_support import client_context, make_tls_pair

REPO = Path(__file__).resolve().parents[2]
FAKE_API = Path(__file__).resolve().parent / "fixtures" / "fake_api.py"
STATIC_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "static"
PYTHON_IMAGE = "python:3.12-slim"


def docker(*args, check=True, timeout=300, input_text=None):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, input=input_text, encoding="utf-8"
    )
    if check and result.returncode != 0:
        raise AssertionError(f"docker {' '.join(args)} falhou ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    return result


# GPG_KEY vem da imagem oficial python:3.12-slim: e a impressao digital PUBLICA da chave de assinatura do
# Python, nao um segredo nosso. Excecao por NOME exato; qualquer outra variavel continua varrida.
INHERITED_PUBLIC_ENV = frozenset({"GPG_KEY"})


def sensitive_env_vars(env):
    """Entradas 'NOME=valor' cujo nome termina em KEY/PASSWORD/TOKEN/SECRET/HASH (exceto INHERITED_PUBLIC_ENV)."""
    return [
        e for e in env
        if e.split("=", 1)[0] not in INHERITED_PUBLIC_ENV
        and re.search(r"(KEY|PASSWORD|TOKEN|SECRET|HASH)=", e)
    ]


def require_docker():
    """Testes `docker` FALHAM (nunca sao pulados) se nao houver Docker."""
    try:
        r = subprocess.run(["docker", "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        pytest.fail(f"Docker indisponivel: {exc}")
    if r.returncode != 0:
        pytest.fail(f"Docker indisponivel: {r.stderr.strip()}")


def build_web_image(tag: str) -> str:
    """Constroi o web/Dockerfile REAL com o nginx real e o front de fixture (o front de verdade e da T6)."""
    ctx = Path(tempfile.mkdtemp(prefix="t7web-"))
    try:
        (ctx / "web").mkdir()
        shutil.copy(REPO / "web" / "Dockerfile", ctx / "web" / "Dockerfile")
        shutil.copytree(REPO / "web" / "nginx", ctx / "web" / "nginx")
        shutil.copytree(STATIC_FIXTURE, ctx / "web" / "static")
        shutil.copy(REPO / ".dockerignore", ctx / ".dockerignore")
        docker("build", "-q", "-f", str(ctx / "web" / "Dockerfile"), "-t", tag, str(ctx), timeout=600)
    finally:
        shutil.rmtree(ctx, ignore_errors=True)
    return tag


class Stack:
    """web (servico real do compose) + api falsa, em redes proprias do teste."""

    def __init__(self, image: str, trusted_octet: int | None = None, allow: str | None = None):
        self.image = image
        self.project = f"livestats-t7test-{random.randrange(10**6):06d}"
        third = random.randrange(100, 250)
        self.subnet = f"172.30.{third}.0/24"
        self.base = f"172.30.{third}"
        self.trusted = f"{self.base}.{trusted_octet}/32" if trusted_octet else ""
        # lista de permissao da 8081 (D33). Padrao do teste: a sub-rede edge inteira (clientes de teste e o gateway
        # pelo qual o host chega); os testes da propria lista passam `allow`.
        self.allow = (allow or "{base}.0/24").format(base=self.base)
        self.dir = Path(tempfile.mkdtemp(prefix="t7stack-"))
        # par TLS de teste (D33), gerado aqui e apagado em down()
        self.cert, self.key = make_tls_pair(self.dir / "tls", ips=("127.0.0.1",))
        self.file = self.dir / "compose.yml"
        self.env = {
            **os.environ,
            "TRUSTED_PROXY_CIDR": self.trusted,
            "ADMIN_ALLOW_CIDRS": self.allow,
            "ADMIN_BIND": "127.0.0.1",
            "COMPOSE_PROJECT_NAME": self.project,
        }

    # ------------------------------------------------------------------ ciclo de vida
    def _write_compose(self):
        real = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
        web = copy.deepcopy(real["services"]["web"])
        web.pop("build")
        web.pop("container_name")
        web["image"] = self.image
        web["ports"] = ["127.0.0.1::8080", "127.0.0.1::8081"]
        api = {
            "image": PYTHON_IMAGE,
            "command": ["python", "/srv/fake_api.py"],
            "volumes": [{"type": "bind", "source": str(FAKE_API), "target": "/srv/fake_api.py", "read_only": True}],
            "networks": ["internal"],
            "healthcheck": {
                "test": ["CMD", "python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health')"],
                "interval": "2s",
                "timeout": "3s",
                "retries": 15,
            },
        }
        doc = {
            "secrets": {
                "admin_tls_cert": {"file": str(self.cert)},
                "admin_tls_key": {"file": str(self.key)},
            },
            "services": {"web": web, "api": api},
            "networks": {
                "internal": {"name": f"{self.project}-internal", "internal": True},
                "edge": {
                    "name": f"{self.project}-edge",
                    "driver_opts": real["networks"]["edge"]["driver_opts"],
                    "ipam": {"config": [{"subnet": self.subnet}]},
                },
            },
        }
        self.file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def compose(self, *args, check=True, timeout=300, input_text=None):
        r = subprocess.run(
            ["docker", "compose", "-f", str(self.file), *args],
            capture_output=True, text=True, timeout=timeout, env=self.env, encoding="utf-8", input=input_text,
        )
        if check and r.returncode != 0:
            raise AssertionError(f"compose {' '.join(args)} falhou:\n{r.stdout}\n{r.stderr}")
        return r

    def up(self):
        self._write_compose()
        self.compose("up", "-d", "--wait", "--wait-timeout", "120", timeout=240)
        self.port_public = self._port(8080)
        self.port_admin = self._port(8081)
        self.edge = f"{self.project}-edge"
        return self

    def _port(self, container_port: int) -> int:
        out = self.compose("port", "web", str(container_port)).stdout.strip().splitlines()[0]
        return int(out.rsplit(":", 1)[1])

    def down(self):
        self.compose("down", "-v", "--remove-orphans", check=False)
        shutil.rmtree(self.dir, ignore_errors=True)

    # ------------------------------------------------------------------ requisicoes
    def host_request(self, port, path, method="GET", headers=None, body=None):
        """Do host. A 8081 e HTTPS (certificado de teste verificado); a 8080 e HTTP."""
        tls = self.cert if port == self.port_admin else None
        return http_request("127.0.0.1", port, path, method, headers, body, tls_cert=tls)

    def client(self, ip: str, requests: list[dict], port: int = 8080) -> list[dict]:
        """Executa requisicoes de um container com IP FIXO na rede edge (origem controlada)."""
        spec = {"host": "web", "port": port, "requests": requests}
        if port == 8081:  # HTTPS: confia so no certificado de teste (o nome "web" nao esta no SAN)
            spec["tls_ca"] = self.cert.read_text(encoding="ascii")
        payload = json.dumps(spec)
        r = docker(
            "run", "--rm", "--network", self.edge, "--ip", f"{self.base}.{ip}", PYTHON_IMAGE,
            "python", "-c", CLIENT_CODE, payload,
        )
        return json.loads(r.stdout)

    def api_log(self) -> list[dict]:
        out = self.compose(
            "exec", "-T", "api", "python", "-c",
            "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/__requests').read().decode())",
        ).stdout
        return json.loads(out)

    def logs(self, service="web") -> str:
        return self.compose("logs", "--no-color", service).stdout


CLIENT_CODE = textwrap.dedent(
    """
    import http.client, json, ssl, sys
    spec = json.loads(sys.argv[1])
    out = []
    ctx = None
    shared = None
    if spec.get("tls_ca"):
        ctx = ssl.create_default_context(cadata=spec["tls_ca"])
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False
    for req in spec["requests"]:
        for _ in range(req.get("repeat", 1)):
            if ctx is not None:
                # TLS: uma conexao so (keep-alive) por chamada, como um navegador; o handshake de cada requisicao
                # mudaria o ritmo que os testes de limit_req medem. O http.client reabre sozinho se o servidor fechar.
                shared = shared or http.client.HTTPSConnection(spec["host"], spec["port"], timeout=10, context=ctx)
                c = shared
            else:
                c = http.client.HTTPConnection(spec["host"], spec["port"], timeout=10)
            body = req.get("body")
            if req.get("body_bytes"):
                body = b"x" * req["body_bytes"]
            elif body is not None:
                body = body.encode()
            c.request(req.get("method", "GET"), req["path"], body=body, headers=req.get("headers", {}))
            r = c.getresponse()
            data = r.read()
            out.append({"status": r.status, "headers": [list(h) for h in r.getheaders()], "body": data.decode("utf-8", "replace")[:600]})
            if ctx is None:
                c.close()
    if shared is not None:
        shared.close()
    print(json.dumps(out))
    """
)


def http_request(host, port, path, method="GET", headers=None, body=None, timeout=15, tls_cert=None):
    """HTTP simples; com `tls_cert` (caminho do certificado de teste) fala HTTPS verificando a cadeia e o nome."""
    if tls_cert is not None:
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=client_context(tls_cert))
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    if isinstance(body, str):
        body = body.encode()
    conn.request(method, path, body=body, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    result = {
        "status": resp.status,
        "headers": [list(h) for h in resp.getheaders()],
        "body": data.decode("utf-8", "replace"),
    }
    conn.close()
    return result


def header_values(resp: dict, name: str) -> list[str]:
    return [v for k, v in resp["headers"] if k.lower() == name.lower()]


def header(resp: dict, name: str):
    values = header_values(resp, name)
    assert len(values) <= 1, f"cabecalho duplicado {name}: {values}"
    return values[0] if values else None


def wait_until(predicate, timeout=40, interval=1.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (AssertionError, OSError, ValueError) as exc:  # so repete; ao esgotar o prazo a falha e levantada
            last = exc
        time.sleep(interval)
    raise AssertionError(f"condicao nao satisfeita em {timeout}s (ultimo: {last!r})")
