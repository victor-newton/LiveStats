"""API-PUB-01 .. API-PUB-07 e API-LEG-01 .. API-LEG-08: endpoints publicos e formatos legados."""

from __future__ import annotations

import json
import re

import pytest

from api_support import (
    BLANK_SLOTS,
    GOLDEN,
    GOLDEN_FILES,
    GOLDEN_LIVE_ROWS,
    GOLDEN_SUBSCRIBERS,
    Stack,
    assert_matches_openapi,
    assert_schema,
    seed_golden_lives,
    seed_live,
    seed_subscribers,
    slot_item,
    walk_keys,
)

TIMESTAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d{6})?Z")
ACTIVE_ORDER = ["video_id", "titulo", "status", "views", "likes", "views_formatado", "likes_formatado", "atualizado_em", "slot", "ativo"]
NO_URL_ORDER = ["slot", "ativo", "video_id", "titulo", "status", "views", "likes", "views_formatado", "likes_formatado", "atualizado_em"]


def golden(name: str) -> bytes:
    return (GOLDEN / name).read_bytes()


@pytest.fixture
def seeded(stack: Stack) -> Stack:
    with stack.shared() as conn:
        seed_golden_lives(conn)
        seed_subscribers(conn)
    return stack


# ------------------------------------------------------------------------------------- API-PUB-01
@pytest.mark.parametrize("slot", [1, 2, 3, 4])
def test_api_pub_01_live_por_slot_e_objeto_conforme_o_contrato(seeded: Stack, slot):
    resp = seeded.client.get(f"/api/public/lives/{slot}")
    assert resp.status_code == 200
    body = resp.json()
    row = GOLDEN_LIVE_ROWS[slot]
    assert isinstance(body, dict)
    assert body == {
        "slot": slot,
        "ativo": "true" if row["active"] else "false",
        "video_id": row["video_id"],
        "titulo": row["title"],
        "status": row["status"],
        "views": row["views"],
        "likes": row["likes"],
        "views_formatado": row["views_formatted"],
        "likes_formatado": row["likes_formatted"],
        "atualizado_em": row["updated_at"],
        "goal": None,  # D34: meta e cor do slot (banco novo: sem meta, cor padrao)
        "color": "#FF0033",
        "font": "Roboto",  # D35: aparencia do texto (padrao)
        "text_color": "#FFFFFF",
    }
    assert isinstance(body["ativo"], str)  # nao e booleano
    assert_schema(body, "PublicLive")
    assert_matches_openapi(resp, "/api/public/lives/{slot}", "get")


# ------------------------------------------------------------------------------------- API-PUB-02
@pytest.mark.parametrize("slot", ["0", "5", "-1", "99"])
def test_api_pub_02_slot_fora_de_1_a_4_devolve_404(seeded: Stack, slot):
    resp = seeded.client.get(f"/api/public/lives/{slot}")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Slot inválido (1-4)"}
    assert_matches_openapi(resp, "/api/public/lives/{slot}", "get")


@pytest.mark.parametrize("slot", ["abc", "1.5", "0x1"])
def test_api_pub_02_slot_nao_numerico_devolve_422(seeded: Stack, slot):
    resp = seeded.client.get(f"/api/public/lives/{slot}")
    assert resp.status_code == 422
    assert "input" not in set(walk_keys(resp.json()))
    assert_matches_openapi(resp, "/api/public/lives/{slot}", "get")


def test_api_pub_02_sem_linha_devolve_503(stack: Stack):
    resp = stack.client.get("/api/public/lives/1")
    assert resp.status_code == 503
    assert resp.json() == {"detail": "Dados ainda não disponíveis"}
    assert_matches_openapi(resp, "/api/public/lives/{slot}", "get")


def test_api_pub_02_so_o_slot_sem_linha_devolve_503(stack: Stack):
    with stack.shared() as conn:
        seed_live(conn, 2)
    assert stack.client.get("/api/public/lives/2").status_code == 200
    assert stack.client.get("/api/public/lives/1").status_code == 503


