"""INT-01 e INT-10: fluxo de lives de ponta a ponta pela pilha real (painel/api -> SQLite -> worker-lives -> YouTube falso)."""

from __future__ import annotations

import time

import pytest
from e2e_stack import (
    LIVE_INTERVAL,
    SLOT_URL,
    VID_ENDED,
    VID_LIVE,
    VID_OTHER,
    VID_UPCOMING,
    wait_until,
)

pytestmark = [pytest.mark.e2e, pytest.mark.docker]


def test_int_01_fluxo_de_lives_de_ponta_a_ponta(stack):
    stack.fake_set(videos=stack.default_videos())
    stack.set_slots(["", "", "", ""])  # ponto de partida conhecido
    wait_until(lambda: stack.public_json("/live1.json")[0]["status"] == "sem_url", timeout=3 * LIVE_INTERVAL)

    t_put = time.time()
    # 3 URLs; o slot 3 repete o video do slot 1 => os IDs UNICOS da chamada sao so dois
    stack.set_slots([SLOT_URL.format(VID_LIVE), SLOT_URL.format(VID_UPCOMING), SLOT_URL.format(VID_LIVE), ""])

    def reflected():
        one = stack.public_json("/live1.json")[0]
        return one["video_id"] == VID_LIVE and one["status"] == "ao_vivo"

    wait_until(reflected, timeout=2 * LIVE_INTERVAL + 5, interval=1)
    reflect_seconds = time.time() - t_put
    assert reflect_seconds <= 2 * LIVE_INTERVAL + 5

    one = stack.public_json("/live1.json")
    two = stack.public_json("/live2.json")
    three = stack.public_json("/live3.json")
    four = stack.public_json("/live4.json")
    assert [len(x) for x in (one, two, three, four)] == [1, 1, 1, 1]
    a, b, c, d = one[0], two[0], three[0], four[0]

    # ao vivo: usa concurrentViewers (3374) como visualizacoes
    assert (a["slot"], a["ativo"], a["video_id"], a["titulo"], a["status"]) == (1, "true", VID_LIVE, "T8 Live ao vivo", "ao_vivo")
    assert (a["views"], a["likes"], a["views_formatado"], a["likes_formatado"]) == (3374, 640, "3.4K", "640")
    # agendada
    assert (b["slot"], b["ativo"], b["video_id"], b["titulo"], b["status"]) == (2, "true", VID_UPCOMING, "T8 Live agendada", "agendada")
    assert (b["views"], b["likes"], b["views_formatado"], b["likes_formatado"]) == (0, 90, "0", "90")
    # o slot 3 mostra o mesmo video do 1, com o numero do slot dele
    assert c["slot"] == 3 and c["video_id"] == VID_LIVE and c["views"] == 3374 and c["titulo"] == a["titulo"]
    # slot 4 vazio
    assert d["slot"] == 4 and d["ativo"] == "false" and d["status"] == "sem_url" and d["video_id"] == "" and d["titulo"] == ""

    # o servidor falso: uma chamada em lote por ciclo, com IDs unicos, chave por cabecalho (nunca na URL)
    def two_calls():
        calls = [r for r in stack.fake_requests("fake-lives", "videos", since=t_put) if set(r["ids"]) == {VID_LIVE, VID_UPCOMING}]
        return calls if len(calls) >= 2 else None

    calls = wait_until(two_calls, timeout=2 * LIVE_INTERVAL + 10, interval=1)
    for call in calls:
        assert sorted(call["ids"]) == sorted([VID_LIVE, VID_UPCOMING]), "ids duplicados ou faltando"
        assert call["key_ok"] is True, "o worker nao mandou a chave do secret no cabecalho"
        assert call["key_in_url"] is False
        assert call["part"] == "statistics,snippet,liveStreamingDetails"
    gaps = [calls[i + 1]["t"] - calls[i]["t"] for i in range(len(calls) - 1)]
    assert all(LIVE_INTERVAL - 1.5 <= gap <= LIVE_INTERVAL + 5 for gap in gaps), f"mais de uma chamada por ciclo? {gaps}"
    # nenhuma chamada fora do lote (cada ciclo = uma unica requisicao)
    all_calls = stack.fake_requests("fake-lives", "videos", since=t_put)
    assert all(set(r["ids"]) == {VID_LIVE, VID_UPCOMING} for r in all_calls)


def test_int_10_troca_de_url_do_slot_leva_no_maximo_um_ciclo(stack):
    stack.ensure_lives()

    t0 = time.time()
    stack.set_slots([SLOT_URL.format(VID_LIVE), SLOT_URL.format(VID_OTHER), SLOT_URL.format(VID_ENDED), ""])
    wait_until(lambda: stack.public_json("/live2.json")[0]["video_id"] == VID_OTHER, timeout=LIVE_INTERVAL + 6, interval=0.5)
    elapsed_change = time.time() - t0
    two = stack.public_json("/live2.json")[0]
    assert (two["titulo"], two["status"], two["views"], two["likes"]) == ("T8 Outro video", "ao_vivo", 321, 7)
    assert elapsed_change <= LIVE_INTERVAL + 6

    t1 = time.time()
    stack.set_slots([SLOT_URL.format(VID_LIVE), "", SLOT_URL.format(VID_ENDED), ""])
    wait_until(lambda: stack.public_json("/live2.json")[0]["status"] == "sem_url", timeout=LIVE_INTERVAL + 6, interval=0.5)
    assert time.time() - t1 <= LIVE_INTERVAL + 6
    emptied = stack.public_json("/live2.json")[0]
    assert (emptied["ativo"], emptied["video_id"], emptied["titulo"], emptied["views"]) == ("false", "", "", 0)

    # sem cache: as vizinhas continuam certas
    assert stack.public_json("/live1.json")[0]["video_id"] == VID_LIVE
    stack.ensure_lives()
