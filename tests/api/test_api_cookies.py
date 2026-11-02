"""API-COOKIE-01 .. API-COOKIE-11: cifragem dos cookies do YouTube Studio."""

from __future__ import annotations

import ast
import base64
import hashlib
import json
import logging
import secrets
import sqlite3

import pytest
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, SealedBox

from api_support import APP_DIR, is_authorizer_denial, Stack, assert_matches_openapi, running_stack, seed_golden_lives, seed_subscribers, walk_keys

SUCCESS_MESSAGE = "{n} cookies salvos com sucesso! O scraper vai recarregar automaticamente em alguns segundos."


def code_tokens(path) -> list[str]:
    """Nomes, atributos, imports e literais de texto do codigo (sem docstrings nem comentarios)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    tokens: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            tokens.append(node.id)
        elif isinstance(node, ast.Attribute):
            tokens.append(node.attr)
        elif isinstance(node, ast.arg):
            tokens.append(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            tokens.append(node.name)
        elif isinstance(node, ast.alias):
            tokens.append(node.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            tokens.append(node.module)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            tokens.append(node.value)
    return tokens


def make_cookies(n: int = 3) -> list[dict]:
    """Cookies com nomes/valores-sentinela ALEATORIOS (impossiveis de aparecer por acaso)."""
    return [
        {
            "name": "N" + secrets.token_hex(10),
            "value": "V" + secrets.token_hex(24),
            "domain": ".youtube.com",
            "path": "/",
            "secure": bool(i % 2),
            "httpOnly": True,
            "expirationDate": 1893456000.5,
            "sameSite": "no_restriction",
        }
        for i in range(n)
    ]


def sentinels(cookies) -> list[str]:
    return [c["name"] for c in cookies] + [c["value"] for c in cookies]


def post_cookies(stack: Stack, csrf: str, body, **kwargs):
    if isinstance(body, (bytes, bytearray)):
        kwargs["content"] = body
        kwargs.setdefault("headers", {})["content-type"] = "application/json"
    else:
        kwargs["json"] = body
    headers = {"X-CSRF-Token": csrf, **kwargs.pop("headers", {})}
    return stack.client.post("/api/admin/cookies", headers=headers, **kwargs)


def stored_rows(stack: Stack):
    with stack.shared() as conn:
        return conn.execute("SELECT id, ciphertext, key_id, cookie_count, uploaded_by, created_at FROM cookie_sets ORDER BY id").fetchall()


def decrypt(stack: Stack, ciphertext: bytes) -> bytes:
    return SealedBox(stack.private_key).decrypt(ciphertext)  # a chave privada e SO do teste


# ------------------------------------------------------------------------------------- API-COOKIE-01
def test_api_cookie_01_upload_grava_somente_ciphertext(stack: Stack):
    csrf = stack.login_csrf()
    cookies = make_cookies(5)
    resp = post_cookies(stack, csrf, cookies)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True, "message": SUCCESS_MESSAGE.format(n=5), "cookies_count": 5}
    assert_matches_openapi(resp, "/api/admin/cookies", "post")

    rows = stored_rows(stack)
    assert len(rows) == 1
    assert rows[0]["cookie_count"] == 5 and rows[0]["uploaded_by"] == stack.username

    blob = stack.all_db_bytes("DB_PATH")
    assert rows[0]["key_id"].encode() in blob  # a varredura enxerga o que foi gravado
    plain_json = json.dumps(cookies, ensure_ascii=False, separators=(",", ":")).encode()
    for needle in sentinels(cookies):
        assert needle.encode() not in blob, needle
        assert base64.b64encode(needle.encode()).rstrip(b"=")[:24] not in blob
    assert base64.b64encode(plain_json)[:48] not in blob
    for name in ("ADMIN_DB_PATH",):  # e o outro banco tambem
        for needle in sentinels(cookies):
            assert needle.encode() not in stack.all_db_bytes(name)


# ------------------------------------------------------------------------------------- API-COOKIE-02
def test_api_cookie_02_decifra_com_a_chave_privada_no_formato_normalizado(stack: Stack):
    csrf = stack.login_csrf()
    cookies = make_cookies(4)
    cookies[1]["value"] = "valor-com-acento-ção-" + secrets.token_hex(4)  # UTF-8 literal, sem \u
    assert post_cookies(stack, csrf, cookies).status_code == 200

    plain = decrypt(stack, stored_rows(stack)[0]["ciphertext"])
    expected = [
        {"name": c["name"], "value": c["value"], "domain": c["domain"], "path": c["path"], "secure": c["secure"]}
        for c in cookies
    ]
    assert json.loads(plain) == expected
    for item in json.loads(plain):
        assert list(item) == ["name", "value", "domain", "path", "secure"]  # exatamente 5 campos, nesta ordem
    assert plain == json.dumps(expected, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert "ção".encode() in plain


# ------------------------------------------------------------------------------------- API-COOKIE-03
def test_api_cookie_03_a_api_nao_tem_como_decifrar(stack: Stack):
    csrf = stack.login_csrf()
    cookies = make_cookies(2)
    assert post_cookies(stack, csrf, cookies).status_code == 200

    # (a) o codigo do pacote `app` (nomes, atributos, imports e literais; nao docstrings) nao le nem aceita chave privada
    for path in sorted(APP_DIR.glob("*.py")):
        for token in code_tokens(path):
            lowered = token.lower()
            assert "private" not in lowered and "decrypt" not in lowered, f"{path.name}: {token!r}"

    # (b) a chave que a api tem (publica) nao abre o SealedBox
    from app.db import open_shared

    conn = open_shared(stack.db_path)
    try:
        with pytest.raises(sqlite3.DatabaseError) as denied:
            conn.execute("SELECT ciphertext FROM cookie_sets").fetchall()
        assert is_authorizer_denial(denied.value)
        with pytest.raises(sqlite3.DatabaseError) as denied_star:
            conn.execute("SELECT * FROM cookie_sets").fetchall()
        assert is_authorizer_denial(denied_star.value)
        # metadados continuam legiveis pela api
        meta = conn.execute("SELECT id, key_id, cookie_count, uploaded_by, created_at FROM cookie_sets").fetchall()
        assert len(meta) == 1
    finally:
        conn.close()

    with stack.shared() as inspector:  # o teste (fora da api) le o ciphertext
        ciphertext = inspector.execute("SELECT ciphertext FROM cookie_sets").fetchone()[0]
    with pytest.raises(CryptoError):
        SealedBox(PrivateKey(stack.public_key_bytes)).decrypt(ciphertext)  # bytes da chave publica "como se fossem privada"
    with pytest.raises(CryptoError):
        SealedBox(PrivateKey.generate()).decrypt(ciphertext)  # nem uma chave qualquer


# ------------------------------------------------------------------------------------- API-COOKIE-04
@pytest.mark.parametrize(
    ("body", "detail"),
    [
        (b"isto nao e json", "JSON inválido no body da requisição"),
        (b"", "JSON inválido no body da requisição"),
        (b'{"a": 1}', "Esperado um array JSON com pelo menos 1 cookie"),
        (b"[]", "Esperado um array JSON com pelo menos 1 cookie"),
        (b'"x"', "Esperado um array JSON com pelo menos 1 cookie"),
        (b"null", "Esperado um array JSON com pelo menos 1 cookie"),
        (b'[{"name": "a"}]', "Cookie #0 sem campo 'name' ou 'value'"),
        (b'[{"value": "a"}]', "Cookie #0 sem campo 'name' ou 'value'"),
        (b'[{"name": "a", "value": "b"}, {"name": "c"}]', "Cookie #1 sem campo 'name' ou 'value'"),
        (b'[{"name": "a", "value": "b"}, 7]', "Cookie #1 sem campo 'name' ou 'value'"),
        (b'["texto"]', "Cookie #0 sem campo 'name' ou 'value'"),
        (b'[[1, 2]]', "Cookie #0 sem campo 'name' ou 'value'"),
    ],
)
def test_api_cookie_04_validacao_herdada_do_legado(stack: Stack, body, detail):
    csrf = stack.login_csrf()
    resp = post_cookies(stack, csrf, body)
    assert resp.status_code == 400, resp.text
    assert resp.json() == {"detail": detail}
    assert_matches_openapi(resp, "/api/admin/cookies", "post")
    assert stored_rows(stack) == []


def test_api_cookie_04_400_vem_antes_do_422(stack: Stack):
    csrf = stack.login_csrf()
    body = [{"name": 123, "value": "v"}, {"name": "sem-valor"}]  # #0 tem tipo errado (422), #1 falta campo (400)
    resp = post_cookies(stack, csrf, body)
    assert resp.status_code == 400
    assert resp.json() == {"detail": "Cookie #1 sem campo 'name' ou 'value'"}


# ------------------------------------------------------------------------------------- API-COOKIE-05
def test_api_cookie_05_normalizacao(stack: Stack):
    csrf = stack.login_csrf()
    sentinel = "V" + secrets.token_hex(8)
    body = [
        {"name": "a", "value": sentinel, "domain": ".[youtube.com](http://youtube.com)", "path": "/x", "secure": True,
         "httpOnly": True, "expirationDate": 123.5, "sameSite": "lax", "hostOnly": False, "id": 7},
        {"name": "b", "value": "2"},
        {"name": "c", "value": "3", "domain": "[studio.youtube.com](https://studio.youtube.com/path)"},
        {"name": "d", "value": "", "domain": "accounts.google.com", "path": "", "secure": False},
    ]
    assert post_cookies(stack, csrf, body).status_code == 200
    stored = json.loads(decrypt(stack, stored_rows(stack)[0]["ciphertext"]))
    assert stored == [
        {"name": "a", "value": sentinel, "domain": ".youtube.com", "path": "/x", "secure": True},
        {"name": "b", "value": "2", "domain": ".youtube.com", "path": "/", "secure": False},
        {"name": "c", "value": "3", "domain": "studio.youtube.com", "path": "/", "secure": False},
        {"name": "d", "value": "", "domain": "accounts.google.com", "path": "/", "secure": False},
    ]
    assert all(set(item) == {"name", "value", "domain", "path", "secure"} for item in stored)


# ------------------------------------------------------------------------------------- API-COOKIE-06
def _sent(**over):
    base = {"name": "N-sentinela-" + secrets.token_hex(6), "value": "V-sentinela-" + secrets.token_hex(6)}
    base.update(over)
    return base


@pytest.mark.parametrize(
    "build",
    [
        lambda: [_sent(name=12345)],
        lambda: [_sent(name="n" * 257)],
        lambda: [_sent(name="")],
        lambda: [_sent(value=["lista"])],
        lambda: [_sent(value="v" * 8193)],
        lambda: [_sent(domain="dominio invalido!")],
        lambda: [_sent(domain="evil.com/<script>")],
        lambda: [_sent(domain=".youtube.com;x=1")],
        lambda: [_sent(domain=["x"])],
        lambda: [_sent(domain="d" * 256)],
        lambda: [_sent(path=42)],
        lambda: [_sent(path="/" + "p" * 1024)],
        lambda: [_sent(secure="sim")],
        lambda: [_sent(secure=1)],
        lambda: [_sent() for _ in range(201)],
    ],
)
def test_api_cookie_06_tipos_tamanhos_e_charset_invalidos_devolvem_422(stack: Stack, build):
    csrf = stack.login_csrf()
    body = build()
    resp = post_cookies(stack, csrf, body)
    assert resp.status_code == 422, resp.text
    for item in body[:3]:
        for value in (item["name"], item["value"]):
            if isinstance(value, str) and value:
                assert value not in resp.text
    assert "input" not in set(walk_keys(resp.json()))
    for entry in resp.json()["detail"]:
        assert set(entry) == {"loc", "msg", "type"}
    assert_matches_openapi(resp, "/api/admin/cookies", "post")
    assert stored_rows(stack) == []


def test_api_cookie_06_exatamente_200_cookies_e_aceito(stack: Stack):
    csrf = stack.login_csrf()
    resp = post_cookies(stack, csrf, [_sent() for _ in range(200)])
    assert resp.status_code == 200 and resp.json()["cookies_count"] == 200


def test_api_cookie_06_corpo_acima_de_256_kib_devolve_413(stack: Stack):
    csrf = stack.login_csrf()
    resp = post_cookies(stack, csrf, [{"name": "a", "value": "v" * (260 * 1024)}])
    assert resp.status_code == 413
    assert stored_rows(stack) == []


# ------------------------------------------------------------------------------------- API-COOKIE-07
def test_api_cookie_07_dois_uploads_mantem_so_o_ultimo(stack: Stack):
    csrf = stack.login_csrf()
    assert post_cookies(stack, csrf, make_cookies(3)).status_code == 200
    first_id = stored_rows(stack)[0]["id"]
    last = make_cookies(2)
    assert post_cookies(stack, csrf, last).status_code == 200
    rows = stored_rows(stack)
    assert len(rows) == 1
    assert rows[0]["id"] > first_id and rows[0]["cookie_count"] == 2
    assert json.loads(decrypt(stack, rows[0]["ciphertext"]))[0]["name"] == last[0]["name"]


# ------------------------------------------------------------------------------------- API-COOKIE-08
def test_api_cookie_08_nenhuma_resposta_devolve_cookies_ou_ciphertext(stack: Stack):
    csrf = stack.login_csrf()
    cookies = make_cookies(3)
    assert post_cookies(stack, csrf, cookies).status_code == 200
    with stack.shared() as conn:
        seed_golden_lives(conn)
        seed_subscribers(conn)
    ciphertext = stored_rows(stack)[0]["ciphertext"]
    forbidden = [s.encode() for s in sentinels(cookies)]
    forbidden += [ciphertext.hex().encode(), base64.b64encode(ciphertext), base64.urlsafe_b64encode(ciphertext)]

    bodies = []
    for path in ("/api/admin/session", "/api/admin/slots", "/api/admin/workers/status", "/health",
                 "/api/public/lives/1", "/api/public/lives/4", "/api/public/subscribers", "/live1.json", "/inscritos.json"):
        resp = stack.client.get(path)
        assert resp.status_code == 200, path
        bodies.append((path, resp.content, str(resp.headers)))
    for path, body, headers in bodies:
        for needle in forbidden:
            assert needle not in body and needle not in headers.encode(), (path, needle[:12])
    status = stack.client.get("/api/admin/workers/status").json()
    assert status["cookies"]["configured"] is True and status["cookies"]["count"] == 3
    assert "ciphertext" not in json.dumps(status)


# ------------------------------------------------------------------------------------- API-COOKIE-09
def test_api_cookie_09_logs_nao_contem_cookies(tmp_path, creds, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    with running_stack(tmp_path, creds) as stack:
        csrf = stack.login_csrf()
        valid = make_cookies(3)
        poison = _sent()
        assert post_cookies(stack, csrf, valid).status_code == 200
        assert post_cookies(stack, csrf, [poison, {"name": poison["name"]}]).status_code == 400
        assert post_cookies(stack, csrf, [dict(poison, domain="dominio invalido!")]).status_code == 422
        assert post_cookies(stack, csrf, b"{" + poison["value"].encode()).status_code == 400
        assert post_cookies(stack, csrf, [poison] * 201).status_code == 422
    captured = capsys.readouterr()
    haystack = caplog.text + "".join(str(r.args) + r.getMessage() for r in caplog.records) + captured.out + captured.err
    for needle in sentinels(valid) + [poison["name"], poison["value"]]:
        assert needle not in haystack, needle


# ------------------------------------------------------------------------------------- API-COOKIE-10
@pytest.mark.parametrize("case", ["sem_variavel", "arquivo_inexistente", "vazio", "nao_base64", "tamanho_errado", "base64_de_pouco"])
def test_api_cookie_10_chave_publica_ausente_ou_corrompida_impede_a_subida(tmp_path, creds, case):
    from app.config import ConfigError

    target = tmp_path / "pub"
    contents = {
        "vazio": "",
        "nao_base64": "isto não é base64!!",
        "tamanho_errado": base64.b64encode(secrets.token_bytes(31)).decode(),
        "base64_de_pouco": base64.b64encode(secrets.token_bytes(48)).decode(),
    }
    if case == "sem_variavel":
        override = None
    else:
        if case in contents:
            target.write_text(contents[case], encoding="utf-8")
        override = str(target)
    with pytest.raises(ConfigError) as caught:
        with running_stack(tmp_path, creds, COOKIE_PUBLIC_KEY_FILE=override):
            pytest.fail("a app subiu sem chave publica valida")
    assert "COOKIE_PUBLIC_KEY_FILE" in str(caught.value)
    assert stored_rows_count(tmp_path) == 0  # nunca gravou cookie sem cifrar


def stored_rows_count(tmp_path) -> int:
    conn = sqlite3.connect(tmp_path / "data" / "livestats.db")
    try:
        return conn.execute("SELECT COUNT(*) FROM cookie_sets").fetchone()[0]
    except sqlite3.OperationalError:
        return 0
    finally:
        conn.close()


# ------------------------------------------------------------------------------------- API-COOKIE-11
def test_api_cookie_11_key_id_e_o_prefixo_do_sha256_da_chave_publica(stack: Stack):
    csrf = stack.login_csrf()
    assert post_cookies(stack, csrf, make_cookies(1)).status_code == 200
    expected = hashlib.sha256(stack.public_key_bytes).hexdigest()[:16]
    assert stored_rows(stack)[0]["key_id"] == expected
    assert len(expected) == 16
