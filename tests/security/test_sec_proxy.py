# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""SEC-05 (limit_req por IP real na 8080), SEC-24 (IP real na 8081 so do NPM), SEC-25 (admin via HTTPS de um proxy de teste)."""

from __future__ import annotations

import datetime
import time
from pathlib import Path

import pytest
from e2e_browser import chromium_context
from e2e_stack import USERNAME, docker, http_request
from sec_support import JSON_HEADERS, login_body

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

HEALTH = "/health"


def _burst(ip_header: str | None, n: int = 70, header_name: str = "CF-Connecting-IP") -> dict:
    headers = {header_name: ip_header} if ip_header else {}
    return {"path": HEALTH, "headers": headers, "repeat": n}


def _codes(out: list[dict]) -> list[int]:
    return [r["status"] for r in out]


def _cf_scenario(stack, octet: int, expect_trusted: bool) -> None:
    out = stack.client(octet, [_burst("198.51.100.1"), {"path": HEALTH, "headers": {"CF-Connecting-IP": "198.51.100.2"}, "repeat": 3}], port=8080)
    first, second = _codes(out[:70]), _codes(out[70:])
    assert 429 in first, "o limite nao agiu na rajada"
    if expect_trusted:
        assert second == [200] * 3, f"IP real diferente deveria ter limite proprio: {second}"
    else:
        assert second == [429] * 3, f"cabecalho de origem nao confiavel deveria ser ignorado: {second}"


def test_sec_05_limit_req_conta_por_ip_real_e_so_confia_no_32_do_cloudflared(stack):
    time.sleep(4)
    # .20 e o "cloudflared" do teste (TRUSTED_PROXY_CIDR = x.x.x.20/32): o cabecalho vale, limites separados
    _cf_scenario(stack, 20, expect_trusted=True)
    # outro container na edge (IP diferente): cabecalho ignorado => os dois valores compartilham o limite do IP da conexao
    _cf_scenario(stack, 40, expect_trusted=False)
    # o host pela porta publicada aparece com o IP do gateway: cabecalho ignorado
    time.sleep(4)
    for _ in range(70):
        http_request("127.0.0.1", 8080, HEALTH, headers={"CF-Connecting-IP": "198.51.100.1"})
    other = http_request("127.0.0.1", 8080, HEALTH, headers={"CF-Connecting-IP": "198.51.100.2"})
    assert other["status"] == 429, "o gateway nao pode ser confiavel"
    time.sleep(5)

    # sem TRUSTED_PROXY_CIDR (padrao vazio) o cabecalho e ignorado ate para o .20
    stack.recreate_web(TRUSTED_PROXY_CIDR="")
    try:
        time.sleep(3)
        _cf_scenario(stack, 20, expect_trusted=False)
    finally:
        stack.recreate_web(TRUSTED_PROXY_CIDR=f"{stack.cloudflared_ip}/32")
    time.sleep(4)


def _login_attempts(n: int, xff: list[str] | None = None, user: str = "sec24-x") -> list[dict]:
    reqs = []
    for i in range(n):
        headers = dict(JSON_HEADERS)
        if xff:
            headers["X-Forwarded-For"] = xff[i % len(xff)]
        reqs.append({"path": "/api/admin/login", "method": "POST", "headers": headers, "body": login_body(user, f"errada-{i}-xxxxxxxx")})
    return reqs


def test_sec_24_ip_real_na_8081_so_vem_do_npm(stack):
    # NPM (.21): o ULTIMO salto do X-Forwarded-For e o IP real
    xff = [f"203.0.113.{i}, 198.51.100.9" for i in range(6)]
    codes = _codes(stack.client(21, _login_attempts(6, xff, "sec24-a"), port=8081))
    assert codes == [401] * 5 + [429], codes
    # outro ultimo salto => outro limite (nao esta bloqueado)
    other = _codes(stack.client(21, _login_attempts(1, ["203.0.113.7, 198.51.100.10"], "sec24-b"), port=8081))
    assert other == [401], other
    # o PRIMEIRO salto do IP bloqueado nao conta: so o ultimo
    first_hop = _codes(stack.client(21, _login_attempts(1, ["198.51.100.9, 198.51.100.77"], "sec24-c"), port=8081))
    assert first_hop == [401], first_hop

    # qualquer outra origem na edge: XFF ignorado => variar o cabecalho nao escapa do limite
    varied = [f"198.51.100.{50 + i}" for i in range(7)]
    codes = _codes(stack.client(36, _login_attempts(7, varied, "sec24-d"), port=8081))
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429], codes
    # o host (gateway da porta publicada) tambem
    codes = []
    for i, ip in enumerate(varied[:6]):
        r = http_request("127.0.0.1", 8081, "/api/admin/login", "POST", {**JSON_HEADERS, "X-Forwarded-For": ip}, login_body("sec24-e", f"errada-{i}-xxxxxxxx"))
        codes.append(r["status"])
    assert codes == [401] * 5 + [429], codes

    # sem ADMIN_TRUSTED_PROXY_IP (padrao vazio) ate o NPM e ignorado
    stack.recreate_web(ADMIN_TRUSTED_PROXY_IP="")
    try:
        time.sleep(3)
        codes = _codes(stack.client(21, _login_attempts(6, [f"198.51.100.{100 + i}" for i in range(6)], "sec24-f"), port=8081))
        assert codes == [401] * 5 + [429], codes
    finally:
        stack.recreate_web(ADMIN_TRUSTED_PROXY_IP=stack.npm_ip)


def _make_cert(directory: Path) -> tuple[Path, Path]:
    import ipaddress

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    return cert_path, key_path


def test_sec_25_admin_via_https_do_proxy_mantem_cookie_secure(stack, tmp_path):
    stack.login()  # espera expirar o bloqueio de login do IP do host (SEC-24): o proxy chega a api com esse IP
    cert, key = _make_cert(tmp_path)
    proxy_src = Path(__file__).resolve().parent / "tls_proxy.py"
    name = "t8-tls-proxy"
    docker("rm", "-f", name, check=False)
    docker(
        "run", "-d", "--name", name, "--label", "livestats-t8-extra=1", "--network", "livestats-t8-edge",
        "--ip", stack.npm_ip, "-p", "127.0.0.1:18443:8443",
        "-v", f"{proxy_src}:/srv/tls_proxy.py:ro", "-v", f"{tmp_path}:/certs:ro",
        "python:3.12-slim", "python", "/srv/tls_proxy.py", "8443", "web", "8081", "/certs/cert.pem", "/certs/key.pem",
    )
    try:
        time.sleep(3)
        with chromium_context(ignore_https_errors=True) as ctx:
            page = ctx.new_page()
            violations: list[str] = []
            page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
            page.goto("https://127.0.0.1:18443/")
            page.locator("#loginUser").fill(USERNAME)
            page.locator("#loginPass").fill(stack.password)
            page.locator("#loginBtn").click()
            page.locator("#panelView").wait_for(state="visible", timeout=30000)  # o painel carrega e o login funciona
            cookies = [c for c in ctx.cookies() if c["name"] == "__Host-livestats_session"]
            assert len(cookies) == 1
            c = cookies[0]
            assert c["secure"] is True and c["httpOnly"] is True and c["sameSite"] == "Strict" and c["path"] == "/"
            assert violations == []
            assert page.evaluate("document.cookie").find("__Host-livestats_session") == -1  # HttpOnly
    finally:
        docker("rm", "-f", name, check=False)
