"""C11 (D35): esquema v3, configuracoes do canal e aparencia (fonte e cor do texto) dos overlays.

IDs: API-SET-01..08 (ver docs/test-plan.md).
"""

from __future__ import annotations

import sqlite3

import pytest

from api_support import (
    BLANK_SLOTS,
    DB_DIR,
    Stack,
    assert_matches_openapi,
    assert_schema,
    seed_golden_lives,
    seed_subscribers,
    slot_item,
    walk_keys,
)
from app import db
from app.fonts import DEFAULT_FONT, FONTS

CHANNEL = "UC" + "a1B2c3D4e5F6g7H8i9J0k_"  # 24 caracteres
assert len(CHANNEL) == 24

V2_SQL = """
PRAGMA journal_mode = WAL;
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL) STRICT;
INSERT INTO schema_meta (key, value) VALUES ('schema_version', '2');
CREATE TABLE slots (
    slot       INTEGER PRIMARY KEY CHECK (slot BETWEEN 1 AND 4),
    url        TEXT    NOT NULL DEFAULT '' CHECK (length(url) <= 2048),
    updated_at TEXT,
    goal       INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000),
    color      TEXT    NOT NULL DEFAULT '#FF0033'
        CHECK (length(color) = 7 AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]')
) STRICT;
INSERT INTO slots (slot, url, updated_at, goal, color) VALUES
    (1, 'dQw4w9WgXcQ', '2026-11-02T10:00:00Z', 5000, '#00AA11'), (2, '', NULL, NULL, '#FF0033'),
    (3, 'https://youtu.be/9bZkp7q19f0', '2026-11-02T10:00:00Z', NULL, '#123456'), (4, '', NULL, NULL, '#FF0033');
"""

INJECTIONS = [
    "Arial; } body{", "url(x)", '"Arial"', "'Arial'", "Arial, sans-serif", "arial", "ARIAL", " Arial", "Arial ",
    "Arial\n", "Arial\x00", "Comic Sans MS", "", "</style><script>", "Roboto;color:red", "inherit", "var(--x)",
    "Segoe  UI", "Ａrial",
]
BAD_FONT_TYPES = [None, 1, 1.5, True, ["Arial"], {"f": "Arial"}]
BAD_CHANNELS = [
    "UC" + "a" * 21, "UC" + "a" * 23, "UC" + "a" * 22 + "\n", "uc" + "a" * 22, "UB" + "a" * 22, "UC" + "a" * 21 + "!",
    "UC" + "a" * 21 + " ", " UC" + "a" * 22, "UC" + "a" * 21 + "/", "UC" + "a" * 21 + "é", "", "UC", "x" * 200,
    "UC" + "a" * 20 + "<>", "UC" + "a" * 22 + ";DROP TABLE settings",
]


def columns(conn: sqlite3.Connection, table: str = "slots") -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]


def put_settings(stack: Stack, csrf: str, body, **kw):
    return stack.client.put("/api/admin/settings", json=body, headers={"X-CSRF-Token": csrf}, **kw)


def put_slots(stack: Stack, csrf: str, items):
    return stack.client.put("/api/admin/slots", json={"slots": items}, headers={"X-CSRF-Token": csrf})


def stored_settings(stack: Stack) -> list[tuple]:
    with stack.shared() as conn:
        return [tuple(r) for r in conn.execute("SELECT key, value FROM settings ORDER BY key")]


def slot_looks(stack: Stack) -> list[tuple]:
    with stack.shared() as conn:
        return [tuple(r) for r in conn.execute("SELECT slot, font, text_color FROM slots ORDER BY slot")]


# ------------------------------------------------------------------------------------- API-SET-01: migracao
def test_api_set_01_migracao_v2_para_v3_preserva_dados_e_e_idempotente(tmp_path):
    path = tmp_path / "livestats.db"
    conn = sqlite3.connect(path)
    conn.executescript(V2_SQL)
    conn.close()

    snapshots = []
    for _ in range(3):
        db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
        conn = sqlite3.connect(path)
        snapshots.append((
            conn.execute("SELECT slot, url, updated_at, goal, color, font, text_color FROM slots ORDER BY slot").fetchall(),
            conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchall(),
            columns(conn), columns(conn, "settings"),
            conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0],
        ))
        conn.close()
    rows, version, cols, settings_cols, n_settings = snapshots[0]
    assert rows == [
        (1, "dQw4w9WgXcQ", "2026-11-02T10:00:00Z", 5000, "#00AA11", "Roboto", "#FFFFFF"),
        (2, "", None, None, "#FF0033", "Roboto", "#FFFFFF"),
        (3, "https://youtu.be/9bZkp7q19f0", "2026-11-02T10:00:00Z", None, "#123456", "Roboto", "#FFFFFF"),
        (4, "", None, None, "#FF0033", "Roboto", "#FFFFFF"),
    ]
    assert version == [("3",)]
    assert cols == ["slot", "url", "updated_at", "goal", "color", "font", "text_color"]
    assert settings_cols == ["key", "value"] and n_settings == 0
    assert snapshots[1] == snapshots[0] and snapshots[2] == snapshots[0]


