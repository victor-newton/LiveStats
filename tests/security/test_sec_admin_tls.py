# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""SEC-26 a SEC-28 (D33): lista de IPs do admin, subida recusada com configuracao perigosa ou sem certificado, TLS na 8081.

Tudo contra a pilha real (compose real + compose.e2e.yml). O par TLS e os segredos sao gerados pela propria suite em
diretorio temporario (tests/e2e/e2e_stack.py) e apagados no fim.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import time

import pytest
from e2e_stack import CONTAINERS, USERNAME, docker, header, header_values, http_request, wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

CSP_STRICT = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
ADMIN_ROUTES = [
    ("GET", "/"), ("GET", "/admin"), ("GET", "/admin-assets/js/main.js"), ("GET", "/api/admin/session"),
    ("GET", "/api/admin/slots"), ("PUT", "/api/admin/slots"), ("POST", "/api/admin/cookies"),
    ("GET", "/api/admin/workers/status"), ("POST", "/api/admin/login"), ("GET", "/live1.json"), ("GET", "/health"),
    ("GET", "/rota-que-nao-existe"),
]


def _recreate_expecting_failure(stack, **env) -> str:
    """Tenta recriar o web com `env`; devolve o log do container (a subida tem que FALHAR)."""
    for key, value in env.items():
        stack.env_extra[key] = value
    r = stack.compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "25", "web", check=False, timeout=120)
    assert r.returncode != 0, f"o web subiu com {env}: {r.stdout}{r.stderr}"
    return stack.logs("web")


def _serves_ok(port: int, tls_cert=None) -> bool:
    try:
        return http_request("127.0.0.1", port, "/health", timeout=3, tls_cert=tls_cert)["status"] == 200
    except (OSError, ssl.SSLError, http.client.HTTPException):
        return False


def _restore_web(stack, allow: str) -> None:
    stack.recreate_web(ADMIN_ALLOW_CIDRS=allow)
    stack.wait_healthy(("web",))


def test_sec_26_lista_de_ips_nega_fora_com_403_json_e_libera_dentro(stack):
    session = stack.session  # sessao valida do host antes de restringir a lista
    original = stack.allow_cidrs
    # .16-.31: inclui o .20 e o .21 de teste; NAO inclui o gateway (.1, por onde o host chega) nem o .36/.40
    stack.recreate_web(ADMIN_ALLOW_CIDRS=f"{stack.base}.16/28")
    try:
        stack.wait_healthy(("web",))
        # fora da lista (o host): 403 JSON em TODA rota da 8081, mesmo com sessao e CSRF validos, sem tocar a api
        for method, path in ADMIN_ROUTES:
            headers = {"Cookie": session.cookie_header, "X-CSRF-Token": session.csrf, "Content-Type": "application/json"}
            r = stack.on_port(8081, path, method, headers, "{}" if method in ("POST", "PUT") else None)
            assert r["status"] == 403, (method, path, r["status"])
            assert json.loads(r["body"]) == {"detail": "Forbidden"}, (method, path)
            assert header(r, "Content-Type").startswith("application/json")
            assert header(r, "Content-Security-Policy") == CSP_STRICT
            assert header(r, "X-Content-Type-Options") == "nosniff"
            assert header(r, "X-Frame-Options") == "DENY"
            assert header(r, "Referrer-Policy") == "no-referrer"
            assert header(r, "Cross-Origin-Opener-Policy") == "same-origin"
            assert header(r, "Access-Control-Allow-Origin") is None
            assert header_values(r, "Set-Cookie") == []
        # cabecalhos de IP forjados nao liberam quem esta fora
        forged = stack.on_port(8081, "/", headers={"X-Forwarded-For": f"{stack.base}.20", "CF-Connecting-IP": f"{stack.base}.20"})
        assert forged["status"] == 403
        # dentro da lista (.21): painel e /api/admin funcionam, e o login de ponta a ponta tambem
        inside = stack.client(21, [
            {"path": "/"}, {"path": "/admin-assets/js/main.js"},
            {"path": "/api/admin/session", "headers": {"Cookie": session.cookie_header}},
            {"path": "/live1.json"},
        ], port=8081)
        assert [r["status"] for r in inside] == [200, 200, 200, 200]
        assert json.loads(inside[2]["body"])["username"] == USERNAME
        # fora da lista (.36): 403 tambem nas rotas publicas da 8081; a 8080 continua para todos
        outside = stack.client(36, [{"path": "/live1.json"}, {"path": "/api/admin/session"}], port=8081)
        assert [r["status"] for r in outside] == [403, 403]
        public = stack.client(36, [{"path": "/live1.json"}, {"path": "/api/admin/session"}], port=8080)
        assert [r["status"] for r in public] == [200, 404]
    finally:
        _restore_web(stack, original)
    back = stack.on_port(8081, "/api/admin/session", headers={"Cookie": session.cookie_header})
    assert back["status"] == 200, back["body"]
    time.sleep(2)


