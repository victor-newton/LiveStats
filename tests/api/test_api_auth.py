"""API-AUTH-01 .. API-AUTH-14: autenticacao, sessao, CSRF, limite de tentativas."""

from __future__ import annotations

import hashlib
import re
import time

import pytest
from fastapi.testclient import TestClient

from api_support import (
    BASE_URL,
    BLANK_SLOTS,
    SESSION_COOKIE,
    Stack,
    assert_matches_openapi,
    assert_schema,
    running_stack,
    walk_keys,
)

UNAUTH = {"detail": "Não autenticado"}
TOO_MANY = {"detail": "Muitas tentativas. Tente novamente em instantes."}
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def iter_routes(routes):
    """Rotas efetivas de ``app.routes`` (o FastAPI atual agrupa os roteadores incluidos em ``_IncludedRouter``)."""
    for route in routes:
        if hasattr(route, "effective_candidates"):
            yield from iter_routes(route.effective_candidates())
        else:
            yield route


def admin_routes(app) -> set[tuple[str, str]]:
    found = set()
    for route in iter_routes(app.routes):
        path = getattr(route, "path", None)
        if path and path.startswith("/api/admin"):
            for method in route.methods:
                found.add((method, path))
    return found


def session_cookie_value(client: TestClient) -> str:
    value = client.cookies.get(SESSION_COOKIE)
    assert value, "cookie de sessao ausente"
    return value


def raw_cookie_header(value: str) -> dict:
    return {"cookie": f"{SESSION_COOKIE}={value}"}


# ------------------------------------------------------------------------------------- API-AUTH-01
EXPECTED_ADMIN_ROUTES = {
    ("POST", "/api/admin/login"),
    ("GET", "/api/admin/session"),
    ("GET", "/api/admin/slots"),
    ("PUT", "/api/admin/slots"),
    ("POST", "/api/admin/cookies"),
    ("GET", "/api/admin/workers/status"),
    ("POST", "/api/admin/logout"),
    *{(m, "/api/admin") for m in ALL_METHODS},
    *{(m, "/api/admin/{path:path}") for m in ALL_METHODS},
}


def test_api_auth_01_toda_rota_admin_sem_sessao_responde_401(stack: Stack):
    routes = admin_routes(stack.app)
    # Se surgir uma rota admin nova, este teste falha ate ela ser coberta aqui.
    assert routes == EXPECTED_ADMIN_ROUTES

    checked = 0
    for method, path in sorted(routes):
        if (method, path) == ("POST", "/api/admin/login"):
            continue
        concrete = path.replace("{path:path}", "nao-existe/x")
        fresh = stack.new_client()  # sem cookie algum
        resp = fresh.request(method, concrete)  # sem sessao e sem CSRF
        assert resp.status_code == 401, (method, concrete, resp.status_code)
        if method != "HEAD":
            assert resp.json() == UNAUTH, (method, concrete)
        checked += 1
    assert checked == len(routes) - 1

    # Caminhos inexistentes explicitos do contrato
    fresh = stack.new_client()
    for method, path in (("GET", "/api/admin/nao-existe"), ("POST", "/api/admin/x/y"), ("DELETE", "/api/admin/slots/1")):
        resp = fresh.request(method, path)
        assert (resp.status_code, resp.json()) == (401, UNAUTH), (method, path)


