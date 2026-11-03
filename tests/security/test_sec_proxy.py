# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""SEC-05 (limit_req por IP real na 8080), SEC-24 (a 8081 ignora SEMPRE cabecalhos de IP, D33), SEC-25 (admin em HTTPS proprio, cookie Secure)."""

from __future__ import annotations

import time

import pytest
from e2e_browser import chromium_context
from e2e_stack import USERNAME, http_request
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


def _login_attempts(n: int, xff: list[str] | None = None, user: str = "sec24-x", cf: list[str] | None = None) -> list[dict]:
    reqs = []
    for i in range(n):
        headers = dict(JSON_HEADERS)
        if xff:
            headers["X-Forwarded-For"] = xff[i % len(xff)]
        if cf:
            headers["CF-Connecting-IP"] = cf[i % len(cf)]
        reqs.append({"path": "/api/admin/login", "method": "POST", "headers": headers, "body": login_body(user, f"errada-{i}-xxxxxxxx")})
    return reqs


def test_sec_24_8081_ignora_sempre_xff_e_cf_connecting_ip(stack):
    """D33: o IP de conexao e o real. Nenhuma origem (nem a que e confiavel na 8080) faz cabecalho valer na 8081."""
    xff = [f"203.0.113.{i}, 198.51.100.9" for i in range(6)]
    cf = [f"198.51.100.{60 + i}" for i in range(6)]
    # origem na lista (.21), cabecalhos diferentes a cada tentativa: o limite e UM so (do IP de conexao)
    codes = _codes(stack.client(21, _login_attempts(6, xff, "sec24-a", cf), port=8081))
    assert codes == [401] * 5 + [429], codes
    # outra origem declarando o ultimo salto e o IP bloqueado como se fosse ele: tem o limite PROPRIO (nao herda o bloqueio)
    other = _codes(stack.client(22, _login_attempts(1, ["203.0.113.7, 198.51.100.9", ], "sec24-b", [stack.base + ".21"]), port=8081))
    assert other == [401], other
    # o IP que e o cloudflared na 8080 (.20) tambem nao tem poder algum na 8081
    varied = [f"198.51.100.{100 + i}" for i in range(7)]
    codes = _codes(stack.client(20, _login_attempts(7, varied, "sec24-d", varied), port=8081))
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429], codes
    # o host (gateway da porta publicada) tambem: variar o cabecalho nao escapa
    codes = []
    for i, ip in enumerate(varied[:6]):
        r = http_request(
            "127.0.0.1", 8081, "/api/admin/login", "POST",
            {**JSON_HEADERS, "X-Forwarded-For": ip, "CF-Connecting-IP": ip}, login_body("sec24-e", f"errada-{i}-xxxxxxxx"), tls_cert=stack.cert,
        )
        codes.append(r["status"])
    assert codes == [401] * 5 + [429], codes
    # e o que a api registra como origem e o IP da conexao, nunca o do cabecalho
    stack.client(23, [{"path": "/live1.json?sec24=1", "headers": {"X-Forwarded-For": "203.0.113.200", "CF-Connecting-IP": "203.0.113.201"}}], port=8081)
    log = stack.logs("web")
    assert f"{stack.base}.23 " in log and "203.0.113.200" not in log and "203.0.113.201" not in log
    time.sleep(5)


def test_sec_25_admin_em_https_proprio_cookie_secure_aceito_pelo_navegador(stack):
    stack.login()  # espera expirar o bloqueio de login do IP do host (SEC-24)
    url = "https://127.0.0.1:8081/"
    # sem confiar no certificado de teste o navegador RECUSA (e autoassinado): prova que ha TLS de verdade
    with chromium_context() as ctx:
        page = ctx.new_page()
        with pytest.raises(Exception, match="ERR_CERT"):
            page.goto(url)
    # `ignore_https_errors` so aqui, no teste: o cookie `__Host-...; Secure` precisa ser aceito e o login funcionar
    with chromium_context(ignore_https_errors=True) as ctx:
        page = ctx.new_page()
        violations: list[str] = []
        page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
        page.goto(url)
        page.locator("#loginUser").fill(USERNAME)
        page.locator("#loginPass").fill(stack.password)
        page.locator("#loginBtn").click()
        page.locator("#panelView").wait_for(state="visible", timeout=30000)  # o painel carrega e o login funciona
        cookies = [c for c in ctx.cookies() if c["name"] == "__Host-livestats_session"]
        assert len(cookies) == 1
        c = cookies[0]
        assert c["secure"] is True and c["httpOnly"] is True and c["sameSite"] == "Strict" and c["path"] == "/"
        assert c["domain"] == "127.0.0.1"
        assert violations == []
        assert page.evaluate("document.cookie").find("__Host-livestats_session") == -1  # HttpOnly
        # uma chamada autenticada do painel passa pela mesma origem HTTPS (caminhos relativos funcionam)
        status = page.evaluate("fetch('/api/admin/session', {credentials: 'same-origin'}).then(r => r.status)")
        assert status == 200