def test_api_set_01_migracao_nao_sobrescreve_o_que_ja_foi_gravado(tmp_path):
    path = tmp_path / "livestats.db"
    conn = sqlite3.connect(path)
    conn.executescript(V2_SQL)
    conn.close()
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    conn.execute("UPDATE slots SET font = 'Impact', text_color = '#00FF00' WHERE slot = 2")
    conn.execute("INSERT INTO settings (key, value) VALUES ('channel_id', ?)", (CHANNEL,))
    conn.commit()
    conn.close()
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT font, text_color FROM slots WHERE slot = 2").fetchone() == ("Impact", "#00FF00")
    assert conn.execute("SELECT key, value FROM settings").fetchall() == [("channel_id", CHANNEL)]
    conn.close()


def test_api_set_01_tabela_settings_so_aceita_as_chaves_da_lista_fechada(tmp_path):
    path = tmp_path / "livestats.db"
    db.bootstrap_schema(str(path), str(DB_DIR / "schema.sql"))
    conn = sqlite3.connect(path)
    for key in ("channel_id", "subs_font", "subs_text_color"):
        conn.execute("INSERT INTO settings (key, value) VALUES (?, 'x')", (key,))
    for key in ("outra", "", "CHANNEL_ID"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO settings (key, value) VALUES (?, 'x')", (key,))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE settings SET value = '' WHERE key = 'channel_id'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE slots SET text_color = '#fff' WHERE slot = 1")
    conn.close()


# ------------------------------------------------------------------------------------- API-SET-02: leitura/escrita
def test_api_set_02_get_em_banco_novo_traz_padroes(stack: Stack):
    stack.login_csrf()
    resp = stack.client.get("/api/admin/settings")
    assert resp.status_code == 200
    assert resp.json() == {"channel_id": None, "subs_font": "Roboto", "subs_text_color": "#FFFFFF"}
    assert_matches_openapi(resp, "/api/admin/settings", "get")
    assert_schema(resp.json(), "Settings")


def test_api_set_02_put_grava_normaliza_e_get_devolve_o_mesmo(stack: Stack):
    csrf = stack.login_csrf()
    resp = put_settings(stack, csrf, {"channel_id": CHANNEL, "subs_font": "Segoe UI Black", "subs_text_color": "#a1b2c3"})
    assert resp.status_code == 200, resp.text
    expected = {"channel_id": CHANNEL, "subs_font": "Segoe UI Black", "subs_text_color": "#A1B2C3"}
    assert resp.json() == {"ok": True, **expected}
    assert_matches_openapi(resp, "/api/admin/settings", "put")
    assert stack.client.get("/api/admin/settings").json() == expected
    assert stored_settings(stack) == sorted(expected.items())


def test_api_set_02_put_parcial_mantem_o_resto(stack: Stack):
    csrf = stack.login_csrf()
    put_settings(stack, csrf, {"channel_id": CHANNEL, "subs_font": "Impact"})
    resp = put_settings(stack, csrf, {"subs_text_color": "#010203"})
    assert resp.json() == {"ok": True, "channel_id": CHANNEL, "subs_font": "Impact", "subs_text_color": "#010203"}
    other = "UC" + "Z" * 22
    assert put_settings(stack, csrf, {"channel_id": other}).json()["channel_id"] == other
    assert dict(stored_settings(stack))["channel_id"] == other
    assert len(stored_settings(stack)) == 3  # upsert: nunca duplica


# ------------------------------------------------------------------------------------- API-SET-03: validacao
def assert_settings_rejected(stack: Stack, csrf: str, payload, field: str | None = None):
    before = stored_settings(stack)
    resp = put_settings(stack, csrf, payload)
    assert resp.status_code == 422, (payload, resp.text)
    assert "input" not in set(walk_keys(resp.json()))
    assert_matches_openapi(resp, "/api/admin/settings", "put")
    assert stored_settings(stack) == before
    if field:
        assert resp.json()["detail"][0]["loc"] == ["body", field]
    return resp


@pytest.mark.parametrize("channel", BAD_CHANNELS + [None, 5, 1.5, True, ["x"], {"a": 1}], ids=repr)
def test_api_set_03_canal_invalido_devolve_422_sem_gravar(stack: Stack, channel):
    csrf = stack.login_csrf()
    put_settings(stack, csrf, {"channel_id": CHANNEL})
    resp = assert_settings_rejected(stack, csrf, {"channel_id": channel})
    if isinstance(channel, str) and len(channel) > 8:
        assert channel not in resp.text
    assert stored_settings(stack) == [("channel_id", CHANNEL)]


