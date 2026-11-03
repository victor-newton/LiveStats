# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""SEC-01, SEC-02, SEC-04, SEC-06, SEC-16, SEC-17: o que a pilha REAL (Nginx + api) responde por HTTP."""

from __future__ import annotations

import json

import pytest
from e2e_stack import USERNAME, header, header_values
from sec_support import (
    JSON_HEADERS,
    ORIGIN,
    admin_flags,
    cors_headers,
    first_with_status,
    flood,
    login_body,
)

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

NO_SESSION_ROUTES = [
    ("GET", "/api/admin/session"),
    ("GET", "/api/admin/slots"),
    ("PUT", "/api/admin/slots"),
    ("POST", "/api/admin/cookies"),
    ("GET", "/api/admin/workers/status"),
    ("POST", "/api/admin/logout"),
    ("GET", "/api/admin/rota-que-nao-existe"),
    ("DELETE", "/api/admin/slots"),
    ("PATCH", "/api/admin/slots"),
    ("POST", "/api/admin/rota-que-nao-existe"),
    ("GET", "/api/admin/"),
]


def _is_json_without_traceback(resp) -> dict:
    assert header(resp, "Content-Type").startswith("application/json"), resp["headers"]
    assert "Traceback" not in resp["body"] and "<html" not in resp["body"].lower()
    return json.loads(resp["body"])


def test_sec_01_toda_rota_admin_sem_sessao_401_e_sem_csrf_403_pela_8081(stack):
    # --- sem cookie e com cookie de sessao inventado: 401 em TODAS as rotas e metodos (inclusive inexistentes)
    for cookie in (None, "__Host-livestats_session=invalida-" + "x" * 40):
        headers = dict(JSON_HEADERS)
        if cookie:
            headers["Cookie"] = cookie
        for method, path in NO_SESSION_ROUTES:
            body = "{}" if method in ("PUT", "POST", "PATCH") else None
            r = stack.admin(path, method, headers, body)
            assert r["status"] == 401, (method, path, cookie, r["status"], r["body"][:200])
            _is_json_without_traceback(r)

    # variantes de caminho que nao podem dar acesso: so 401 (a api decide) ou 404 (o Nginx nao conhece)
    for path in ("//api/admin/slots", "/api/admin/./slots", "/api/admin/slots/", "/api/admin/%73lots", "/API/ADMIN/slots", "/api/admin/../admin/slots"):
        r = stack.admin(path)
        assert r["status"] in (401, 404), (path, r["status"])
        assert '"slots"' not in r["body"]

    # --- com sessao valida e SEM o token CSRF: leitura passa; metodos que alteram estado => 403
    own = stack.login()  # sessao propria: nao interfere nas demais
    h = {"Cookie": own.cookie_header, **JSON_HEADERS}
    for path in ("/api/admin/session", "/api/admin/slots", "/api/admin/workers/status"):
        assert stack.admin(path, "GET", h)["status"] == 200, path
    unsafe = [
        ("PUT", "/api/admin/slots", json.dumps({"slots": [{"url": "", "goal": None, "color": "#FF0033"}] * 4})),
        ("POST", "/api/admin/cookies", json.dumps([{"name": "a", "value": "b"}])),
        ("POST", "/api/admin/logout", "{}"),
    ]
    slots_before = stack.api("GET", "/api/admin/slots")["body"]
    for method, path, body in unsafe:
        r = stack.admin(path, method, h, body)
        assert r["status"] == 403, (method, path, r["status"], r["body"][:200])
        _is_json_without_traceback(r)
        wrong = stack.admin(path, method, {**h, "X-CSRF-Token": "x" * 43}, body)
        assert wrong["status"] == 403, (method, path, "csrf errado", wrong["status"])
        # token CSRF de OUTRA sessao tambem nao vale
        other = stack.admin(path, method, {**h, "X-CSRF-Token": stack.session.csrf}, body)
        assert other["status"] == 403, (method, path, "csrf de outra sessao", other["status"])
    assert stack.api("GET", "/api/admin/slots")["body"] == slots_before, "um 403 nao pode ter alterado os slots"
    assert stack.admin("/api/admin/session", "GET", h)["status"] == 200, "logout sem CSRF nao pode encerrar a sessao"

    # --- com sessao e CSRF corretos as mesmas rotas funcionam (controle: o 401/403 acima vem da protecao, nao de rota quebrada)
    ok = {**h, "X-CSRF-Token": own.csrf}
    assert stack.admin("/api/admin/slots", "PUT", ok, json.dumps({"slots": [{"url": "", "goal": None, "color": "#FF0033"}] * 4}))["status"] == 200
    assert stack.admin("/api/admin/cookies", "POST", ok, "[]")["status"] == 400  # passou pela auth e caiu na validacao
    assert stack.admin("/api/admin/rota-que-nao-existe", "GET", ok)["status"] == 404  # com sessao: 404 JSON
    stack.set_slots(stack.default_slots())
    assert stack.admin("/api/admin/logout", "POST", ok, "{}")["status"] == 200
    assert stack.admin("/api/admin/session", "GET", h)["status"] == 401, "sessao encerrada precisa deixar de valer"