# ------------------------------------------------------------------------------------- API-AUTH-02
def test_api_auth_02_csrf_obrigatorio_nos_metodos_que_alteram_estado(stack: Stack):
    csrf = stack.login_csrf()
    other_csrf = stack.login_csrf(stack.new_client())  # token valido, mas de OUTRA sessao
    assert other_csrf != csrf

    slots_body = {"slots": BLANK_SLOTS}
    cookies_body = [{"name": "a", "value": "b"}]
    attempts = {
        "PUT /api/admin/slots": ("PUT", "/api/admin/slots", slots_body),
        "POST /api/admin/cookies": ("POST", "/api/admin/cookies", cookies_body),
        "POST /api/admin/logout": ("POST", "/api/admin/logout", None),
    }
    for label, (method, path, body) in attempts.items():
        for headers in ({}, {"X-CSRF-Token": ""}, {"X-CSRF-Token": other_csrf}, {"X-CSRF-Token": csrf[:-1] + "x"}):
            resp = stack.client.request(method, path, json=body, headers=headers)
            assert resp.status_code == 403, (label, headers, resp.text)
            assert resp.json() == {"detail": "CSRF token inválido ou ausente"}
    # Nada foi gravado/encerrado pelas tentativas recusadas
    assert stack.client.get("/api/admin/session").status_code == 200

    # GETs funcionam sem token
    for path in ("/api/admin/session", "/api/admin/slots", "/api/admin/workers/status"):
        assert stack.client.get(path).status_code == 200, path

    # Com o token correto: 2xx (logout por ultimo, encerra a sessao)
    headers = {"X-CSRF-Token": csrf}
    assert stack.client.put("/api/admin/slots", json=slots_body, headers=headers).status_code == 200
    assert stack.client.post("/api/admin/cookies", json=cookies_body, headers=headers).status_code == 200
    assert stack.client.post("/api/admin/logout", headers=headers).status_code == 200


# ------------------------------------------------------------------------------------- API-AUTH-03
def test_api_auth_03_atributos_do_cookie_de_sessao(stack: Stack):
    resp = stack.login()
    assert resp.status_code == 200
    set_cookies = resp.headers.get_list("set-cookie")
    assert len(set_cookies) == 1
    parts = [p.strip() for p in set_cookies[0].split(";")]
    name, _, value = parts[0].partition("=")
    attrs = {p.split("=")[0].lower(): (p.partition("=")[2] or True) for p in parts[1:]}
    assert name == "__Host-livestats_session"
    assert len(value) >= 32
    assert attrs["httponly"] is True
    assert attrs["secure"] is True
    assert attrs["samesite"] == "Strict"
    assert attrs["path"] == "/"
    assert attrs["max-age"] == "28800"
    assert "domain" not in attrs
    body = resp.json()
    assert body["csrf_token"] and value not in resp.text
    assert body["ok"] is True and body["username"] == stack.username
    assert_matches_openapi(resp, "/api/admin/login", "post")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", body["expires_at"])


# ------------------------------------------------------------------------------------- API-AUTH-04
def test_api_auth_04_usuario_inexistente_e_senha_errada_sao_indistinguiveis(stack: Stack):
    def attempt(i: int, username: str, password: str):
        start = time.perf_counter()
        resp = stack.login(ip=f"10.1.0.{i}", username=username, password=password)
        return resp, time.perf_counter() - start

    wrong_pw, unknown_user, wrong_times, unknown_times = None, None, [], []
    for i in range(3):
        wrong_pw, t1 = attempt(i, stack.username, "senha-errada-" + str(i))
        unknown_user, t2 = attempt(10 + i, f"nao-existe-{i}", stack.password)
        wrong_times.append(t1)
        unknown_times.append(t2)
        assert wrong_pw.status_code == unknown_user.status_code == 401
        assert wrong_pw.json() == unknown_user.json() == {"detail": "Credenciais inválidas"}
        assert "set-cookie" not in wrong_pw.headers and "set-cookie" not in unknown_user.headers
    # Ambos executam a verificacao argon2: tempos da mesma ordem de grandeza (sem hash seria ~100x menor)
    assert min(unknown_times) > 0.3 * min(wrong_times), (unknown_times, wrong_times)
    assert min(wrong_times) > 0.3 * min(unknown_times), (unknown_times, wrong_times)
    assert min(wrong_times) > 0.005  # argon2 de fato rodou


