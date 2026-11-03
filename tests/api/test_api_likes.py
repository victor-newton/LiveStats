"""API-LIKE-01 .. API-LIKE-06 (D34): meta e cor por slot, migracao v1 -> v2 e formato publico."""

from __future__ import annotations

import sqlite3

import pytest

from api_support import (
    BLANK_SLOTS,
    DB_DIR,
    DEFAULT_COLOR,
    GOLDEN,
    GOLDEN_FILES,
    Stack,
    assert_matches_openapi,
    assert_schema,
    running_stack,
    seed_golden_lives,
    slot_item,
    slot_items,
    walk_keys,
)
from app import db

# Esquema v1 como foi entregue (so o que a migracao precisa existir antes dela): slots sem goal/color.
V1_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
INSERT INTO schema_meta (key, value) VALUES ('schema_version', '1');
CREATE TABLE slots (
    slot       INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 4),
    url        TEXT    NOT NULL DEFAULT '' CHECK (length(url) <= 2048),
    updated_at TEXT
) STRICT;
INSERT INTO slots (slot, url, updated_at) VALUES
    (1, 'dQw4w9WgXcQ', '2026-11-02T10:00:00Z'), (2, '', NULL),
    (3, 'https://youtu.be/9bZkp7q19f0', '2026-11-02T10:00:00Z'), (4, '', NULL);