@pytest.mark.parametrize("font", INJECTIONS + BAD_FONT_TYPES, ids=repr)
def test_api_set_03_fonte_invalida_do_contador_devolve_422_sem_gravar(stack: Stack, font):
    csrf = stack.login_csrf()
    resp = assert_settings_rejected(stack, csrf, {"subs_font": font}, "subs_font")
    if isinstance(font, str) and len(font) > 8:
        assert font not in resp.text
    assert stored_settings(stack) == []


@pytest.mark.parametrize("color", ["red", "#FFF", "url(x)", "#FF0033;x", "#FF0033\n", "FF0033", "", None, 5, "</style>"], ids=repr)
def test_api_set_03_cor_invalida_do_contador_devolve_422(stack: Stack, color):
    csrf = stack.login_csrf()
    assert_settings_rejected(stack, csrf, {"subs_text_color": color}, "subs_text_color")


@pytest.mark.parametrize("body", [{}, {"channel_id": None}, {"extra": 1}, {"channel_id": CHANNEL, "extra": 1}, [], "x", 5])
def test_api_set_03_corpo_vazio_desconhecido_ou_de_tipo_errado_devolve_422(stack: Stack, body):
    csrf = stack.login_csrf()
    assert_settings_rejected(stack, csrf, body)


def test_api_set_03_um_campo_ruim_impede_gravar_os_bons(stack: Stack):
    csrf = stack.login_csrf()
    assert_settings_rejected(stack, csrf, {"channel_id": CHANNEL, "subs_font": "Arial; } body{"})
    assert stored_settings(stack) == []


