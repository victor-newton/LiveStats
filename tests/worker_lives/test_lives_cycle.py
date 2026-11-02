"""LIV-API-*, LIV-MAP-*, LIV-SLOT-*, LIV-TS-01: um ciclo do worker contra o dublê HTTP do YouTube.

O dublê serve itens de vídeo REAIS gravados (fixtures/). Em poucos testes um item real é derivado
(copiado e com UM campo removido/trocado) para chegar a um caso que a API real não oferecia no
momento da gravação; cada um desses testes diz isso na docstring.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time

import pytest

from lives_helpers import (
    ENDED_ID,
    GOLDEN,
    LIVE_ID,
    NO_LIKES_ID,
    UPCOMING_ID,
    epoch,
    legacy_json,
    live_rows,
    real_catalog,
    set_slots,
    watch,
)

PART = "statistics,snippet,liveStreamingDetails"


def _one_slot(db_path, url):
    set_slots(db_path, [url, "", "", ""])


# ---------------------------------------------------------------------------- LIV-API


def test_liv_api_01_uma_unica_chamada_com_ids_unicos(make_worker, youtube, db_path):
    set_slots(
        db_path,
        [watch(LIVE_ID), UPCOMING_ID, f"https://youtu.be/{ENDED_ID}?t=5", f"https://www.youtube.com/live/{LIVE_ID}"],
    )
    worker = make_worker()

    worker.run_cycle()

    assert len(youtube.requests) == 1
    params = youtube.requests[0].url.params
    assert params["id"] == f"{LIVE_ID},{UPCOMING_ID},{ENDED_ID}"
    assert params["part"] == PART
    assert youtube.requests[0].url.path == "/youtube/v3/videos"
    assert sorted(live_rows(db_path)) == [1, 2, 3, 4]

    worker.run_cycle()
    assert len(youtube.requests) == 2  # uma por ciclo


def test_liv_api_02_chave_vai_no_cabecalho_e_nao_na_url(make_worker, youtube, db_path, api_key, monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "valor-do-ambiente-que-nao-deve-ser-usado")
    _one_slot(db_path, watch(LIVE_ID))

    make_worker().run_cycle()

    request = youtube.requests[0]
    assert request.headers["X-Goog-Api-Key"] == api_key
    assert "key" not in request.url.params
    assert api_key not in str(request.url)
    assert "key=" not in str(request.url)


# ---------------------------------------------------------------------------- LIV-MAP


def test_liv_map_01_live_usa_espectadores_simultaneos(make_worker, youtube, db_path):
    item = real_catalog()[LIVE_ID]
    concurrent = int(item["liveStreamingDetails"]["concurrentViewers"])
    assert int(item["statistics"]["viewCount"]) != concurrent  # a fixture distingue os dois campos
    _one_slot(db_path, watch(LIVE_ID))

    make_worker().run_cycle()

    row = live_rows(db_path)[1]
    assert row["status"] == "ao_vivo"
    assert row["active"] == 1
    assert row["video_id"] == LIVE_ID
    assert row["title"] == item["snippet"]["title"]
    assert row["views"] == concurrent == 3374
    assert row["likes"] == int(item["statistics"]["likeCount"]) == 643
    assert row["views_formatted"] == "3.4K"
    assert row["likes_formatted"] == "643"


@pytest.mark.parametrize("variante", ["ausente", "zero"])
def test_liv_map_02_live_sem_espectadores_usa_view_count(variante, make_worker, youtube, db_path):
    """Item REAL ao vivo, derivado: só `concurrentViewers` removido ou "0" (a API não ofereceu
    uma live sem o campo no momento da gravação)."""
    details = youtube.catalog[LIVE_ID]["liveStreamingDetails"]
    if variante == "ausente":
        del details["concurrentViewers"]
    else:
        details["concurrentViewers"] = "0"
    view_count = int(youtube.catalog[LIVE_ID]["statistics"]["viewCount"])
    assert view_count == 4877
    _one_slot(db_path, watch(LIVE_ID))

    make_worker().run_cycle()

    row = live_rows(db_path)[1]
    assert row["status"] == "ao_vivo"
    assert row["views"] == view_count
    assert row["views_formatted"] == "4.9K"


def test_liv_map_03_agendada_e_encerrada(make_worker, youtube, db_path):
    set_slots(db_path, [watch(UPCOMING_ID), watch(ENDED_ID), "", ""])

    make_worker().run_cycle()

    rows = live_rows(db_path)
    assert rows[1]["status"] == "agendada"
    assert rows[1]["views"] == 0
    assert rows[1]["likes"] == 90
    assert rows[2]["status"] == "encerrada"
    assert rows[2]["views"] == 15233501
    assert rows[2]["views_formatted"] == "15.2M"
    assert rows[2]["likes"] == 625436
    assert rows[2]["likes_formatted"] == "625.4K"


def test_liv_map_03_live_broadcast_content_ausente_vira_encerrada(make_worker, youtube, db_path):
    """Item REAL encerrado, derivado: `snippet.liveBroadcastContent` removido."""
    del youtube.catalog[ENDED_ID]["snippet"]["liveBroadcastContent"]
    _one_slot(db_path, watch(ENDED_ID))

    make_worker().run_cycle()

    assert live_rows(db_path)[1]["status"] == "encerrada"


def test_liv_map_04_sem_like_count_vira_zero(make_worker, youtube, db_path):
    item = real_catalog()[NO_LIKES_ID]
    assert "likeCount" not in item["statistics"]  # a fixture real realmente não tem o campo
    _one_slot(db_path, watch(NO_LIKES_ID))

    make_worker().run_cycle()

    row = live_rows(db_path)[1]
    assert row["likes"] == 0
    assert row["likes_formatted"] == "0"
    assert row["views"] == 8
    assert row["status"] == "agendada"


GOLDEN_FILES = {1: "live1_ao_vivo.json", 2: "live2_agendada.json", 3: "live3_encerrada.json", 4: "live4_sem_url.json"}
BASE_ITEM = {1: LIVE_ID, 2: UPCOMING_ID, 3: ENDED_ID}
BROADCAST = {"ao_vivo": "live", "agendada": "upcoming", "encerrada": "none"}


def _item_from_golden(base: dict, golden: dict) -> dict:
    """Item REAL como base, com os valores de entrada do golden (id, título, números, estado)."""
    item = copy.deepcopy(base)
    item["id"] = golden["video_id"]
    item["snippet"]["title"] = golden["titulo"]
    item["snippet"]["liveBroadcastContent"] = BROADCAST[golden["status"]]
    stats = item["statistics"]
    if golden["status"] == "ao_vivo":
        stats["viewCount"] = str(golden["views"] * 3 + 1)  # diferente do simultâneo
        item["liveStreamingDetails"]["concurrentViewers"] = str(golden["views"])
    else:
        stats["viewCount"] = str(golden["views"])
        item.get("liveStreamingDetails", {}).pop("concurrentViewers", None)
    stats["likeCount"] = str(golden["likes"])
    return item


def test_liv_map_05_linhas_reproduzem_os_golden_byte_a_byte(make_worker, youtube, db_path, clock):
    goldens = {}
    for slot, name in GOLDEN_FILES.items():
        raw = (GOLDEN / name).read_bytes()
        goldens[slot] = (raw, json.loads(raw)[0])

    urls = [""] * 4
    for slot, base_id in BASE_ITEM.items():
        raw, golden = goldens[slot]
        youtube.catalog[golden["video_id"]] = _item_from_golden(youtube.catalog[base_id], golden)
        urls[slot - 1] = watch(golden["video_id"])
    set_slots(db_path, urls)
    worker = make_worker()

    for slot, (raw, golden) in goldens.items():
        clock.t = epoch(golden["atualizado_em"])
        worker.run_cycle()
        row = live_rows(db_path)[slot]
        assert legacy_json(row) == raw, f"slot {slot} diverge do golden"


# ---------------------------------------------------------------------------- LIV-SLOT


def test_liv_slot_01_slot_vazio_ou_invalido_vira_sem_url_a_cada_ciclo(make_worker, youtube, db_path, clock):
    set_slots(db_path, ["", "abc", "   ", watch(LIVE_ID)])
    worker = make_worker()

    worker.run_cycle()
    first = live_rows(db_path)
    clock.t += 30
    worker.run_cycle()
    second = live_rows(db_path)

    for slot in (1, 2, 3):
        row = second[slot]
        assert row["active"] == 0
        assert row["status"] == "sem_url"
        assert row["video_id"] == ""
        assert row["title"] == ""
        assert (row["views"], row["likes"]) == (0, 0)
        assert (row["views_formatted"], row["likes_formatted"]) == ("0", "0")
        assert row["updated_at"] != first[slot]["updated_at"]  # reescrita a cada ciclo
    assert second[4]["active"] == 1
    assert [r.url.params["id"] for r in youtube.requests] == [LIVE_ID, LIVE_ID]


def test_liv_slot_01_sem_nenhum_id_valido_nao_chama_a_api(make_worker, youtube, db_path):
    set_slots(db_path, ["", "abc", "   ", "https://youtube.com/shorts/dQw4w9WgXcQ"])

    make_worker().run_cycle()

    assert youtube.requests == []
    assert {r["status"] for r in live_rows(db_path).values()} == {"sem_url"}


def test_liv_slot_02_mesmo_video_em_dois_slots(make_worker, youtube, db_path):
    set_slots(db_path, [watch(LIVE_ID), "", f"https://youtu.be/{LIVE_ID}", ""])

    make_worker().run_cycle()

    rows = live_rows(db_path)
    assert rows[1]["video_id"] == rows[3]["video_id"] == LIVE_ID
    assert (rows[1]["slot"], rows[3]["slot"]) == (1, 3)
    assert rows[1]["active"] == rows[3]["active"] == 1
    assert rows[2]["status"] == rows[4]["status"] == "sem_url"
    assert len(youtube.requests) == 1


def test_liv_slot_03_mudanca_de_url_vale_no_ciclo_seguinte(make_worker, youtube, db_path):
    _one_slot(db_path, watch(LIVE_ID))
    worker = make_worker()
    worker.run_cycle()
    assert live_rows(db_path)[1]["video_id"] == LIVE_ID

    _one_slot(db_path, watch(ENDED_ID))  # o admin trocou a URL; o worker NÃO é reiniciado
    worker.run_cycle()

    assert youtube.requests[1].url.params["id"] == ENDED_ID
    row = live_rows(db_path)[1]
    assert row["video_id"] == ENDED_ID
    assert row["status"] == "encerrada"


def test_liv_slot_03_url_com_espacos_nas_pontas(make_worker, youtube, db_path):
    _one_slot(db_path, f"  {watch(LIVE_ID)}  ")

    make_worker().run_cycle()

    assert live_rows(db_path)[1]["video_id"] == LIVE_ID


# ---------------------------------------------------------------------------- LIV-TS


def test_liv_ts_01_updated_at_em_utc_com_z(make_worker, youtube, db_path, clock):
    """Com fuso local diferente de UTC (TZ=UTC-3), o texto continua em UTC."""
    clock.t = epoch("2026-11-02T14:32:00.123456Z")
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "BRT3"
    time.tzset()
    try:
        worker.run_cycle()
    finally:
        if old_tz is None:
            del os.environ["TZ"]
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    rows = live_rows(db_path)
    pattern = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
    for row in rows.values():
        assert pattern.match(row["updated_at"]), row["updated_at"]
        assert row["updated_at"] == "2026-11-02T14:32:00.123456Z"