# ------------------------------------------------------------------------------------- API-PUB-03
def test_api_pub_03_subscribers_com_e_sem_leitura(stack: Stack):
    empty = stack.client.get("/api/public/subscribers")
    assert empty.status_code == 503
    assert empty.json() == {"detail": "Dados ainda não disponíveis. Scraper inicializando..."}
    assert_matches_openapi(empty, "/api/public/subscribers", "get")

    with stack.shared() as conn:
        seed_subscribers(conn)
    resp = stack.client.get("/api/public/subscribers")
    assert resp.status_code == 200
    assert resp.json() == {
        "inscritos": "460600", "raw": "460600", "atualizado_em": GOLDEN_SUBSCRIBERS["read_at"],
        "font": "Roboto", "text_color": "#FFFFFF",  # D35
    }
    assert isinstance(resp.json()["inscritos"], str)
    assert_matches_openapi(resp, "/api/public/subscribers", "get")


# ------------------------------------------------------------------------------------- API-PUB-04
def test_api_pub_04_cabecalhos_das_respostas_publicas(seeded: Stack):
    for path in ("/api/public/lives/1", "/api/public/subscribers", "/live1.json", "/inscritos.json", "/health"):
        resp = seeded.client.get(path)
        assert resp.status_code == 200, path
        assert resp.headers["cache-control"] == "no-store", path
        assert resp.headers["content-type"] == "application/json", path
    for path in ("/api/public/lives/9", "/live9.json"):  # erros tambem nao entram em cache
        resp = seeded.client.get(path)
        assert resp.status_code == 404 and resp.headers["cache-control"] == "no-store"


# ------------------------------------------------------------------------------------- API-PUB-05
PUBLIC_PATHS = ["/api/public/lives/1", "/api/public/subscribers", "/live1.json", "/inscritos.json", "/health", "/live9.json"]
ADMIN_PATHS = ["/api/admin/session", "/api/admin/slots", "/api/admin/login", "/api/admin/workers/status", "/api/admin/nada"]


def cors_headers(resp) -> list[str]:
    return [name for name in resp.headers if name.lower().startswith("access-control-")]


def test_api_pub_05_a_api_nunca_emite_cors(seeded: Stack):
    seeded.login_csrf()
    origin = {"Origin": "https://evil.example"}
    preflight = {**origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "x-csrf-token"}
    for path in PUBLIC_PATHS + ADMIN_PATHS:
        resp = seeded.client.get(path, headers=origin)
        assert cors_headers(resp) == [], (path, resp.status_code, cors_headers(resp))
        options = seeded.client.options(path, headers=preflight)
        assert options.status_code in (401, 404, 405), (path, options.status_code)  # nunca 200/204 de preflight
        assert cors_headers(options) == [], (path, cors_headers(options))
    anonymous = seeded.new_client()
    for path in PUBLIC_PATHS + ADMIN_PATHS:
        for resp in (anonymous.get(path, headers=origin), anonymous.options(path, headers=preflight)):
            assert cors_headers(resp) == [], path
    post = anonymous.post("/api/admin/login", json={"username": "x", "password": "y"}, headers=origin)
    assert cors_headers(post) == []


# ------------------------------------------------------------------------------------- API-PUB-06
def test_api_pub_06_dados_publicos_nao_vazam_admin(seeded: Stack):
    csrf = seeded.login_csrf()
    url_sentinel = "https://www.youtube.com/watch?v=SENTINELA01"
    assert seeded.client.put(
        "/api/admin/slots", json={"slots": [slot_item(url_sentinel), *BLANK_SLOTS[1:]]}, headers={"X-CSRF-Token": csrf}
    ).status_code == 200
    with seeded.shared() as conn:
        conn.execute(
            "INSERT INTO worker_status (worker, state, last_heartbeat_at, interval_seconds, last_error_code, last_error_message) "
            "VALUES ('lives', 'degraded', 1800000000, 30, 'http_error', 'MENSAGEM-SENTINELA')"
        )
        conn.commit()
    forbidden = [url_sentinel, "SENTINELA01", csrf, "MENSAGEM-SENTINELA", "csrf", "worker", "last_error", "heartbeat", seeded.username]
    allowed_live_keys = set(ACTIVE_ORDER)
    for path in ("/api/public/lives/1", "/api/public/subscribers", "/live1.json", "/inscritos.json", "/health"):
        resp = seeded.client.get(path)
        assert resp.status_code == 200
        for needle in forbidden:
            assert needle not in resp.text, (path, needle)
        if path.startswith("/api/public/lives") or path.startswith("/live"):
            body = resp.json()
            body = body[0] if isinstance(body, list) else body
            # D34: so o formato novo carrega goal e color (configuracao publica do overlay); os legados nao
            assert set(body) == allowed_live_keys | (
                {"goal", "color", "font", "text_color"} if path.startswith("/api/public") else set()
            )


