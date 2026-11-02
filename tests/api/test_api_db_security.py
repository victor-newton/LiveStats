"""API-DB-*, API-SEC-*, API-ADM-01, API-HEALTH-01, API-VAL-01: bancos, seguranca e disponibilidade."""

from __future__ import annotations

import ast
import logging
import re
import sqlite3
import secrets
from pathlib import Path

import pytest

from api_support import (
    APP_DIR,
    DB_DIR,
    REPO,
    START_EPOCH,
    SESSION_COOKIE,
    TOOLS_DIR,
    Stack,
    assert_matches_openapi,
    assert_schema,
    is_authorizer_denial,
    running_stack,
    seed_golden_lives,
    seed_subscribers,
    walk_keys,
)
from app import db

# ------------------------------------------------------------------------------------- API-DB-01
SHARED_COLUMNS = {
    "schema_meta": [("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0)],
    "slots": [("slot", "INTEGER", 0, 1), ("url", "TEXT", 1, 0), ("updated_at", "TEXT", 0, 0)],
    "live_stats": [
        ("slot", "INTEGER", 0, 1), ("active", "INTEGER", 1, 0), ("video_id", "TEXT", 1, 0), ("title", "TEXT", 1, 0),
        ("status", "TEXT", 1, 0), ("views", "INTEGER", 1, 0), ("likes", "INTEGER", 1, 0),
        ("views_formatted", "TEXT", 1, 0), ("likes_formatted", "TEXT", 1, 0), ("updated_at", "TEXT", 1, 0),
    ],
    "subscriber_reading": [("id", "INTEGER", 0, 1), ("value", "TEXT", 1, 0), ("raw", "TEXT", 1, 0), ("read_at", "TEXT", 1, 0)],
    "cookie_sets": [
        ("id", "INTEGER", 0, 1), ("ciphertext", "BLOB", 1, 0), ("key_id", "TEXT", 1, 0), ("cookie_count", "INTEGER", 1, 0),
        ("uploaded_by", "TEXT", 1, 0), ("created_at", "TEXT", 1, 0),
    ],
    "worker_status": [
        ("worker", "TEXT", 0, 1), ("state", "TEXT", 1, 0), ("last_heartbeat_at", "INTEGER", 1, 0),
        ("last_success_at", "INTEGER", 0, 0), ("consecutive_failures", "INTEGER", 1, 0), ("interval_seconds", "INTEGER", 1, 0),
        ("next_attempt_at", "INTEGER", 0, 0), ("last_error_code", "TEXT", 0, 0), ("last_error_message", "TEXT", 0, 0),
        ("auth_state", "TEXT", 0, 0), ("cookies_applied_id", "INTEGER", 0, 0), ("version", "TEXT", 1, 0),
    ],
}
ADMIN_COLUMNS = {
    "schema_meta": [("key", "TEXT", 0, 1), ("value", "TEXT", 1, 0)],
    "admin_sessions": [
        ("id_hash", "TEXT", 0, 1), ("csrf_token", "TEXT", 1, 0), ("username", "TEXT", 1, 0), ("client_ip", "TEXT", 1, 0),
        ("created_at", "INTEGER", 1, 0), ("last_seen_at", "INTEGER", 1, 0), ("expires_at", "INTEGER", 1, 0),
    ],
    "login_attempts": [
        ("id", "INTEGER", 0, 1), ("attempted_at", "INTEGER", 1, 0), ("ip", "TEXT", 1, 0), ("username", "TEXT", 1, 0),
        ("success", "INTEGER", 1, 0),
    ],
}


def describe(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    out = {}
    for table in tables:
        cols = []
        for _cid, name, ctype, notnull, _default, pk in conn.execute(f"PRAGMA table_info({table})"):
            # PRIMARY KEY nao carrega NOT NULL explicito no contrato: compara notnull so das colunas comuns
            cols.append((name, ctype, 0 if pk else notnull, 1 if pk else 0))
        out[table] = cols
    return out


def test_api_db_01_esquemas_idempotentes_e_conforme_o_contrato(tmp_path):
    shared, admin = tmp_path / "livestats.db", tmp_path / "admin.db"
    for _ in range(2):  # duas vezes cada, em bancos novos
        db.bootstrap_schema(str(shared), str(DB_DIR / "schema.sql"))
        db.bootstrap_schema(str(admin), str(DB_DIR / "admin_schema.sql"))

    conn = sqlite3.connect(shared)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert describe(conn) == SHARED_COLUMNS
    assert [r[0] for r in conn.execute("SELECT slot FROM slots ORDER BY slot")] == [1, 2, 3, 4]
    assert conn.execute("SELECT COUNT(*) FROM schema_meta").fetchone()[0] == 1
    assert {"admin_sessions", "login_attempts"}.isdisjoint(describe(conn))
    conn.close()

    conn = sqlite3.connect(admin)
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert describe(conn) == ADMIN_COLUMNS
    assert set(describe(conn)) == {"schema_meta", "admin_sessions", "login_attempts"}
    conn.close()


def test_api_db_01_a_subida_da_app_cria_os_dois_bancos_e_repetir_nao_duplica(tmp_path, creds):
    with running_stack(tmp_path, creds) as first:
        first.login_csrf()
        data_path, admin_path = first.db_path, first.admin_db_path
    with running_stack(tmp_path, creds) as second:  # segunda subida sobre os mesmos arquivos
        assert second.db_path == data_path
        with second.shared() as conn:
            assert describe(conn) == SHARED_COLUMNS
            assert conn.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 4
        with second.admin() as conn:
            assert describe(conn) == ADMIN_COLUMNS
            assert conn.execute("SELECT COUNT(*) FROM admin_sessions").fetchone()[0] == 1  # a sessao sobreviveu
    assert Path(data_path) != Path(admin_path)


# ------------------------------------------------------------------------------------- API-DB-02
def test_api_db_02_autorizador_permite_o_que_a_api_precisa(stack: Stack):
    conn = db.open_shared(stack.db_path)
    try:
        conn.execute("UPDATE slots SET url = ?, updated_at = ? WHERE slot = ?", ("dQw4w9WgXcQ", "2026-11-02T00:00:00Z", 1))
        assert conn.execute("SELECT url FROM slots WHERE slot = 1").fetchone()[0] == "dQw4w9WgXcQ"
        for table in ("live_stats", "subscriber_reading", "worker_status", "schema_meta"):
            conn.execute(f"SELECT * FROM {table}").fetchall()
        with db.transaction(conn, immediate=True):
            conn.execute(
                "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at) VALUES (?, ?, 1, 'x', '2026-11-02T00:00:00Z')",
                (secrets.token_bytes(64), "0123456789abcdef"),
            )
        assert conn.execute("SELECT id, key_id, cookie_count, uploaded_by, created_at FROM cookie_sets").fetchall()
        conn.execute("DELETE FROM cookie_sets WHERE id <> 0")
        assert conn.execute("SELECT COUNT(*) FROM cookie_sets").fetchone()[0] == 0
    finally:
        conn.close()


def test_api_db_02_autorizador_nega_sql_malicioso_simulado(stack: Stack):
    injected_url = "x' UNION SELECT ciphertext FROM cookie_sets --"
    conn = db.open_shared(stack.db_path)
    try:
        denied = [
            f"SELECT url FROM slots WHERE url = '{injected_url}'",           # injecao lendo a coluna cifrada
            "SELECT ciphertext FROM cookie_sets",
            "SELECT * FROM cookie_sets",
            "INSERT INTO live_stats (slot, active, video_id, title, status, updated_at) VALUES (1, 0, '', '', 'sem_url', '2026-11-02T00:00:00Z')",
            "UPDATE live_stats SET views = 1",
            "DELETE FROM live_stats",
            "INSERT INTO worker_status (worker, state, last_heartbeat_at, interval_seconds) VALUES ('lives', 'ok', 1, 30)",
            "UPDATE worker_status SET state = 'ok'",
            "INSERT INTO subscriber_reading (id, value, raw, read_at) VALUES (1, '12', '12', '2026-11-02T00:00:00Z')",
            "DELETE FROM subscriber_reading",
            "INSERT INTO slots (slot, url) VALUES (9, '')",
            "DELETE FROM slots",
            "UPDATE cookie_sets SET key_id = 'aaaaaaaaaaaaaaaa'",
            "INSERT INTO schema_meta (key, value) VALUES ('x', 'y')",
            "DROP TABLE slots",
            "CREATE TABLE intruso (a TEXT)",
            "ALTER TABLE slots ADD COLUMN extra TEXT",
            "ATTACH DATABASE ':memory:' AS outro",
            "PRAGMA writable_schema = 1",
            "SELECT sql FROM sqlite_master",
        ]
        for statement in denied:
            with pytest.raises(sqlite3.DatabaseError) as caught:
                conn.execute(statement).fetchall()
            assert is_authorizer_denial(caught.value), (statement, str(caught.value))
    finally:
        conn.close()
    # a recusa nao alterou nada
    with stack.shared() as inspector:
        assert inspector.execute("SELECT COUNT(*) FROM live_stats").fetchone()[0] == 0
        assert inspector.execute("SELECT COUNT(*) FROM slots").fetchone()[0] == 4


def test_api_db_02_autorizador_do_admin_db(stack: Stack):
    conn = db.open_admin(stack.admin_db_path)
    try:
        conn.execute("SELECT * FROM admin_sessions").fetchall()
        conn.execute("SELECT * FROM login_attempts").fetchall()
        for statement in ("CREATE TABLE intruso (a TEXT)", "DROP TABLE login_attempts", "ATTACH DATABASE ':memory:' AS x",
                          "PRAGMA writable_schema = 1", "SELECT sql FROM sqlite_master"):
            with pytest.raises(sqlite3.DatabaseError) as caught:
                conn.execute(statement).fetchall()
            assert is_authorizer_denial(caught.value), statement
    finally:
        conn.close()


def test_api_db_02_conexao_nao_cria_banco_inexistente(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.open_shared(str(tmp_path / "nao-existe.db"))
    assert not (tmp_path / "nao-existe.db").exists()


# ------------------------------------------------------------------------------------- API-DB-03
def test_api_db_03_estado_de_admin_nunca_toca_o_banco_compartilhado(stack: Stack):
    csrf = stack.login_csrf()
    session_id = stack.client.cookies.get(SESSION_COOKIE)
    for n in range(2):
        assert stack.login(ip=f"10.9.0.{n}", password="errada").status_code == 401
    assert stack.client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf}).status_code == 200

    # admin.db guardou as tentativas
    with stack.admin() as conn:
        assert conn.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0] == 3
    # livestats.db: sem as tabelas de admin e sem nenhum rastro em NENHUMA tabela
    with stack.shared() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert "admin_sessions" not in tables and "login_attempts" not in tables
        dump = ""
        for table in tables:
            dump += repr(conn.execute(f"SELECT * FROM {table}").fetchall())
    for needle in (session_id, csrf, stack.username, stack.username.lower(), "10.9.0.0", "testclient"):
        assert needle not in dump, needle
    assert session_id.encode() not in stack.all_db_bytes("DB_PATH")


def test_api_db_03_sessao_forjada_no_banco_compartilhado_nao_vale(stack: Stack):
    """Um 'worker comprometido' escreve uma sessao no livestats.db: a api nao consulta esse banco."""
    forged = "F" * 43
    import hashlib

    with stack.shared() as conn:
        conn.execute(
            "CREATE TABLE admin_sessions (id_hash TEXT PRIMARY KEY, csrf_token TEXT, username TEXT, client_ip TEXT, "
            "created_at INTEGER, last_seen_at INTEGER, expires_at INTEGER)"
        )
        conn.execute(
            "INSERT INTO admin_sessions VALUES (?, ?, ?, '', ?, ?, ?)",
            (hashlib.sha256(forged.encode()).hexdigest(), "c" * 43, stack.username, 1, 1, 2**40),
        )
        conn.commit()
    resp = stack.new_client().get("/api/admin/session", headers={"cookie": f"{SESSION_COOKIE}={forged}"})
    assert resp.status_code == 401


@pytest.mark.parametrize("case", ["mesmo_caminho", "mesmo_arquivo_por_outro_nome", "variavel_ausente", "variavel_vazia"])
def test_api_db_03_admin_db_precisa_ser_outro_arquivo(tmp_path, creds, case):
    from app.config import ConfigError

    shared = str(tmp_path / "data" / "livestats.db")
    overrides = {
        "mesmo_caminho": {"ADMIN_DB_PATH": shared},
        "mesmo_arquivo_por_outro_nome": {"ADMIN_DB_PATH": str(tmp_path / "data" / ".." / "data" / "livestats.db")},
        "variavel_ausente": {"ADMIN_DB_PATH": None},
        "variavel_vazia": {"ADMIN_DB_PATH": ""},
    }[case]
    with pytest.raises(ConfigError):
        with running_stack(tmp_path, creds, **overrides):
            pytest.fail("a app subiu com o estado de admin no banco compartilhado")


# ------------------------------------------------------------------------------------- API-SEC-01
def test_api_sec_01_422_nunca_expoe_input(stack: Stack):
    csrf = stack.login_csrf()
    sentinel = "SENTINELA-" + secrets.token_hex(6)
    h = {"X-CSRF-Token": csrf}
    responses = [
        ("/api/admin/login", stack.new_client().post("/api/admin/login", json={"username": sentinel, "password": [sentinel]})),
        ("/api/admin/slots", stack.client.put("/api/admin/slots", json={"slots": [sentinel, "", "", ""]}, headers=h)),
        ("/api/admin/slots", stack.client.put("/api/admin/slots", json={"slots": 7, sentinel: 1}, headers=h)),
        ("/api/admin/cookies", stack.client.post("/api/admin/cookies", json=[{"name": 1, "value": sentinel}], headers=h)),
        ("/api/admin/cookies", stack.client.post("/api/admin/cookies", json=[{"name": "a", "value": "b", "domain": sentinel + " !"}], headers=h)),
        ("/api/public/lives/{slot}", stack.client.get(f"/api/public/lives/{sentinel}")),
        ("/live{n}.json", stack.client.get(f"/live{sentinel}.json")),
    ]
    for template, resp in responses:
        assert resp.status_code == 422, (template, resp.status_code, resp.text)
        body = resp.json()
        keys = set(walk_keys(body))
        assert not keys & {"input", "ctx", "url"}, (template, keys)
        for item in body["detail"]:
            assert set(item) == {"loc", "msg", "type"}
        operation = "put" if template == "/api/admin/slots" and "slots" in str(resp.request.url) else (
            "post" if template.startswith("/api/admin") else "get")
        assert_matches_openapi(resp, template, operation)


def test_api_sec_01_valor_enviado_nao_volta_no_corpo(stack: Stack):
    csrf = stack.login_csrf()
    sentinel = "ENVIADO-" + secrets.token_hex(8)
    resp = stack.client.put("/api/admin/slots", json={"slots": [sentinel, "", "", ""]}, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 422 and sentinel not in resp.text
    resp = stack.client.post("/api/admin/cookies", json=[{"name": sentinel, "value": 5}], headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 422 and sentinel not in resp.text
    resp = stack.new_client().post("/api/admin/login", json={"username": sentinel, "password": 5})
    assert resp.status_code == 422 and sentinel not in resp.text


# ------------------------------------------------------------------------------------- API-SEC-02
def test_api_sec_02_admin_responde_com_no_store(stack: Stack):
    anonymous = stack.new_client()
    checks = [
        anonymous.post("/api/admin/login", json={"username": "x", "password": "y"}),         # 401
        anonymous.get("/api/admin/session"),                                                  # 401
        anonymous.get("/api/admin/qualquer-coisa"),                                           # 401
        anonymous.post("/api/admin/login", json={}),                                          # 422
    ]
    login = stack.login()
    csrf = login.json()["csrf_token"]
    checks.append(login)                                                                      # 200
    checks += [stack.client.get(p) for p in ("/api/admin/session", "/api/admin/slots", "/api/admin/workers/status", "/api/admin/nao-existe")]
    checks.append(stack.client.put("/api/admin/slots", json={"slots": ["", "", "", ""]}, headers={"X-CSRF-Token": csrf}))
    checks.append(stack.client.put("/api/admin/slots", json={"slots": []}, headers={"X-CSRF-Token": csrf}))
    checks.append(stack.client.post("/api/admin/cookies", json=[{"name": "a", "value": "b"}], headers={"X-CSRF-Token": csrf}))
    checks.append(stack.client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf}))
    assert {r.status_code for r in checks} >= {200, 401, 422}
    for resp in checks:
        assert resp.headers.get("cache-control") == "no-store", (resp.request.url.path, resp.status_code)


# ------------------------------------------------------------------------------------- API-SEC-03
def test_api_sec_03_logs_nao_vazam_segredos(tmp_path, creds, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    wrong_password = "errada-" + secrets.token_hex(8)
    with running_stack(tmp_path, creds) as stack:
        csrf = stack.login_csrf()
        session_id = stack.client.cookies.get(SESSION_COOKIE)
        assert stack.login(ip="10.8.0.1", password=wrong_password).status_code == 401
        for n in range(5):
            stack.login(ip="10.8.0.2", username=f"u{n}", password=wrong_password)
        assert stack.login(ip="10.8.0.2").status_code == 429
        assert stack.client.get("/api/admin/slots").status_code == 200
        assert stack.client.post("/api/admin/cookies", json=[{"name": "n", "value": "v"}], headers={"X-CSRF-Token": csrf}).status_code == 200
        assert stack.client.post("/api/admin/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
    import base64

    secrets_in_play = [
        creds.password, wrong_password, creds.password_hash, session_id, csrf,
        base64.b64encode(creds.public_key_bytes).decode(), base64.b64encode(bytes(creds.private_key)).decode(),
    ]
    out = capsys.readouterr()
    haystack = caplog.text + "".join(r.getMessage() + str(r.args) for r in caplog.records) + out.out + out.err
    assert "login" in haystack  # os eventos de login SAO registrados (a varredura nao e vazia)
    for needle in secrets_in_play:
        assert needle not in haystack, needle[:8]


# ------------------------------------------------------------------------------------- API-SEC-04
def imported_modules(path: Path) -> set[str]:
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
            found.update(f"{node.module}.{alias.name}" for alias in node.names)
    return found


NETWORK_MODULES = ("httpx", "requests", "aiohttp", "urllib3", "urllib.request", "socket", "http.client", "ftplib", "smtplib", "telnetlib", "xmlrpc")


def test_api_sec_04_a_api_nao_tem_cliente_http_de_saida():
    for path in sorted(APP_DIR.glob("*.py")) + sorted(TOOLS_DIR.glob("*.py")):
        modules = imported_modules(path)
        for module in modules:
            for banned in NETWORK_MODULES:
                if module == banned or module.startswith(banned + "."):
                    # unica excecao: o healthcheck fala com o PROPRIO servidor em loopback (verificado abaixo)
                    assert path.name == "healthcheck.py" and banned == "http.client", f"{path.name} importa {module}"
        text = path.read_text(encoding="utf-8")
        for token in ("create_connection", "urlopen", "getaddrinfo", "gethostbyname"):
            assert token not in text, f"{path.name} usa {token}"

    for req in ("requirements.txt", "requirements.in"):
        names = {
            m.group(1).lower().replace("_", "-")
            for line in (REPO / "api" / req).read_text(encoding="utf-8").splitlines()
            if (m := re.match(r"^([A-Za-z0-9_.-]+)\s*(==|>=|<=|~=|$)", line))
        }
        assert names, req
        assert not names & {"httpx", "requests", "aiohttp", "urllib3", "httpcore", "certifi", "websockets"}, (req, names)


def test_api_sec_04_healthcheck_so_fala_com_loopback():
    tree = ast.parse((APP_DIR / "healthcheck.py").read_text(encoding="utf-8"))
    constants = {
        t.id: node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert constants["LOOPBACK"] == "127.0.0.1"
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "HTTPConnection"]
    assert len(calls) == 1
    first_arg = calls[0].args[0]
    assert isinstance(first_arg, ast.Name) and first_arg.id == "LOOPBACK"  # o host nunca vem de configuracao
    assert "HOST" not in (APP_DIR / "healthcheck.py").read_text(encoding="utf-8")


# ------------------------------------------------------------------------------------- API-SEC-05
def test_api_sec_05_ip_do_cliente(stack: Stack):
    assert stack.login(ip="203.0.113.9", password="errada").status_code == 401           # X-Real-IP
    assert stack.login(password="errada").status_code == 401                              # sem cabecalho: endereco da conexao
    assert stack.client.post(
        "/api/admin/login", json={"username": "x", "password": "y"}, headers={"X-Real-IP": "nao-e-um-ip"}
    ).status_code == 401                                                                  # invalido: ignorado
    assert stack.login(ip="2001:0db8:0000:0000:0000:0000:0000:0001", password="errada").status_code == 401
    with stack.admin() as conn:
        ips = [r[0] for r in conn.execute("SELECT ip FROM login_attempts ORDER BY id")]
    assert ips == ["203.0.113.9", "testclient", "testclient", "2001:db8::1"]


def test_api_sec_05_x_real_ip_forjado_nao_escapa_do_limite_por_usuario(stack: Stack):
    for n in range(5):
        assert stack.login(ip=f"198.51.100.{n + 1}", password="errada").status_code == 401
    forged = stack.login(ip="192.0.2.77")  # IP novo e senha certa, mesmo usuario
    assert forged.status_code == 429


# ------------------------------------------------------------------------------------- API-SEC-06
UNKNOWN = [("GET", "/api/admin/nao-existe"), ("POST", "/api/admin/x"), ("DELETE", "/api/admin/slots/1"),
           ("PUT", "/api/admin/slots/1/extra"), ("PATCH", "/api/admin/cookies/x"), ("GET", "/api/admin/"),
           ("GET", "/api/admin"), ("GET", "/api/admin/logout"), ("POST", "/api/admin/session"),
           ("GET", "/api/admin/../admin/nada"), ("GET", "/api/admin/%2e%2e/x"), ("GET", "/api/admin/a/b/c/d")]


def test_api_sec_06_desconhecido_sem_sessao_e_401_json(stack: Stack):
    anonymous = stack.new_client()
    for method, path in UNKNOWN:
        resp = anonymous.request(method, path)
        assert resp.status_code == 401, (method, path, resp.status_code)
        assert resp.json() == {"detail": "Não autenticado"}
        assert resp.headers["content-type"].startswith("application/json")
        assert "Traceback" not in resp.text and "<html" not in resp.text.lower()


def test_api_sec_06_desconhecido_com_sessao_e_404_json(stack: Stack):
    stack.login_csrf()
    for method, path in UNKNOWN:
        resp = stack.client.request(method, path)
        assert resp.status_code == 404, (method, path, resp.status_code)
        assert resp.json() == {"detail": "Not Found"}
        assert resp.headers["content-type"].startswith("application/json")
        assert "Traceback" not in resp.text and "<" not in resp.text
        assert_matches_openapi(resp, "/api/admin/{path}", "get")


def test_api_sec_06_sessao_expirada_volta_a_401(stack: Stack):
    stack.login_csrf()
    assert stack.client.get("/api/admin/nao-existe").status_code == 404
    stack.clock.advance(9 * 3600)
    for method, path in UNKNOWN[:4]:
        resp = stack.client.request(method, path)
        assert resp.status_code == 401 and resp.json() == {"detail": "Não autenticado"}


def test_api_sec_06_banco_indisponivel_nao_vira_500_com_traceback(stack: Stack):
    csrf = stack.login_csrf()
    Path(stack.db_path).unlink()
    for ext in ("-wal", "-shm"):
        Path(stack.db_path + ext).unlink(missing_ok=True)
    resp = stack.client.get("/api/admin/slots")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Banco de dados indisponível"}
    assert_matches_openapi(resp, "/api/admin/slots", "get")
    put = stack.client.put("/api/admin/slots", json={"slots": ["", "", "", ""]}, headers={"X-CSRF-Token": csrf})
    assert put.status_code == 503 and "Traceback" not in put.text


# ------------------------------------------------------------------------------------- API-SEC-07
FORBIDDEN_TOKENS = ("TESTING", "DISABLE_AUTH", "SKIP_CSRF", "NO_RATE_LIMIT", "DISABLE_CSRF", "DEBUG_AUTH", "PYTEST", "pytest", "unittest")
ALLOWED_ENV_NAMES = {
    "DB_PATH", "ADMIN_DB_PATH", "ADMIN_USERNAME", "PUBLIC_BASE_URL", "COOKIE_PUBLIC_KEY_FILE", "ADMIN_PASSWORD_HASH_FILE",
    "DB_SCHEMA_FILE", "ADMIN_DB_SCHEMA_FILE", "PORT",
}
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "COMMIT", "ROLLBACK", "BEGIN"}  # + SQL de transacao


def test_api_sec_07_sem_caminhos_de_teste_no_codigo_de_producao():
    files = sorted(APP_DIR.glob("*.py")) + sorted(TOOLS_DIR.glob("*.py"))
    assert len(files) >= 10
    env_like = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")
    for path in files:
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_TOKENS:
            assert token not in text, f"{path.name}: {token}"
        # usuario/senha/segredo literais atribuidos no codigo
        assert not re.search(r"(?i)\b(password|passwd|senha|secret|username|api_?key)\b\s*=\s*[\"'][^\"']", text), path.name
        # toda variavel de ambiente que o codigo conhece esta na lista do contrato (nenhuma chave liga/desliga auth)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and env_like.fullmatch(node.value):
                assert node.value in ALLOWED_ENV_NAMES | HTTP_METHODS, f"{path.name}: {node.value}"


# ------------------------------------------------------------------------------------- API-ADM-01
def test_api_adm_01_workers_status_sem_dados(stack: Stack):
    stack.login_csrf()
    resp = stack.client.get("/api/admin/workers/status")
    assert resp.status_code == 200
    body = resp.json()
    assert_matches_openapi(resp, "/api/admin/workers/status", "get")
    for name in ("lives", "inscritos"):
        worker = body["workers"][name]
        assert worker["state"] == "unknown"
        assert worker["last_heartbeat_at"] is None and worker["interval_seconds"] is None
        assert worker["consecutive_failures"] == 0
    assert body["workers"]["inscritos"]["auth_state"] == "unknown"
    assert "auth_state" not in body["workers"]["lives"]
    assert body["cookies"] == {"configured": False, "count": 0, "uploaded_at": None, "applied": False}
    assert body["subscribers"] is None
    assert body["server_time"] == "2027-01-15T08:00:00Z"


def test_api_adm_01_workers_status_reflete_o_banco(stack: Stack):
    csrf = stack.login_csrf()
    assert stack.client.post(
        "/api/admin/cookies", json=[{"name": "a", "value": "1"}, {"name": "b", "value": "2"}], headers={"X-CSRF-Token": csrf}
    ).status_code == 200
    with stack.shared() as conn:
        cookie_id = conn.execute("SELECT id FROM cookie_sets").fetchone()[0]
        conn.execute(
            "INSERT INTO worker_status (worker, state, last_heartbeat_at, last_success_at, consecutive_failures, interval_seconds, "
            "next_attempt_at, last_error_code, last_error_message) VALUES ('lives', 'degraded', ?, ?, 3, 30, ?, 'quota_exceeded', 'Cota diaria excedida')",
            (int(START_EPOCH) - 5, int(START_EPOCH) - 400, int(START_EPOCH) + 120),
        )
        conn.execute(
            "INSERT INTO worker_status (worker, state, last_heartbeat_at, last_success_at, interval_seconds, auth_state, cookies_applied_id) "
            "VALUES ('inscritos', 'ok', ?, ?, 10, 'ok', ?)",
            (int(START_EPOCH) - 2, int(START_EPOCH) - 2, cookie_id),
        )
        conn.commit()
        seed_subscribers(conn)

    resp = stack.client.get("/api/admin/workers/status")
    body = resp.json()
    assert_matches_openapi(resp, "/api/admin/workers/status", "get")
    lives, inscritos = body["workers"]["lives"], body["workers"]["inscritos"]
    assert lives["state"] == "degraded" and lives["consecutive_failures"] == 3 and lives["interval_seconds"] == 30
    assert lives["last_error_code"] == "quota_exceeded" and lives["last_error_message"] == "Cota diaria excedida"
    assert lives["last_heartbeat_at"] == "2027-01-15T07:59:55Z"
    assert lives["last_success_at"] == "2027-01-15T07:53:20Z"
    assert lives["next_attempt_at"] == "2027-01-15T08:02:00Z"
    assert inscritos["state"] == "ok" and inscritos["auth_state"] == "ok" and inscritos["interval_seconds"] == 10
    assert inscritos["last_error_code"] is None and inscritos["next_attempt_at"] is None
    assert body["cookies"]["configured"] is True and body["cookies"]["count"] == 2 and body["cookies"]["applied"] is True
    assert re.fullmatch(r"2027-01-15T08:00:00(\.\d+)?Z", body["cookies"]["uploaded_at"])
    assert body["subscribers"] == {"inscritos": "460600", "raw": "460600", "atualizado_em": "2026-11-02T14:32:10.654321Z"}

    # o worker aplicou OUTRO conjunto: applied = false
    with stack.shared() as conn:
        conn.execute("UPDATE worker_status SET cookies_applied_id = ? WHERE worker = 'inscritos'", (cookie_id + 99,))
        conn.commit()
    assert stack.client.get("/api/admin/workers/status").json()["cookies"]["applied"] is False
    with stack.shared() as conn:
        conn.execute("UPDATE worker_status SET auth_state = 'login_required', state = 'error', last_error_code = 'login_required' WHERE worker = 'inscritos'")
        conn.commit()
    changed = stack.client.get("/api/admin/workers/status").json()["workers"]["inscritos"]
    assert (changed["auth_state"], changed["state"], changed["last_error_code"]) == ("login_required", "error", "login_required")


# ------------------------------------------------------------------------------------- API-HEALTH-01
def test_api_health_01_banco_compartilhado_inacessivel_devolve_503(stack: Stack):
    assert stack.client.get("/health").status_code == 200
    Path(stack.db_path).unlink()
    for ext in ("-wal", "-shm"):
        Path(stack.db_path + ext).unlink(missing_ok=True)
    resp = stack.client.get("/health")
    assert resp.status_code == 503
    assert set(resp.json()) == {"detail"}
    assert_matches_openapi(resp, "/health", "get")
    assert stack.client.get("/live1.json").status_code == 503
    assert stack.client.get("/api/public/subscribers").status_code == 503


def test_api_health_01_banco_corrompido_ou_de_admin_ausente(stack: Stack):
    Path(stack.admin_db_path).unlink()
    for ext in ("-wal", "-shm"):
        Path(stack.admin_db_path + ext).unlink(missing_ok=True)
    assert stack.client.get("/health").status_code == 503
    assert stack.client.post("/api/admin/login", json={"username": stack.username, "password": stack.password}).status_code == 503

    Path(stack.admin_db_path).write_bytes(b"isto nao e um banco sqlite " * 40)
    assert stack.client.get("/health").status_code == 503


# ------------------------------------------------------------------------------------- API-VAL-01
def test_api_val_01_respostas_conformes_ao_openapi(stack: Stack):
    anon = stack.new_client()
    # --- publicos sem dados
    for tpl, path in (("/health", "/health"), ("/api/public/lives/{slot}", "/api/public/lives/1"),
                      ("/api/public/lives/{slot}", "/api/public/lives/7"), ("/api/public/lives/{slot}", "/api/public/lives/x"),
                      ("/api/public/subscribers", "/api/public/subscribers"), ("/live{n}.json", "/live1.json"),
                      ("/live{n}.json", "/live8.json"), ("/inscritos.json", "/inscritos.json")):
        assert_matches_openapi(anon.get(path), tpl, "get")
    # --- publicos com dados
    with stack.shared() as conn:
        seed_golden_lives(conn)
        seed_subscribers(conn)
    for tpl, path in (("/health", "/health"), ("/api/public/lives/{slot}", "/api/public/lives/4"),
                      ("/api/public/subscribers", "/api/public/subscribers"), ("/live{n}.json", "/live3.json"),
                      ("/inscritos.json", "/inscritos.json")):
        resp = anon.get(path)
        assert resp.status_code == 200
        assert_matches_openapi(resp, tpl, "get")
    # --- admin: sem sessao
    for method, tpl, path in (("get", "/api/admin/session", "/api/admin/session"), ("get", "/api/admin/slots", "/api/admin/slots"),
                              ("get", "/api/admin/workers/status", "/api/admin/workers/status"),
                              ("put", "/api/admin/slots", "/api/admin/slots"), ("post", "/api/admin/cookies", "/api/admin/cookies"),
                              ("post", "/api/admin/logout", "/api/admin/logout"), ("get", "/api/admin/{path}", "/api/admin/x")):
        resp = anon.request(method.upper(), path)
        assert resp.status_code == 401
        assert_matches_openapi(resp, tpl, method)
    # --- login
    assert_matches_openapi(anon.post("/api/admin/login", json={"username": stack.username}), "/api/admin/login", "post")
    assert_matches_openapi(anon.post("/api/admin/login", content=b"x", headers={"content-type": "application/json"}), "/api/admin/login", "post")
    assert_matches_openapi(stack.login(anon, ip="10.7.0.1", password="errada"), "/api/admin/login", "post")
    ok = stack.login(ip="10.7.0.2")
    assert ok.status_code == 200
    assert_matches_openapi(ok, "/api/admin/login", "post")
    csrf = ok.json()["csrf_token"]
    h = {"X-CSRF-Token": csrf}
    for n in range(5):
        stack.login(anon, ip="10.7.0.3", password="errada")
    blocked = stack.login(anon, ip="10.7.0.3")
    assert blocked.status_code == 429
    assert_matches_openapi(blocked, "/api/admin/login", "post")
    assert blocked.headers["retry-after"].isdigit()
    # --- admin: com sessao
    for tpl, path in (("/api/admin/session", "/api/admin/session"), ("/api/admin/slots", "/api/admin/slots"),
                      ("/api/admin/workers/status", "/api/admin/workers/status"), ("/api/admin/{path}", "/api/admin/nada")):
        resp = stack.client.get(path)
        assert resp.status_code in (200, 404)
        assert_matches_openapi(resp, tpl, "get")
    for body, status in (({"slots": ["dQw4w9WgXcQ", "", "", ""]}, 200), ({"slots": ["abc", "", "", ""]}, 422)):
        resp = stack.client.put("/api/admin/slots", json=body, headers=h)
        assert resp.status_code == status
        assert_matches_openapi(resp, "/api/admin/slots", "put")
    forbidden = stack.client.put("/api/admin/slots", json={"slots": ["", "", "", ""]})
    assert forbidden.status_code == 403
    assert_matches_openapi(forbidden, "/api/admin/slots", "put")
    for body, status in (([{"name": "a", "value": "b"}], 200), ([], 400), ([{"name": 1, "value": "b"}], 422)):
        resp = stack.client.post("/api/admin/cookies", json=body, headers=h)
        assert resp.status_code == status
        assert_matches_openapi(resp, "/api/admin/cookies", "post")
    forbidden = stack.client.post("/api/admin/cookies", json=[{"name": "a", "value": "b"}])
    assert forbidden.status_code == 403
    assert_matches_openapi(forbidden, "/api/admin/cookies", "post")
    big = stack.client.put("/api/admin/slots", json={"slots": ["", "", "", ""], "x": "y" * 300_000}, headers=h)
    assert big.status_code == 413
    assert_matches_openapi(big, "/api/admin/slots", "put")
    logout = stack.client.post("/api/admin/logout", headers=h)
    assert logout.status_code == 200
    assert_matches_openapi(logout, "/api/admin/logout", "post")
    assert_schema({"ok": True}, "Ok")