@pytest.mark.parametrize("value", ["0.0.0.0/0", "10.0.0.0/7", "qualquer-coisa", "::/0", "192.168.15.0/24; allow all", "127.0.0.1/32 }"])
def test_sec_27_configuracao_perigosa_da_lista_recusa_a_subida(stack, value):
    original = stack.allow_cidrs
    try:
        log = _recreate_expecting_failure(stack, ADMIN_ALLOW_CIDRS=value)
        assert "15-livestats-realip: ERRO" in log and "ADMIN_ALLOW_CIDRS" in log, log[-600:]
        # nunca cai para uma configuracao aberta: nada responde 200 na 8081
        assert not _serves_ok(8081) and not _serves_ok(8081, stack.cert)
    finally:
        _restore_web(stack, original)


@pytest.mark.parametrize("which", ["admin_tls_cert.pem", "admin_tls_key.pem"])
def test_sec_27_sem_o_secret_tls_o_web_nao_sobe_e_nunca_cai_para_http(stack, which):
    path = stack.secrets_dir / which
    original_bytes = path.read_bytes()
    original = stack.allow_cidrs
    try:
        path.write_bytes(b"")
        log = _recreate_expecting_failure(stack, ADMIN_ALLOW_CIDRS=original)
        assert "15-livestats-realip: ERRO" in log and which.replace(".pem", "") in log, log[-600:]
        assert "admin_tls" in log
        # a 8081 nao responde nem em HTTP simples nem em TLS; a 8080 tambem nao (o web inteiro nao subiu)
        assert not _serves_ok(8081) and not _serves_ok(8081, stack.cert) and not _serves_ok(8080)
    finally:
        path.write_bytes(original_bytes)
        _restore_web(stack, original)


def test_sec_28_login_na_8081_so_por_tls_cookie_secure_e_sem_hsts(stack):
    r = stack.on_port(8081, "/api/admin/login", "POST", {"Content-Type": "application/json"},
                      json.dumps({"username": USERNAME, "password": stack.password}))
    while r["status"] == 429:  # bloqueio de login do IP do host por testes anteriores
        time.sleep(float(header(r, "Retry-After") or 5) + 1)
        r = stack.on_port(8081, "/api/admin/login", "POST", {"Content-Type": "application/json"},
                          json.dumps({"username": USERNAME, "password": stack.password}))
    assert r["status"] == 200, r["body"]
    cookie = [v for v in header_values(r, "Set-Cookie") if v.startswith("__Host-livestats_session=")]
    assert len(cookie) == 1
    assert "; Secure" in cookie[0] and "; HttpOnly" in cookie[0] and "SameSite=Strict" in cookie[0] and "Path=/" in cookie[0]
    assert "Domain" not in cookie[0]
    assert header(r, "Strict-Transport-Security") is None
    # HTTP simples na 8081: nunca um login (400 JSON do Nginx, sem Set-Cookie)
    plain = http_request("127.0.0.1", 8081, "/api/admin/login", "POST", {"Content-Type": "application/json"},
                         json.dumps({"username": USERNAME, "password": stack.password}))
    assert plain["status"] == 400 and header_values(plain, "Set-Cookie") == []
    assert json.loads(plain["body"]) == {"detail": "Bad Request"}
    # TLS 1.2 e 1.3 aceitos; os certificados verificados contra o par de teste
    for version in (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3):
        ctx = ssl.create_default_context(cadata=stack.cert.read_text(encoding="ascii"))
        ctx.minimum_version = ctx.maximum_version = version
        with socket.create_connection(("127.0.0.1", 8081), timeout=10) as raw, ctx.wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            assert tls.version() == {ssl.TLSVersion.TLSv1_2: "TLSv1.2", ssl.TLSVersion.TLSv1_3: "TLSv1.3"}[version]
    # a chave privada nao existe em lugar nenhum da imagem do web nem em variavel de ambiente
    env = docker("inspect", CONTAINERS["web"], "--format", "{{json .Config.Env}}").stdout
    assert "PRIVATE KEY" not in env and "admin_tls" not in env
    wait_until(lambda: stack.on_port(8081, "/api/admin/session")["status"] == 401, timeout=15)
