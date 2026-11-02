"""API-SLOT-01 .. API-SLOT-07 (+ corpo/validacao herdada): slots de lives."""

from __future__ import annotations

import pytest

from api_support import GOLDEN_LIVE_ROWS, Stack, assert_matches_openapi, assert_schema, seed_golden_lives, walk_keys

VALID = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/9bZkp7q19f0",
    "",
    "jNQXAC9IVRw",
]


def db_slots(stack: Stack) -> list[tuple[int, str, str | None]]:
    with stack.shared() as conn:
        return [tuple(r) for r in conn.execute("SELECT slot, url, updated_at FROM slots ORDER BY slot")]


def put(stack: Stack, csrf: str, slots, **kwargs):
    return stack.client.put("/api/admin/slots", json={"slots": slots}, headers={"X-CSRF-Token": csrf}, **kwargs)


# ------------------------------------------------------------------------------------- API-SLOT-01
def test_api_slot_01_get_em_banco_novo(stack: Stack):
    stack.login_csrf()
    resp = stack.client.get("/api/admin/slots")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"slots": [{"slot": n, "url": "", "stats": None} for n in (1, 2, 3, 4)], "saved_at": None}
    assert_matches_openapi(resp, "/api/admin/slots", "get")
    assert resp.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------------------------- API-SLOT-02
def test_api_slot_02_put_grava_com_trim_e_get_devolve_o_mesmo(stack: Stack):
    csrf = stack.login_csrf()
    padded = ["  https://www.youtube.com/watch?v=dQw4w9WgXcQ  ", "https://youtu.be/9bZkp7q19f0", "   ", " jNQXAC9IVRw"]
    resp = put(stack, csrf, padded)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    expected = [s.strip() for s in padded]
    assert body["ok"] is True and body["slots"] == expected and body["saved_at"]
    assert_matches_openapi(resp, "/api/admin/slots", "put")

    rows = db_slots(stack)
    assert [r[1] for r in rows] == expected  # gravado sem espacos nas pontas
    assert all(r[2] == body["saved_at"] for r in rows)

    got = stack.client.get("/api/admin/slots").json()
    assert [item["url"] for item in got["slots"]] == expected
    assert got["saved_at"] == body["saved_at"]
    assert_schema(got, "SlotsRead")


def test_api_slot_02_saved_at_e_a_hora_do_ultimo_put(stack: Stack):
    csrf = stack.login_csrf()
    first = put(stack, csrf, VALID).json()["saved_at"]
    stack.clock.advance(90)
    second = put(stack, csrf, VALID[::-1]).json()["saved_at"]
    assert second > first
    assert stack.client.get("/api/admin/slots").json()["saved_at"] == second