# ------------------------------------------------------------------------------------- API-SET-04: fontes
def test_api_set_04_fonts_expoe_a_lista_fechada_unica(stack: Stack):
    stack.login_csrf()
    resp = stack.client.get("/api/admin/fonts")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"fonts": list(FONTS), "default": DEFAULT_FONT}
    assert_matches_openapi(resp, "/api/admin/fonts", "get")
    assert_schema(body, "Fonts")
    assert len(set(FONTS)) == len(FONTS)
    for needed in ("Roboto", "Segoe UI", "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Candara", "Consolas",
                   "Constantia", "Corbel", "Franklin Gothic Medium", "Georgia", "Impact", "Lucida Console",
                   "Palatino Linotype", "Segoe UI Black", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana"):
        assert needed in FONTS
    assert all(f.isascii() and not set(f) & set(";{}()\"'\\<>/:,") for f in FONTS)  # nada que sirva de CSS


def test_api_set_04_toda_fonte_da_lista_e_aceita_no_contador_e_nos_slots(stack: Stack):
    csrf = stack.login_csrf()
    for font in FONTS:
        assert put_settings(stack, csrf, {"subs_font": font}).json()["subs_font"] == font
        items = [slot_item(font=font, text_color="#102030") for _ in range(4)]
        assert put_slots(stack, csrf, items).status_code == 200
    assert slot_looks(stack) == [(n, FONTS[-1], "#102030") for n in (1, 2, 3, 4)]


# ------------------------------------------------------------------------------------- API-SET-05: slots font/text_color
@pytest.mark.parametrize("font", INJECTIONS + BAD_FONT_TYPES, ids=repr)
def test_api_set_05_slot_com_fonte_invalida_devolve_422_sem_gravar(stack: Stack, font):
    csrf = stack.login_csrf()
    put_slots(stack, csrf, [slot_item("dQw4w9WgXcQ", font="Impact"), *BLANK_SLOTS[1:]])
    before = slot_looks(stack)
    for position in range(4):
        items = [dict(i) for i in BLANK_SLOTS]
        items[position]["font"] = font
        resp = put_slots(stack, csrf, items)
        assert resp.status_code == 422, (font, resp.text)
        assert resp.json()["detail"][0]["loc"] == ["body", "slots", position, "font"]
        assert "input" not in set(walk_keys(resp.json()))
        assert_matches_openapi(resp, "/api/admin/slots", "put")
        assert slot_looks(stack) == before


@pytest.mark.parametrize("color", ["red", "#FFF", "url(x)", "#FF0033;x", "#FF0033\n", "", None, 7, "expression()"], ids=repr)
def test_api_set_05_slot_com_cor_de_texto_invalida_devolve_422_sem_gravar(stack: Stack, color):
    csrf = stack.login_csrf()
    before = slot_looks(stack)
    items = [dict(i) for i in BLANK_SLOTS]
    items[2]["text_color"] = color
    resp = put_slots(stack, csrf, items)
    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["body", "slots", 2, "text_color"]
    assert slot_looks(stack) == before


def test_api_set_05_put_de_slots_grava_fonte_e_cor_por_slot_e_normaliza(stack: Stack):
    csrf = stack.login_csrf()
    fonts = ["Impact", "Georgia", "Roboto", "Consolas"]
    colors = ["#aabbcc", "#000000", "#FfFfFf", "#123abc"]
    items = [slot_item("", None, "#FF0033", f, c) for f, c in zip(fonts, colors)]
    resp = put_slots(stack, csrf, items)
    assert resp.status_code == 200, resp.text
    assert [(s["font"], s["text_color"]) for s in resp.json()["slots"]] == list(zip(fonts, [c.upper() for c in colors]))
    assert_matches_openapi(resp, "/api/admin/slots", "put")
    got = stack.client.get("/api/admin/slots").json()
    assert [(s["font"], s["text_color"]) for s in got["slots"]] == list(zip(fonts, [c.upper() for c in colors]))
    assert_schema(got, "SlotsRead")


def test_api_set_05_omitir_font_e_text_color_mantem_o_que_estava(stack: Stack):
    csrf = stack.login_csrf()
    put_slots(stack, csrf, [slot_item("dQw4w9WgXcQ", font="Impact", text_color="#112233"), *BLANK_SLOTS[1:]])
    legacy_item = {"url": "jNQXAC9IVRw", "goal": None, "color": "#FF0033"}  # formato da D34, sem aparencia
    resp = put_slots(stack, csrf, [legacy_item, *BLANK_SLOTS[1:]])
    assert resp.status_code == 200, resp.text
    assert slot_looks(stack)[0] == (1, "Impact", "#112233")
    assert resp.json()["slots"][0]["font"] == "Impact"
    assert_matches_openapi(resp, "/api/admin/slots", "put")


# ------------------------------------------------------------------------------------- API-SET-06: publico
def test_api_set_06_publico_carrega_font_e_text_color(stack: Stack):
    csrf = stack.login_csrf()
    with stack.shared() as conn:
        seed_golden_lives(conn)
        seed_subscribers(conn)
    put_slots(stack, csrf, [slot_item("dQw4w9WgXcQ", 10, "#112233", "Bahnschrift", "#0a0b0c"), *BLANK_SLOTS[1:]])
    put_settings(stack, csrf, {"subs_font": "Verdana", "subs_text_color": "#ff8800"})

    live = stack.client.get("/api/public/lives/1")
    assert live.json()["font"] == "Bahnschrift" and live.json()["text_color"] == "#0A0B0C"
    assert list(live.json())[-4:] == ["goal", "color", "font", "text_color"]
    assert_schema(live.json(), "PublicLive")
    assert_matches_openapi(live, "/api/public/lives/{slot}", "get")

    subs = stack.client.get("/api/public/subscribers")
    assert subs.json()["font"] == "Verdana" and subs.json()["text_color"] == "#FF8800"
    assert list(subs.json())[-2:] == ["font", "text_color"]
    assert_schema(subs.json(), "PublicSubscribers")
    assert_matches_openapi(subs, "/api/public/subscribers", "get")

    # legados: nem uma palavra nova (os golden files continuam o contrato)
    for path in ("/live1.json", "/inscritos.json"):
        text = stack.client.get(path).text
        assert "font" not in text and "text_color" not in text and "goal" not in text, path


# ------------------------------------------------------------------------------------- API-SET-07: autenticacao
def test_api_set_07_rotas_novas_exigem_sessao_e_csrf(stack: Stack):
    fresh = stack.new_client()
    for method, path in (("GET", "/api/admin/settings"), ("PUT", "/api/admin/settings"), ("GET", "/api/admin/fonts")):
        assert fresh.request(method, path, json={"subs_font": "Arial"} if method == "PUT" else None).status_code == 401
    csrf = stack.login_csrf()
    for headers in ({}, {"X-CSRF-Token": csrf[:-1] + "x"}):
        resp = stack.client.put("/api/admin/settings", json={"subs_font": "Arial"}, headers=headers)
        assert resp.status_code == 403
    assert stored_settings(stack) == []
    assert put_settings(stack, csrf, {"subs_font": "Arial"}).status_code == 200


# ------------------------------------------------------------------------------------- API-SET-08: autorizador
def test_api_set_08_autorizador_da_api_em_settings(stack: Stack):
    conn = db.open_shared(stack.db_path)
    try:
        conn.execute("INSERT INTO settings (key, value) VALUES ('subs_font', 'Arial')")
        conn.execute("UPDATE settings SET value = 'Impact' WHERE key = 'subs_font'")
        assert conn.execute("SELECT value FROM settings WHERE key = 'subs_font'").fetchone()[0] == "Impact"
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DELETE FROM settings")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("DROP TABLE settings")
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("ALTER TABLE slots ADD COLUMN x TEXT")
    finally:
        conn.close()