def test_sec_02_sexta_tentativa_errada_de_login_em_1_minuto_e_429(stack):
    attempts = [
        {"path": "/api/admin/login", "method": "POST", "headers": JSON_HEADERS,
         "body": login_body("sec02-inexistente", f"errada-{i}-xxxxxxxx")}
        for i in range(7)
    ]
    out = stack.client(30, attempts, port=8081)
    codes = [r["status"] for r in out]
    assert codes[:5] == [401] * 5, codes
    assert codes[5] == 429, f"a 6a tentativa precisa ser 429, veio {codes}"  # dentro do burst do Nginx => 429 da api
    assert codes[6] == 429
    limited = out[5]
    assert json.loads(limited["body"]) == {"detail": "Muitas tentativas. Tente novamente em instantes."}
    assert header(limited, "Retry-After")
    assert "login bloqueado" in stack.logs("api"), "o 429 da 6a tentativa tem que ter vindo do contador da api"

    # mesmo com a senha CERTA a tentativa seguinte a 5 falhas do usuario e bloqueada (D5)
    wrong = [
        {"path": "/api/admin/login", "method": "POST", "headers": JSON_HEADERS, "body": login_body(USERNAME, f"errada-{i}-xxxxxxxx")}
        for i in range(5)
    ]
    right = {"path": "/api/admin/login", "method": "POST", "headers": JSON_HEADERS, "body": login_body(USERNAME, stack.password)}
    out = stack.client(31, [*wrong, right], port=8081)
    assert [r["status"] for r in out] == [401] * 5 + [429], [r["status"] for r in out]
    assert "set-cookie" not in {k.lower() for k, _ in out[5]["headers"]}


def test_sec_04_porta_publica_nao_tem_nenhuma_rota_de_admin(stack):
    paths = [
        "/", "/admin", "/admin/", "/admin-assets/index.html", "/api/admin/session", "/api/admin/slots",
        "/api/admin/cookies", "/api/admin/workers/status", "/api/admin/login", "/api/admin/logout",
        "/api/admin", "/api/admin/", "/api/admin/qualquer-coisa",
    ]
    for path in paths:
        for method in ("GET", "POST"):
            body = "{}" if method == "POST" else None
            r = stack.public(path, method, JSON_HEADERS, body)
            assert r["status"] == 404, (method, path, r["status"], r["body"][:120])
            assert "csrf" not in r["body"].lower() and "<html" not in r["body"].lower()
    # controle: as MESMAS rotas existem na 8081 (o 404 nao e a pilha toda quebrada)
    assert stack.admin("/admin/")["status"] == 200
    assert stack.admin("/")["status"] == 200 and stack.admin("/admin")["status"] == 200
    assert stack.admin("/api/admin/session")["status"] == 401
    # e a 8080 serve o que e publico
    assert stack.public("/health")["status"] == 200 and stack.public("/contador")["status"] == 200


def _assert_security_headers(resp, where: str, overlay: bool = False) -> None:
    csp = header(resp, "Content-Security-Policy")
    assert csp, f"{where}: sem CSP"
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp, f"{where}: {csp}"
    assert "frame-ancestors" in csp, f"{where}: {csp}"
    assert ("frame-ancestors *" in csp) == overlay, f"{where}: {csp}"
    assert header(resp, "X-Content-Type-Options") == "nosniff", where
    assert header(resp, "Referrer-Policy"), where


def test_sec_06_cabecalhos_de_seguranca_em_200_404_e_429_das_duas_portas(stack):
    stack.ensure_subs("460600")
    floods = flood(stack)
    for port, label in ((8080, "8080"), (8081, "8081")):
        ok = stack.on_port(port, "/health")
        assert ok["status"] == 200
        _assert_security_headers(ok, f"{label} 200 /health")
        not_found = stack.on_port(port, "/nao-existe-sec06")
        assert not_found["status"] == 404
        _assert_security_headers(not_found, f"{label} 404")
        data = stack.on_port(port, "/inscritos.json")
        assert data["status"] == 200
        _assert_security_headers(data, f"{label} 200 /inscritos.json")
        overlay = stack.on_port(port, "/contador")
        assert overlay["status"] == 200
        _assert_security_headers(overlay, f"{label} /contador", overlay=True)
    panel = stack.admin("/")
    assert panel["status"] == 200
    _assert_security_headers(panel, "8081 / (painel)")

    # 429 de verdade, produzido por rajadas (Nginx) nas duas portas e na zona de admin
    _assert_security_headers(first_with_status(floods["public_8080"], 429), "8080 429")
    _assert_security_headers(first_with_status(floods["public_8081"], 429), "8081 429")
    _assert_security_headers(first_with_status(floods["admin_8081"], 429), "8081 429 /api/admin")
    # 429 do login (vem da api, atravessando o Nginx)
    r = stack.client(
        35,
        [{"path": "/api/admin/login", "method": "POST", "headers": JSON_HEADERS, "body": login_body("sec06-x", "errada-xxxxxxxx"), "repeat": 6}],
        port=8081,
    )
    assert r[-1]["status"] == 429
    _assert_security_headers(r[-1], "8081 429 login")


