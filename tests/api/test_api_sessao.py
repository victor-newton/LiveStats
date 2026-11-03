"""API-SES-01 .. API-SES-04 (D37): esquema v4 (cookie_sets.source e renewed_from), migracao v3 -> v4 e status sem valores."""

from __future__ import annotations

import json
import sqlite3

import pytest

from api_support import DB_DIR, Stack, assert_matches_openapi
from app import db
from test_api_cookies import make_cookies, post_cookies, sentinels

# Esquema v3 como foi entregue (so o necessario para a migracao): cookie_sets sem source/renewed_from.
V3_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
INSERT INTO schema_meta (key, value) VALUES ('schema_version', '3');
CREATE TABLE slots (
    slot INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 4),
    url TEXT NOT NULL DEFAULT '' CHECK (length(url) <= 2048),
    updated_at TEXT,
    goal INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000),
    color TEXT NOT NULL DEFAULT '#FF0033'
        CHECK (length(color) = 7 AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    font TEXT NOT NULL DEFAULT 'Roboto' CHECK (length(font) BETWEEN 1 AND 40),
    text_color TEXT NOT NULL DEFAULT '#FFFFFF'
        CHECK (length(text_color) = 7 AND text_color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]')
) STRICT;
INSERT INTO slots (slot, url) VALUES (1, 'dQw4w9WgXcQ'), (2, ''), (3, ''), (4, '');
CREATE TABLE cookie_sets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ciphertext   BLOB    NOT NULL CHECK (length(ciphertext) BETWEEN 49 AND 1048576),
    key_id       TEXT    NOT NULL CHECK (length(key_id) = 16),
    cookie_count INTEGER NOT NULL CHECK (cookie_count BETWEEN 1 AND 200),
    uploaded_by  TEXT    NOT NULL CHECK (length(uploaded_by) <= 64),
    created_at   TEXT    NOT NULL
        CHECK (created_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T*Z')
) STRICT;
INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at)
VALUES (zeroblob(60), '0123456789abcdef', 3, 'victor', '2026-11-01T10:00:00Z');
"""


def cols(conn: sqlite3.Connection) -> list[tuple]:
    return [(r[1], r[2], r[3], r[4]) for r in conn.execute("PRAGMA table_info(cookie_sets)")]


def test_api_ses_01_banco_novo_nasce_na_v4_com_source_e_renewed_from(tmp_path):
    path = tmp_path / "livestats.db"
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone() == ("4",)
    assert cols(conn)[-2:] == [("source", "TEXT", 1, "'admin'"), ("renewed_from", "INTEGER", 0, None)]
    ok = "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at, source) VALUES (zeroblob(60), '0123456789abcdef', 1, 'x', '2026-01-01T00:00:00Z', ?)"
    conn.execute(ok, ("worker",))
    conn.execute(ok, ("admin",))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(ok, ("intruso",))
    conn.close()


def test_api_ses_02_migracao_v3_para_v4_idempotente_e_sem_perder_dados(tmp_path):
    path = tmp_path / "livestats.db"
    conn = sqlite3.connect(path)
    conn.executescript(V3_SQL)
    conn.close()

    snapshots = []
    for _ in range(3):  # a primeira migra; as outras nao mudam nada
        db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
        conn = sqlite3.connect(path)
        snapshots.append((
            conn.execute("SELECT id, key_id, cookie_count, uploaded_by, created_at, source, renewed_from FROM cookie_sets").fetchall(),
            conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchall(),
            cols(conn),
        ))
        conn.close()
    assert snapshots[0] == snapshots[1] == snapshots[2]
    linhas, versao, colunas = snapshots[0]
    assert linhas == [(1, "0123456789abcdef", 3, "victor", "2026-11-01T10:00:00Z", "admin", None)]
    assert versao == [("4",)]
    assert [c[0] for c in colunas] == ["id", "ciphertext", "key_id", "cookie_count", "uploaded_by", "created_at", "source", "renewed_from"]
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT slot, url FROM slots ORDER BY slot").fetchall() == [(1, "dQw4w9WgXcQ"), (2, ""), (3, ""), (4, "")]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE cookie_sets SET source = 'x'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE cookie_sets SET renewed_from = 0")
    conn.close()


def test_api_ses_03_upload_do_admin_grava_source_admin(stack: Stack):
    csrf = stack.login_csrf()
    assert post_cookies(stack, csrf, make_cookies(2)).status_code == 200
    with stack.shared() as conn:
        assert [tuple(r) for r in conn.execute("SELECT source, renewed_from FROM cookie_sets")] == [("admin", None)]


def test_api_ses_04_status_traz_origem_data_e_renewed_from_sem_nenhum_valor_de_cookie(stack: Stack):
    csrf = stack.login_csrf()
    vazio = stack.client.get("/api/admin/workers/status").json()["cookies"]
    assert vazio == {
        "configured": False, "count": 0, "uploaded_at": None, "applied": False,
        "source": None, "created_at": None, "renewed_from": None,
    }

    cookies = make_cookies(3)
    assert post_cookies(stack, csrf, cookies).status_code == 200
    resp = stack.client.get("/api/admin/workers/status")
    assert_matches_openapi(resp, "/api/admin/workers/status", "get")
    c = resp.json()["cookies"]
    assert c["source"] == "admin" and c["renewed_from"] is None and c["created_at"] == c["uploaded_at"] and c["count"] == 3

    # o worker renova: nova linha source='worker', renewed_from = id anterior (INSERT + DELETE, como faz o worker)
    with stack.shared() as conn:
        antigo = conn.execute("SELECT id FROM cookie_sets").fetchone()[0]
        conn.execute(
            "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at, source, renewed_from) "
            "VALUES (zeroblob(60), '0123456789abcdef', 4, 'worker', '2027-01-15T07:00:00Z', 'worker', ?)", (antigo,),
        )
        conn.execute("DELETE FROM cookie_sets WHERE id = ?", (antigo,))
        conn.commit()
    resp = stack.client.get("/api/admin/workers/status")
    assert_matches_openapi(resp, "/api/admin/workers/status", "get")
    c = resp.json()["cookies"]
    assert (c["source"], c["renewed_from"], c["created_at"], c["count"]) == ("worker", antigo, "2027-01-15T07:00:00Z", 4)
    corpo = json.dumps(resp.json())
    for needle in sentinels(cookies):
        assert needle not in corpo
    assert "ciphertext" not in corpo