# ------------------------------------------------------------------------------------- API-PUB-07
def test_api_pub_07_titulo_com_html_e_devolvido_como_texto_literal(stack: Stack):
    xss = "<img src=x onerror=alert(1)>"
    with stack.shared() as conn:
        seed_live(conn, 1, title=xss)
    for path in ("/api/public/lives/1", "/live1.json"):
        resp = stack.client.get(path)
        assert resp.content.count(xss.encode()) == 1  # literal nos bytes: sem escape de < > nem \u003c
        body = resp.json()
        assert (body[0] if isinstance(body, list) else body)["titulo"] == xss
        assert resp.headers["content-type"] == "application/json"  # nunca text/html


# ------------------------------------------------------------------------------------- API-LEG-01
@pytest.mark.parametrize("slot", [1, 2, 3, 4])
def test_api_leg_01_live_json_byte_a_byte(seeded: Stack, slot):
    resp = seeded.client.get(f"/live{slot}.json")
    assert resp.status_code == 200
    assert resp.content == golden(GOLDEN_FILES[slot])
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-store"
    assert_matches_openapi(resp, "/live{n}.json", "get")


# ------------------------------------------------------------------------------------- API-LEG-02
def test_api_leg_02_ordem_das_chaves(seeded: Stack):
    def ordered_keys(slot: int) -> list[str]:
        pairs = json.loads(seeded.client.get(f"/live{slot}.json").content, object_pairs_hook=lambda p: p)
        assert len(pairs) == 1
        return [k for k, _ in pairs[0]]

    for slot in (1, 2, 3):
        assert ordered_keys(slot) == ACTIVE_ORDER, slot
        assert ordered_keys(slot)[-2:] == ["slot", "ativo"]
    assert ordered_keys(4) == NO_URL_ORDER
    assert ordered_keys(4)[:2] == ["slot", "ativo"]
    # o mesmo vale para o objeto de /api/public/lives/{n}, que ganha goal e color no fim (D34)
    for slot, order in ((1, ACTIVE_ORDER), (4, NO_URL_ORDER)):
        pairs = json.loads(seeded.client.get(f"/api/public/lives/{slot}").content, object_pairs_hook=lambda p: p)
        assert [k for k, _ in pairs] == order + ["goal", "color", "font", "text_color"]


# ------------------------------------------------------------------------------------- API-LEG-03
@pytest.mark.parametrize("n", [0, 5, 9, 100])
def test_api_leg_03_slot_invalido_byte_a_byte(seeded: Stack, n):
    resp = seeded.client.get(f"/live{n}.json")
    assert resp.status_code == 404
    assert resp.content == golden("error_slot_invalido.json")
    assert resp.headers["content-type"] == "application/json"


def test_api_leg_03_sem_dado_byte_a_byte(stack: Stack):
    for n in (1, 2, 3, 4):
        resp = stack.client.get(f"/live{n}.json")
        assert resp.status_code == 503
        assert resp.content == golden("error_dados_indisponiveis.json")
        assert_matches_openapi(resp, "/live{n}.json", "get")


def test_api_leg_03_n_nao_numerico_devolve_422_da_api(seeded: Stack):
    resp = seeded.client.get("/liveabc.json")
    assert resp.status_code == 422
    assert "input" not in set(walk_keys(resp.json()))


# ------------------------------------------------------------------------------------- API-LEG-04
def test_api_leg_04_inscritos_json_byte_a_byte(seeded: Stack):
    resp = seeded.client.get("/inscritos.json")
    assert resp.status_code == 200
    assert resp.content == golden("inscritos.json")
    assert resp.headers["content-type"] == "application/json"
    assert resp.headers["cache-control"] == "no-store"
    assert_matches_openapi(resp, "/inscritos.json", "get")


def test_api_leg_04_inscritos_sem_leitura(stack: Stack):
    resp = stack.client.get("/inscritos.json")
    assert resp.status_code == 503
    assert resp.content == golden("error_inscritos_indisponiveis.json")
    assert_matches_openapi(resp, "/inscritos.json", "get")


def test_api_leg_04_atualizado_em_acompanha_cada_leitura(stack: Stack):
    with stack.shared() as conn:
        seed_subscribers(conn, read_at="2026-11-02T14:32:10.000001Z")
    first = stack.client.get("/inscritos.json").json()
    with stack.shared() as conn:  # mesmo numero, nova leitura (como o worker faz)
        seed_subscribers(conn, read_at="2026-11-02T14:32:20.000002Z")
    second = stack.client.get("/inscritos.json").json()
    assert first["inscritos"] == second["inscritos"]
    assert first["atualizado_em"] != second["atualizado_em"]