PUBLIC_DATA = ["/live1.json", "/live2.json", "/live3.json", "/live4.json", "/inscritos.json", "/api/public/lives/1", "/api/public/subscribers"]


def test_sec_16_cors_aberto_so_nos_get_publicos_de_dados(stack):
    stack.ensure_lives()
    stack.ensure_subs("460600")
    origin = {"Origin": ORIGIN}
    for port in (8080, 8081):
        for path in PUBLIC_DATA:
            r = stack.on_port(port, path, headers=origin)
            assert r["status"] == 200, (port, path)
            assert header(r, "Access-Control-Allow-Origin") == "*", (port, path)
            assert header(r, "Access-Control-Allow-Methods") == "GET", (port, path)
            assert header(r, "Access-Control-Allow-Credentials") is None
            pre = stack.on_port(
                port, path, "OPTIONS", {**origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "content-type"}
            )
            assert pre["status"] == 204, (port, path, pre["status"])
            assert header(pre, "Access-Control-Allow-Origin") == "*" and header(pre, "Access-Control-Allow-Methods") == "GET"
            assert header(pre, "Access-Control-Allow-Credentials") is None
        # tambem nas respostas de erro (404 de slot invalido)
        for path in ("/api/public/lives/5", "/live5.json"):
            r = stack.on_port(port, path, headers=origin)
            assert r["status"] == 404, (port, path, r["status"])
            assert header(r, "Access-Control-Allow-Origin") == "*", (port, path)
            assert header(r, "Access-Control-Allow-Methods") == "GET", (port, path)
        # 503 (dados ainda nao disponiveis): resposta capturada logo apos a subida do zero, ja com Origin
        for path in ("/inscritos.json", "/api/public/subscribers"):
            r = stack.fresh[(port, path)]
            assert r["status"] == 503, (port, path, r["status"])
            assert header(r, "Access-Control-Allow-Origin") == "*" and header(r, "Access-Control-Allow-Methods") == "GET"

    # demais rotas: NENHUM Access-Control-Allow-*, mesmo com Origin e em preflight
    for port in (8080, 8081):
        others = ["/health", "/contador", "/overlay/inscritos", "/", "/admin", "/api/admin/session", "/api/admin/slots", "/api/admin/workers/status"]
        for path in others:
            for method in ("GET", "OPTIONS"):
                r = stack.on_port(port, path, method, {**origin, "Access-Control-Request-Method": "GET"})
                assert cors_headers(r) == [], (port, method, path, cors_headers(r))
        login = stack.on_port(port, "/api/admin/login", "POST", {**origin, **JSON_HEADERS}, "{}")
        assert cors_headers(login) == [], (port, "login", cors_headers(login))
        # com sessao tambem (200 do admin)
        r = stack.api("GET", "/api/admin/session", headers=origin, port=port)
        if port == 8081:
            assert r["status"] == 200 and cors_headers(r) == []


def test_sec_17_cookie_de_sessao_pelo_nginx_tem_todos_os_atributos(stack):
    r = stack.admin(
        "/api/admin/login", "POST", JSON_HEADERS, login_body(USERNAME, stack.password)
    )
    if r["status"] == 429:  # testes anteriores esgotaram o limite do IP do host; espera a janela
        stack.login()
        r = stack.admin("/api/admin/login", "POST", JSON_HEADERS, login_body(USERNAME, stack.password))
    assert r["status"] == 200, r["body"]
    cookies = header_values(r, "Set-Cookie")
    assert len(cookies) == 1
    parsed = admin_flags(cookies[0])
    assert parsed["name"] == "__Host-livestats_session"
    flags = parsed["flags"]
    assert flags.get("httponly") is True and flags.get("secure") is True
    assert str(flags.get("samesite")).lower() == "strict"
    assert flags.get("path") == "/"
    assert "domain" not in flags, "prefixo __Host- proibe Domain"
    assert int(flags["max-age"]) == 28800
    assert len(parsed["value"]) >= 40
    session = json.loads(r["body"])
    assert "csrf_token" in session

    # logout tambem limpa o cookie mantendo os atributos
    out = stack.admin(
        "/api/admin/logout", "POST",
        {"Cookie": f"__Host-livestats_session={parsed['value']}", "X-CSRF-Token": session["csrf_token"], **JSON_HEADERS}, "{}",
    )
    assert out["status"] == 200
    cleared = admin_flags(header_values(out, "Set-Cookie")[0])
    assert cleared["flags"].get("httponly") is True and cleared["flags"].get("secure") is True
    assert str(cleared["flags"].get("samesite")).lower() == "strict" and cleared["flags"].get("max-age") == "0"