# ------------------------------------------------------------------------------------- API-AUTH-05
def test_api_auth_05_sexta_tentativa_recebe_429_mesmo_com_senha_certa(stack: Stack):
    ip = "10.2.0.1"
    for n in range(5):
        resp = stack.login(ip=ip, password=f"errada-{n}")
        assert resp.status_code == 401, f"falha #{n + 1}: {resp.status_code}"

    blocked = stack.login(ip=ip)  # senha CERTA
    assert blocked.status_code == 429
    assert blocked.json() == TOO_MANY
    assert blocked.headers["retry-after"] == "60"
    assert "set-cookie" not in blocked.headers
    assert_matches_openapi(blocked, "/api/admin/login", "post")

    # Tentativas bloqueadas nao prolongam o bloqueio: a janela corre desde as FALHAS reais
    stack.clock.advance(30)
    again = stack.login(ip=ip)
    assert again.status_code == 429 and again.headers["retry-after"] == "30"
    stack.clock.advance(15)
    assert stack.login(ip=ip).status_code == 429
    stack.clock.advance(16)  # 61 s depois das falhas
    ok = stack.login(ip=ip)
    assert ok.status_code == 200, ok.text


def test_api_auth_05_sucesso_nao_conta_como_falha(stack: Stack):
    ip = "10.2.0.2"
    assert stack.login(ip=ip).status_code == 200
    for n in range(5):
        assert stack.login(ip=ip, password=f"errada-{n}").status_code == 401, f"falha #{n + 1}"
    assert stack.login(ip=ip).status_code == 429


def test_api_auth_05_janela_de_60s_exata(stack: Stack):
    ip = "10.2.0.3"
    for n in range(5):
        assert stack.login(ip=ip, password=f"errada-{n}").status_code == 401
    stack.clock.advance(59)
    assert stack.login(ip=ip).status_code == 429
    stack.clock.advance(1)  # 60 s: as falhas saem da janela
    assert stack.login(ip=ip).status_code == 200


# ------------------------------------------------------------------------------------- API-AUTH-06
def test_api_auth_06_limite_por_usuario_com_ips_diferentes(stack: Stack):
    for n in range(5):
        resp = stack.login(ip=f"10.3.0.{n + 1}", password=f"errada-{n}")  # IP novo a cada falha
        assert resp.status_code == 401
    resp = stack.login(ip="10.3.0.99")  # IP inedito, senha certa
    assert resp.status_code == 429


def test_api_auth_06_limite_por_ip_com_usuarios_diferentes(stack: Stack):
    ip = "10.3.1.1"
    for n in range(5):
        resp = stack.login(ip=ip, username=f"intruso-{n}", password="qualquer-coisa")  # usuario novo a cada falha
        assert resp.status_code == 401
    resp = stack.login(ip=ip)  # usuario e senha certos, mesmo IP
    assert resp.status_code == 429


# ------------------------------------------------------------------------------------- API-AUTH-07
def test_api_auth_07_logout_apaga_a_sessao_no_servidor(stack: Stack):
    csrf = stack.login_csrf()
    cookie = session_cookie_value(stack.client)
    with stack.admin() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 1

    resp = stack.client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200 and resp.json() == {"ok": True}
    assert_matches_openapi(resp, "/api/admin/logout", "post")
    expired = resp.headers["set-cookie"]
    assert expired.startswith(f"{SESSION_COOKIE}=") and "Max-Age=0" in expired and "HttpOnly" in expired
    with stack.admin() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0

    replay = stack.new_client().get("/api/admin/session", headers=raw_cookie_header(cookie))
    assert replay.status_code == 401 and replay.json() == UNAUTH


# ------------------------------------------------------------------------------------- API-AUTH-08
def test_api_auth_08_expiracao_por_ociosidade(stack: Stack):
    stack.login_csrf()
    stack.clock.advance(29 * 60 + 59)  # 1799 s ocioso: ainda vale
    assert stack.client.get("/api/admin/session").status_code == 200
    stack.clock.advance(30 * 60)  # exatamente a ociosidade apos o ultimo uso
    resp = stack.client.get("/api/admin/session")
    assert resp.status_code == 401 and resp.json() == UNAUTH
    with stack.admin() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 0  # linha removida