# ------------------------------------------------------------------------------------- API-LEG-05
def test_api_leg_05_health_com_banco_vazio(stack: Stack):
    stack.clock.advance(0.5)
    resp = stack.client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert list(body) == ["status", "server_time", "data_file_exists", "inscritos", "last_update"]
    assert body["status"] == "ok"
    assert TIMESTAMP.fullmatch(body["server_time"])
    assert body["server_time"] == "2027-01-15T08:00:00.500000Z"  # UTC, isoformat + Z, com microssegundos
    assert body["data_file_exists"] is False and body["inscritos"] is None and body["last_update"] is None
    assert "cookies_file_exists" not in resp.text
    assert_matches_openapi(resp, "/health", "get")


def test_api_leg_05_health_reflete_a_leitura_de_inscritos(seeded: Stack):
    body = seeded.client.get("/health").json()
    assert list(body) == ["status", "server_time", "data_file_exists", "inscritos", "last_update"]
    assert body["data_file_exists"] is True
    assert body["inscritos"] == "460600"
    assert body["last_update"] == GOLDEN_SUBSCRIBERS["read_at"]
    assert_schema(body, "Health")


def test_api_leg_05_health_nao_exige_sessao_nem_dados(stack: Stack):
    assert stack.new_client().get("/health").status_code == 200  # sem cookie
    assert stack.new_client().head("/health").status_code in (200, 405)


# ------------------------------------------------------------------------------------- API-LEG-06
@pytest.mark.parametrize("slot", [1, 2, 3, 4])
def test_api_leg_06_caminhos_novo_e_legado_concordam(seeded: Stack, slot):
    legacy = seeded.client.get(f"/live{slot}.json").content
    new = seeded.client.get(f"/api/public/lives/{slot}").content
    # o objeto novo e o legado + goal/color no fim (D34, banco sem meta: null e a cor padrao)
    extra = b',"goal":null,"color":"#FF0033","font":"Roboto","text_color":"#FFFFFF"}'
    assert new.endswith(extra)
    assert legacy == b"[" + new[: -len(extra)] + b"}]"
    assert {k: v for k, v in json.loads(new).items() if k not in ("goal", "color", "font", "text_color")} == json.loads(legacy)[0]


def test_api_leg_06_subscribers_e_inscritos_json_sao_identicos(seeded: Stack):
    # D35: o formato novo e o legado + font/text_color no fim; o legado continua byte a byte igual
    legacy = seeded.client.get("/inscritos.json").content
    new = seeded.client.get("/api/public/subscribers").content
    extra = b',"font":"Roboto","text_color":"#FFFFFF"}'
    assert new.endswith(extra) and new[: -len(extra)] + b"}" == legacy


# ------------------------------------------------------------------------------------- API-LEG-07
def test_api_leg_07_utf8_literal_e_timestamps(stack: Stack):
    title = "Sorteio açaí 🎉 — café <b>teste</b> & \"aspas\""
    with stack.shared() as conn:
        seed_live(conn, 1, title=title, updated_at="2026-11-02T14:32:00.123456Z")
        seed_live(conn, 2, title="curto", updated_at="2026-11-02T14:32:00Z")  # isoformat sem microssegundos
    one = stack.client.get("/live1.json").content
    assert "açaí 🎉 — café".encode("utf-8") in one
    assert b"\\u" not in one and b"\\U" not in one
    assert b"<b>teste</b> & " in one
    assert json.loads(one)[0]["titulo"] == title
    assert b'"atualizado_em":"2026-11-02T14:32:00.123456Z"' in one
    two = stack.client.get("/live2.json").content
    assert b'"atualizado_em":"2026-11-02T14:32:00Z"' in two
    for payload in (one, two):
        assert b": " not in payload and b", " not in payload  # compacto
        assert not payload.endswith(b"\n")


# ------------------------------------------------------------------------------------- API-LEG-08
@pytest.mark.parametrize("path", ["/", "/admin", "/admin/", "/contador"])
def test_api_leg_08_a_api_nao_serve_paginas(stack: Stack, path):
    resp = stack.client.get(path)
    assert resp.status_code == 404
    assert "text/html" not in resp.headers.get("content-type", "")
    assert "<" not in resp.text