# ------------------------------------------------------------------------------------- API-SLOT-03
@pytest.mark.parametrize(
    "payload",
    [
        {"slots": ["", "", ""]},
        {"slots": ["", "", "", "", ""]},
        {"slots": []},
        {"slots": "dQw4w9WgXcQ"},
        {"slots": ["", 123, "", ""]},
        {"slots": ["", None, "", ""]},
        {"slots": ["", ["dQw4w9WgXcQ"], "", ""]},
        {"slots": ["a" * 2049, "", "", ""]},
        {"slots": ["dQw4w9WgXcQ" + "\x00", "", "", ""]},
        {"slots": ["dQw4w9WgXcQ\x07", "", "", ""]},
        {"slots": ["https://youtu.be/dQw4w9WgXcQ\nhttps://evil.example", "", "", ""]},
        {"slots": ["", "", "", ""], "extra": 1},
        {},
        [],
        "dQw4w9WgXcQ",
    ],
)
def test_api_slot_03_formato_invalido_devolve_422_e_nao_grava(stack: Stack, payload):
    csrf = stack.login_csrf()
    assert put(stack, csrf, VALID).status_code == 200
    before = db_slots(stack)

    resp = stack.client.put("/api/admin/slots", json=payload, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 422, resp.text
    assert "input" not in set(walk_keys(resp.json()))
    assert_matches_openapi(resp, "/api/admin/slots", "put")
    assert db_slots(stack) == before  # nada mudou, nem o updated_at


def test_api_slot_03_limite_de_2048_caracteres_e_aceito(stack: Stack):
    csrf = stack.login_csrf()
    long_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&x=" + "a" * (2048 - 46)
    assert len(long_url) == 2048
    resp = put(stack, csrf, [long_url, "", "", ""])
    assert resp.status_code == 200 and db_slots(stack)[0][1] == long_url


def test_api_slot_03_corpo_que_nao_e_json_devolve_400(stack: Stack):
    csrf = stack.login_csrf()
    resp = stack.client.put("/api/admin/slots", content=b"{nao json", headers={"X-CSRF-Token": csrf, "content-type": "application/json"})
    assert resp.status_code == 400
    assert resp.json() == {"detail": "JSON inválido no body da requisição"}
    assert_matches_openapi(resp, "/api/admin/slots", "put")


def test_api_slot_03_corpo_acima_de_256_kib_devolve_413_e_nao_grava(stack: Stack):
    csrf = stack.login_csrf()
    before = db_slots(stack)
    huge = {"slots": ["", "", "", ""], "pad": "x" * (256 * 1024)}
    resp = stack.client.put("/api/admin/slots", json=huge, headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 413
    assert_matches_openapi(resp, "/api/admin/slots", "put")
    assert db_slots(stack) == before


def test_api_slot_03_sessao_e_checada_antes_do_corpo(stack: Stack):
    # sem sessao, um corpo invalido/gigante nao muda a resposta: 401 (nao 400/413/422)
    anonymous = stack.new_client()
    for content in (b"{nao json", b"x" * (300 * 1024), b'{"slots": 1}'):
        resp = anonymous.put("/api/admin/slots", content=content, headers={"content-type": "application/json"})
        assert resp.status_code == 401, content[:10]


# ------------------------------------------------------------------------------------- API-SLOT-04
ACCEPTED_VECTORS = [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ?t=5",
    "https://www.youtube.com/live/dQw4w9WgXcQ",
    "dQw4w9WgXcQ",
    "https://youtube.com/watch?list=x&v=dQw4w9WgXcQ&t=1",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQextra",
]
REJECTED_VECTORS = [
    "https://www.youtube.com/shorts/dQw4w9WgXcQ",
    "https://www.youtube.com/embed/dQw4w9WgXcQ",
    "abc",
]


@pytest.mark.parametrize("url", ACCEPTED_VECTORS)
def test_api_slot_04_vetores_com_id_sao_aceitos(stack: Stack, url):
    csrf = stack.login_csrf()
    resp = put(stack, csrf, [url, "", "", ""])
    assert resp.status_code == 200, (url, resp.text)
    assert db_slots(stack)[0][1] == url


@pytest.mark.parametrize("url", REJECTED_VECTORS)
def test_api_slot_04_vetores_sem_id_sao_rejeitados(stack: Stack, url):
    csrf = stack.login_csrf()
    before = db_slots(stack)
    for position in range(4):
        slots = ["", "", "", ""]
        slots[position] = url
        resp = put(stack, csrf, slots)
        assert resp.status_code == 422, (url, position)
        assert resp.json()["detail"][0]["loc"] == ["body", "slots", position]
    assert db_slots(stack) == before


# ------------------------------------------------------------------------------------- API-SLOT-05
@pytest.mark.parametrize(
    "payload",
    [
        "<img src=x onerror=alert(1)>",
        "javascript:alert(1)",
        '"><script>alert(1)</script>',
        "<svg onload=alert(1)>",
        "https://example.com/watch?x=1",
    ],
)
def test_api_slot_05_payload_xss_como_url_e_rejeitado(stack: Stack, payload):
    csrf = stack.login_csrf()
    before = db_slots(stack)
    resp = put(stack, csrf, ["", payload, "", ""])
    assert resp.status_code == 422
    assert payload not in resp.text
    assert db_slots(stack) == before
    assert payload.encode() not in stack.all_db_bytes("DB_PATH")


# ------------------------------------------------------------------------------------- API-SLOT-06
def test_api_slot_06_get_traz_as_estatisticas_do_slot_certo(stack: Stack):
    csrf = stack.login_csrf()
    with stack.shared() as conn:
        seed_golden_lives(conn)
    xss_title = "<img src=x onerror=alert(1)>"
    with stack.shared() as conn:
        conn.execute("UPDATE live_stats SET title = ? WHERE slot = 2", (xss_title,))
        conn.commit()
    put(stack, csrf, VALID)

    resp = stack.client.get("/api/admin/slots")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/json"
    assert xss_title in resp.text  # literal: sem escape HTML nem <
    assert "\\u003c" not in resp.text.lower()
    items = resp.json()["slots"]
    for item in items:
        stats = item["stats"]
        row = GOLDEN_LIVE_ROWS[item["slot"]]
        assert stats["slot"] == item["slot"]
        assert stats["ativo"] == ("true" if row["active"] else "false")
        assert stats["video_id"] == row["video_id"]
        assert stats["status"] == row["status"]
        assert stats["views"] == row["views"] and stats["likes"] == row["likes"]
        assert stats["views_formatado"] == row["views_formatted"]
        assert stats["atualizado_em"] == row["updated_at"]
    assert items[1]["stats"]["titulo"] == xss_title
    assert items[0]["stats"]["titulo"] == GOLDEN_LIVE_ROWS[1]["title"]
    assert_schema(resp.json(), "SlotsRead")
    assert_matches_openapi(resp, "/api/admin/slots", "get")


def test_api_slot_06_slot_sem_linha_em_live_stats_tem_stats_nulo(stack: Stack):
    stack.login_csrf()
    with stack.shared() as conn:
        seed_golden_lives(conn)
        conn.execute("DELETE FROM live_stats WHERE slot = 3")
        conn.commit()
    stats = [item["stats"] for item in stack.client.get("/api/admin/slots").json()["slots"]]
    assert [s is None for s in stats] == [False, False, True, False]


# ------------------------------------------------------------------------------------- API-SLOT-07
def test_api_slot_07_urls_ficam_no_sqlite_para_o_worker(stack: Stack):
    csrf = stack.login_csrf()
    put(stack, csrf, VALID)
    # "segundo processo": conexao nova, sem passar pela api (como o worker-lives)
    with stack.shared() as worker_view:
        urls = [r[0] for r in worker_view.execute("SELECT url FROM slots ORDER BY slot")]
    assert urls == VALID
    # e uma troca posterior tambem aparece na conexao ja aberta
    with stack.shared() as worker_view:
        put(stack, csrf, ["", "", "", "dQw4w9WgXcQ"])
        assert [r[0] for r in worker_view.execute("SELECT url FROM slots ORDER BY slot")] == ["", "", "", "dQw4w9WgXcQ"]