"""


FONT_DEFAULTS = {"font": "Roboto", "text_color": "#FFFFFF"}


def columns(conn: sqlite3.Connection, table: str = "slots") -> list[tuple]:
    return [(r[1], r[2], r[3]) for r in conn.execute(f"PRAGMA table_info({table})")]


def make_v1(path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(V1_SQL)
    conn.close()


def put(stack: Stack, csrf: str, slots, **kwargs):
    return stack.client.put("/api/admin/slots", json={"slots": slots}, headers={"X-CSRF-Token": csrf}, **kwargs)


def db_rows(stack: Stack):
    with stack.shared() as conn:
        return [tuple(r) for r in conn.execute("SELECT slot, url, goal, color, updated_at FROM slots ORDER BY slot")]


# ------------------------------------------------------------------------------------- API-LIKE-01
def test_api_like_01_banco_novo_nasce_na_v2_com_goal_e_color(tmp_path):
    path = tmp_path / "livestats.db"
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone() == ("4",)
    assert columns(conn) == [
        ("slot", "INTEGER", 0), ("url", "TEXT", 1), ("updated_at", "TEXT", 0), ("goal", "INTEGER", 0), ("color", "TEXT", 1),
        ("font", "TEXT", 1), ("text_color", "TEXT", 1),
    ]
    assert conn.execute("SELECT slot, goal, color FROM slots ORDER BY slot").fetchall() == [
        (n, None, DEFAULT_COLOR) for n in (1, 2, 3, 4)
    ]
    conn.close()


@pytest.mark.parametrize("sql", [
    "UPDATE slots SET color = '#ff0033' WHERE slot = 1",  # minuscula: o banco so guarda maiusculas
    "UPDATE slots SET color = 'red' WHERE slot = 1",
    "UPDATE slots SET color = '#FF0033;x' WHERE slot = 1",
    "UPDATE slots SET color = NULL WHERE slot = 1",
    "UPDATE slots SET goal = 0 WHERE slot = 1",
    "UPDATE slots SET goal = 1000000001 WHERE slot = 1",
    "UPDATE slots SET goal = 'abc' WHERE slot = 1",
])
def test_api_like_01_o_proprio_banco_recusa_valores_invalidos(tmp_path, sql):
    path = tmp_path / "livestats.db"
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute(sql)
    conn.close()


# ------------------------------------------------------------------------------------- API-LIKE-02
def test_api_like_02_migracao_v1_para_v2_preserva_dados_e_e_idempotente(tmp_path):
    path = tmp_path / "livestats.db"
    make_v1(path)
    conn = sqlite3.connect(path)
    assert [c[0] for c in columns(conn)] == ["slot", "url", "updated_at"]
    conn.close()

    snapshots = []
    for _ in range(3):  # a primeira migra; as outras nao mudam nada
        db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
        conn = sqlite3.connect(path)
        snapshots.append((
            conn.execute("SELECT slot, url, updated_at, goal, color FROM slots ORDER BY slot").fetchall(),
            conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchall(),
            columns(conn),
        ))
        conn.close()
    rows, version, cols = snapshots[0]
    assert rows == [
        (1, "dQw4w9WgXcQ", "2026-11-02T10:00:00Z", None, DEFAULT_COLOR),
        (2, "", None, None, DEFAULT_COLOR),
        (3, "https://youtu.be/9bZkp7q19f0", "2026-11-02T10:00:00Z", None, DEFAULT_COLOR),
        (4, "", None, None, DEFAULT_COLOR),
    ]
    assert version == [("3",)]
    assert [c[0] for c in cols] == ["slot", "url", "updated_at", "goal", "color", "font", "text_color"]
    assert snapshots[1] == snapshots[0] and snapshots[2] == snapshots[0]


def test_api_like_02_migracao_nao_sobrescreve_meta_e_cor_ja_gravadas(tmp_path):
    path = tmp_path / "livestats.db"
    make_v1(path)
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE slots SET goal = 5000, color = '#00AA11' WHERE slot = 2")
    conn.commit()
    conn.close()
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))  # subida seguinte
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT goal, color FROM slots WHERE slot = 2").fetchone() == (5000, "#00AA11")
    conn.close()


def test_api_like_02_a_app_sobe_sobre_banco_v1_e_atende_o_painel(tmp_path, creds):
    (tmp_path / "data").mkdir()
    make_v1(tmp_path / "data" / "livestats.db")
    with running_stack(tmp_path, creds) as stack:
        csrf = stack.login_csrf()
        body = stack.client.get("/api/admin/slots").json()
        assert [(s["url"], s["goal"], s["color"]) for s in body["slots"]] == [
            ("dQw4w9WgXcQ", None, DEFAULT_COLOR), ("", None, DEFAULT_COLOR),
            ("https://youtu.be/9bZkp7q19f0", None, DEFAULT_COLOR), ("", None, DEFAULT_COLOR),
        ]
        assert put(stack, csrf, [slot_item("dQw4w9WgXcQ", 10000, "#12ab34"), *BLANK_SLOTS[1:]]).status_code == 200
        with stack.shared() as conn:
            assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()[0] == "4"


def test_api_like_02_workers_continuam_lendo_slots_como_antes(stack: Stack):
    csrf = stack.login_csrf()
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 777, "#0000FF"), *slot_items("", "jNQXAC9IVRw", "")])
    with stack.shared() as worker_view:  # a mesma consulta do worker-lives
        assert [tuple(r) for r in worker_view.execute("SELECT slot, url FROM slots ORDER BY slot")] == [
            (1, "dQw4w9WgXcQ"), (2, ""), (3, "jNQXAC9IVRw"), (4, ""),
        ]


# ------------------------------------------------------------------------------------- API-LIKE-03
def test_api_like_03_get_em_banco_novo_traz_goal_e_color_padrao(stack: Stack):
    stack.login_csrf()
    resp = stack.client.get("/api/admin/slots")
    assert resp.status_code == 200
    assert resp.json() == {
        "slots": [
            {"slot": n, "url": "", "goal": None, "color": DEFAULT_COLOR, "font": "Roboto", "text_color": "#FFFFFF", "stats": None}
            for n in (1, 2, 3, 4)
        ],
        "saved_at": None,
    }
    assert_matches_openapi(resp, "/api/admin/slots", "get")


def test_api_like_03_put_grava_url_meta_e_cor_normalizada_em_maiusculas(stack: Stack):
    csrf = stack.login_csrf()
    items = [
        slot_item("  dQw4w9WgXcQ ", 10000, "#ff00aa"),
        slot_item("", None, "#0a0B0c"),
        slot_item("https://youtu.be/9bZkp7q19f0", 1, "#ABCDEF"),
        slot_item("jNQXAC9IVRw", 1_000_000_000, "#000000"),
    ]
    resp = put(stack, csrf, items)
    assert resp.status_code == 200, resp.text
    expected = [
        {"url": "dQw4w9WgXcQ", "goal": 10000, "color": "#FF00AA", **FONT_DEFAULTS},
        {"url": "", "goal": None, "color": "#0A0B0C", **FONT_DEFAULTS},
        {"url": "https://youtu.be/9bZkp7q19f0", "goal": 1, "color": "#ABCDEF", **FONT_DEFAULTS},
        {"url": "jNQXAC9IVRw", "goal": 1_000_000_000, "color": "#000000", **FONT_DEFAULTS},
    ]
    body = resp.json()
    assert body["ok"] is True and body["slots"] == expected and body["saved_at"]
    assert_matches_openapi(resp, "/api/admin/slots", "put")
    assert [(r[1], r[2], r[3]) for r in db_rows(stack)] == [(e["url"], e["goal"], e["color"]) for e in expected]
    assert all(r[4] == body["saved_at"] for r in db_rows(stack))

    got = stack.client.get("/api/admin/slots").json()
    assert [{k: s[k] for k in ("url", "goal", "color", "font", "text_color")} for s in got["slots"]] == expected
    assert_schema(got, "SlotsRead")


def test_api_like_03_meta_pode_voltar_a_nula(stack: Stack):
    csrf = stack.login_csrf()
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 50, "#112233"), *BLANK_SLOTS[1:]])
    assert put(stack, csrf, [slot_item("dQw4w9WgXcQ", None, "#112233"), *BLANK_SLOTS[1:]]).status_code == 200
    assert db_rows(stack)[0][2] is None


# ------------------------------------------------------------------------------------- API-LIKE-04
BAD_COLORS = [
    "red", "#FFF", "#GGGGGG", "url(x)", "expression()", "#FF0033;x", "#FF0033; background:url(x)",
    "FF0033", "#FF00331", "#FF003", " #FF0033", "#FF0033 ", "#FF0033\n", "#FF0033\x00", "rgb(1,2,3)",
    "var(--x)", "#ＦＦ" + "0033", "", "</style><script>", "#FF0033'--",
]
BAD_COLOR_TYPES = [None, 255, 1.5, True, ["#FF0033"], {"c": "#FF0033"}]
BAD_GOALS = [0, -1, 1_000_000_001, 10**30, 1.5, 5.0, "100", "", "1e3", True, False, [], {}, [1]]


def assert_rejected(stack: Stack, csrf: str, payload, baseline):
    resp = stack.client.put("/api/admin/slots", json=payload, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 422, (payload, resp.text)
    assert "input" not in set(walk_keys(resp.json()))
    assert_matches_openapi(resp, "/api/admin/slots", "put")
    assert db_rows(stack) == baseline  # nada mudou, nem o updated_at
    return resp


@pytest.mark.parametrize("color", BAD_COLORS + BAD_COLOR_TYPES, ids=repr)
def test_api_like_04_cor_invalida_devolve_422_sem_ecoar_nem_gravar(stack: Stack, color):
    csrf = stack.login_csrf()
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 10, "#112233"), *BLANK_SLOTS[1:]])
    baseline = db_rows(stack)
    for position in range(4):
        items = [dict(i) for i in BLANK_SLOTS]
        items[position]["color"] = color
        resp = assert_rejected(stack, csrf, {"slots": items}, baseline)
        assert resp.json()["detail"][0]["loc"] == ["body", "slots", position, "color"]
        if isinstance(color, str) and len(color) > 3:
            assert color not in resp.text


@pytest.mark.parametrize("goal", BAD_GOALS, ids=repr)
def test_api_like_04_meta_invalida_devolve_422_e_nao_grava(stack: Stack, goal):
    csrf = stack.login_csrf()
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 10, "#112233"), *BLANK_SLOTS[1:]])
    baseline = db_rows(stack)
    for position in range(4):
        items = [dict(i) for i in BLANK_SLOTS]
        items[position]["goal"] = goal
        resp = assert_rejected(stack, csrf, {"slots": items}, baseline)
        assert resp.json()["detail"][0]["loc"] == ["body", "slots", position, "goal"]


@pytest.mark.parametrize(
    "payload",
    [
        {"slots": ["", "", "", ""]},  # formato antigo (lista de textos)
        {"slots": ["dQw4w9WgXcQ", "", "", ""]},
        {"slots": [{"url": ""}, *BLANK_SLOTS[1:]]},  # faltam goal e color
        {"slots": [{"url": "", "goal": None}, *BLANK_SLOTS[1:]]},
        {"slots": [{"goal": None, "color": DEFAULT_COLOR}, *BLANK_SLOTS[1:]]},
        {"slots": [{**BLANK_SLOTS[0], "extra": 1}, *BLANK_SLOTS[1:]]},
        {"slots": [{**BLANK_SLOTS[0], "url": 5}, *BLANK_SLOTS[1:]]},
        {"slots": [{**BLANK_SLOTS[0], "url": "abc"}, *BLANK_SLOTS[1:]]},
        {"slots": [None, *BLANK_SLOTS[1:]]},
        {"slots": BLANK_SLOTS[:3]},
        {"slots": BLANK_SLOTS + BLANK_SLOTS[:1]},
        {"slots": BLANK_SLOTS, "extra": 1},
    ],
    ids=lambda p: str(p)[:60],
)
def test_api_like_04_formato_do_corpo_invalido_devolve_422(stack: Stack, payload):
    csrf = stack.login_csrf()
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 10, "#112233"), *BLANK_SLOTS[1:]])
    assert_rejected(stack, csrf, payload, db_rows(stack))


def test_api_like_04_um_item_invalido_nao_grava_os_outros(stack: Stack):
    csrf = stack.login_csrf()
    baseline = db_rows(stack)
    items = [slot_item("dQw4w9WgXcQ", 99, "#ABCDEF"), slot_item("", 5, "#123456"), slot_item("", None, "red"), slot_item()]
    assert_rejected(stack, csrf, {"slots": items}, baseline)


# ------------------------------------------------------------------------------------- API-LIKE-05
def test_api_like_05_publico_ganha_goal_e_color_no_fim_do_objeto(stack: Stack):
    csrf = stack.login_csrf()
    with stack.shared() as conn:
        seed_golden_lives(conn)
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 10000, "#1a2b3c"), *BLANK_SLOTS[1:]])
    resp = stack.client.get("/api/public/lives/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["goal"] == 10000 and body["color"] == "#1A2B3C" and body["likes"] == 56
    assert list(body)[-4:] == ["goal", "color", "font", "text_color"]
    assert_schema(body, "PublicLive")
    assert_matches_openapi(resp, "/api/public/lives/{slot}", "get")

    other = stack.client.get("/api/public/lives/2").json()  # slot sem meta
    assert other["goal"] is None and other["color"] == DEFAULT_COLOR
    assert_schema(other, "PublicLive")


def test_api_like_05_sem_dados_do_worker_continua_503(stack: Stack):
    put(stack, stack.login_csrf(), [slot_item("dQw4w9WgXcQ", 10, "#112233"), *BLANK_SLOTS[1:]])
    assert stack.client.get("/api/public/lives/1").status_code == 503


# ------------------------------------------------------------------------------------- API-LIKE-06
@pytest.mark.parametrize("slot", [1, 2, 3, 4])
def test_api_like_06_legados_continuam_byte_a_byte_iguais_com_meta_e_cor(stack: Stack, slot):
    csrf = stack.login_csrf()
    with stack.shared() as conn:
        seed_golden_lives(conn)
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 123456, "#00FF00") for _ in range(4)])
    resp = stack.client.get(f"/live{slot}.json")
    assert resp.status_code == 200
    assert resp.content == (GOLDEN / GOLDEN_FILES[slot]).read_bytes()
    assert b"goal" not in resp.content and b"color" not in resp.content


def test_api_like_06_stats_do_painel_nao_ganham_goal_nem_color(stack: Stack):
    csrf = stack.login_csrf()
    with stack.shared() as conn:
        seed_golden_lives(conn)
    put(stack, csrf, [slot_item("dQw4w9WgXcQ", 1, "#000000"), *BLANK_SLOTS[1:]])
    for item in stack.client.get("/api/admin/slots").json()["slots"]:
        assert set(item["stats"]) == {
            "slot", "ativo", "video_id", "titulo", "status", "views", "likes", "views_formatado", "likes_formatado", "atualizado_em",
        }