def test_api_auth_08_uso_renova_ociosidade_mas_nao_o_limite_absoluto(stack: Stack):
    stack.login_csrf()
    first = stack.client.get("/api/admin/session").json()
    ok_steps = 0
    for _ in range(40):
        stack.clock.advance(25 * 60)  # sempre menos que 30 min de ociosidade
        resp = stack.client.get("/api/admin/session")
        if resp.status_code != 200:
            break
        ok_steps += 1
        info = resp.json()
        assert info["expires_at"] == first["expires_at"]  # o limite absoluto nunca se move
        assert info["idle_expires_at"] > first["idle_expires_at"] or info["idle_expires_at"] == info["expires_at"]
    else:
        pytest.fail("a sessao nunca expirou")
    # 8 h = 480 min: 25 * 19 = 475 min valem; 25 * 20 = 500 min > 8 h
    assert ok_steps == 19
    assert resp.status_code == 401 and resp.json() == UNAUTH


# ------------------------------------------------------------------------------------- API-AUTH-09
def test_api_auth_09_so_o_hash_do_id_vai_para_o_banco(stack: Stack):
    stack.login_csrf()
    cookie = session_cookie_value(stack.client)
    expected = hashlib.sha256(cookie.encode("utf-8")).hexdigest()
    with stack.admin() as conn:
        rows = conn.execute("SELECT id_hash, csrf_token FROM admin_sessions").fetchall()
    assert [r["id_hash"] for r in rows] == [expected]

    admin_bytes = stack.all_db_bytes("ADMIN_DB_PATH")
    assert expected.encode() in admin_bytes  # a varredura enxerga o que foi gravado
    for name in ("ADMIN_DB_PATH", "DB_PATH"):
        assert cookie.encode() not in stack.all_db_bytes(name), name


# ------------------------------------------------------------------------------------- API-AUTH-10
def test_api_auth_10_login_com_cookie_antigo_emite_sessao_nova(stack: Stack):
    stack.login_csrf()
    old = session_cookie_value(stack.client)
    resp = stack.login()  # o cookie antigo vai junto na requisicao
    assert resp.status_code == 200
    new = session_cookie_value(stack.client)
    assert new != old
    assert stack.new_client().get("/api/admin/session", headers=raw_cookie_header(old)).status_code == 401
    assert stack.new_client().get("/api/admin/session", headers=raw_cookie_header(new)).status_code == 200
    with stack.admin() as conn:
        assert conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 1


def test_api_auth_10_cookie_inventado_pelo_atacante_nao_e_adotado(stack: Stack):
    attacker_id = "A" * 43
    client = stack.new_client()
    client.cookies.set(SESSION_COOKIE, attacker_id, domain="testserver.local")
    assert stack.login(client).status_code == 200
    assert session_cookie_value(client) != attacker_id
    assert stack.new_client().get("/api/admin/session", headers=raw_cookie_header(attacker_id)).status_code == 401


# ------------------------------------------------------------------------------------- API-AUTH-11
@pytest.mark.parametrize(
    "case",
    ["sem_variavel", "arquivo_inexistente", "arquivo_vazio", "so_espacos", "texto_qualquer", "argon2i", "hash_truncado"],
)
def test_api_auth_11_sem_hash_de_senha_valido_a_app_nao_sobe(tmp_path, creds, case):
    sentinel = "SENTINELA-" + creds.username
    overrides: dict = {}
    if case == "sem_variavel":
        overrides["ADMIN_PASSWORD_HASH_FILE"] = None
    else:
        target = tmp_path / "h"
        content = {
            "arquivo_inexistente": None,
            "arquivo_vazio": "",
            "so_espacos": "  \n\n",
            "texto_qualquer": sentinel,
            "argon2i": "$argon2i$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$RdescudvJCsgt3ub+b+dWRWJTmaaJObG",
            "hash_truncado": creds.password_hash[:40],
        }[case]
        if content is not None:
            target.write_text(content, encoding="utf-8")
        overrides["ADMIN_PASSWORD_HASH_FILE"] = str(target)

    from app.config import ConfigError

    with pytest.raises(ConfigError) as caught:
        with running_stack(tmp_path, creds, **overrides):
            pytest.fail("a app subiu sem hash de senha valido")
    assert "ADMIN_PASSWORD_HASH_FILE" in str(caught.value)
    assert sentinel not in str(caught.value)  # nunca imprime o conteudo do arquivo


def test_api_auth_11_hash_valido_sobe_e_autentica(tmp_path, creds):
    with running_stack(tmp_path, creds) as running:
        assert running.login().status_code == 200


# ------------------------------------------------------------------------------------- API-AUTH-12
@pytest.mark.parametrize(
    "body",
    [
        {"username": "x"},
        {"password": "y"},
        {"username": "x", "password": 12345},
        {"username": ["a"], "password": "SENTINELA-SENHA"},
        {"username": "x", "password": ["SENTINELA-SENHA"]},
        {"username": "x", "password": "SENTINELA-SENHA", "extra": "SENTINELA-EXTRA"},
        {"username": "", "password": "SENTINELA-SENHA"},
        {"username": "u" * 65, "password": "SENTINELA-SENHA"},
        {"username": "x", "password": "p" * 1025},
        ["SENTINELA-SENHA"],
        "SENTINELA-SENHA",
    ],
)
def test_api_auth_12_corpo_invalido_devolve_422_sem_input(stack: Stack, body):
    resp = stack.client.post("/api/admin/login", json=body)
    assert resp.status_code == 422, resp.text
    assert "SENTINELA-SENHA" not in resp.text and "p" * 1025 not in resp.text
    assert "input" not in set(walk_keys(resp.json()))
    for item in resp.json()["detail"]:
        assert set(item) == {"loc", "msg", "type"}
    assert_matches_openapi(resp, "/api/admin/login", "post")


def test_api_auth_12_corpo_que_nao_e_json_devolve_400(stack: Stack):
    resp = stack.client.post("/api/admin/login", content=b"{senha: SENTINELA", headers={"content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "JSON inválido no body da requisição"}
    assert "SENTINELA" not in resp.text


# ------------------------------------------------------------------------------------- API-AUTH-13
def test_api_auth_13_session_devolve_token_e_expiracoes(stack: Stack):
    login = stack.login()
    csrf = login.json()["csrf_token"]
    resp = stack.client.get("/api/admin/session")
    assert resp.status_code == 200
    info = resp.json()
    assert info["authenticated"] is True
    assert info["username"] == stack.username
    assert info["csrf_token"] == csrf
    assert info["expires_at"] == login.json()["expires_at"]
    assert info["public_base_url"] is None
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", info["idle_expires_at"])
    assert info["idle_expires_at"] < info["expires_at"]
    assert_schema(info, "SessionInfo")
    assert_matches_openapi(resp, "/api/admin/session", "get")


def test_api_auth_13_public_base_url_configurada(tmp_path, creds):
    with running_stack(tmp_path, creds, PUBLIC_BASE_URL="https://live.example.com/") as running:
        running.login_csrf()
        assert running.client.get("/api/admin/session").json()["public_base_url"] == "https://live.example.com"


# ------------------------------------------------------------------------------------- API-AUTH-14
def test_api_auth_14_usuario_e_comparado_exatamente_e_contado_em_minusculo(stack: Stack):
    name = stack.username
    assert name != name.lower()
    for i, variant in enumerate((name.upper(), name.lower(), name.swapcase())):
        resp = stack.login(ip=f"10.4.0.{i}", username=variant)  # senha CERTA, usuario com outra caixa
        assert resp.status_code == 401, variant
        assert "set-cookie" not in resp.headers
    # contador por usuario em minusculo: estas duas falhas somam com as tres acima (5 no total)
    for i in range(2):
        assert stack.login(ip=f"10.4.1.{i}", password="errada").status_code == 401
    assert stack.login(ip="10.4.2.1").status_code == 429

    with stack.admin() as conn:
        users = {row[0] for row in conn.execute("SELECT username FROM login_attempts")}
    assert users == {name.lower()}


# --------------------------------------------------------------------------------- extra: isolamento
def test_api_auth_extra_sessao_de_outro_cliente_nao_vale_sem_o_cookie(stack: Stack):
    stack.login_csrf()
    stranger = stack.new_client()
    assert stranger.get("/api/admin/session").status_code == 401
    assert stranger.get("/api/admin/session", headers=raw_cookie_header("B" * 43)).status_code == 401
    assert stranger.get("/api/admin/session", headers={"cookie": f"{SESSION_COOKIE}=%00%00"}).status_code == 401
    assert BASE_URL.startswith("https://")
