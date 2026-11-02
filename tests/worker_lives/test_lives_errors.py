"""LIV-ERR-*, LIV-SEC-03: falhas do YouTube, backoff e vazamento de segredo.

Respostas de erro REAIS: só `error_invalid_key` (400). As demais (403 quotaExceeded, 429, 5xx, corpo
inválido) usam fixtures `synthetic: true` no formato documentado pelo Google, e timeout/erro de rede
são exceções do httpx (não existe resposta a gravar). Ver fixtures/ e o relatório da T4.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from lives_helpers import (
    ENDED_ID,
    LIVE_ID,
    MISSING_ID,
    fixture_response,
    live_rows,
    set_slots,
    watch,
    worker_status,
)
from worker_lives.worker import backoff_seconds


def _primed(make_worker, db_path, clock, youtube, urls=None):
    """Worker que já completou um ciclo bom (linhas gravadas); devolve (worker, linhas)."""
    set_slots(db_path, urls or [watch(LIVE_ID), watch(ENDED_ID), "", ""])
    worker = make_worker()
    worker.run_cycle()
    assert worker_status(db_path)["state"] == "ok"
    clock.t += 30
    return worker, live_rows(db_path)


# ---------------------------------------------------------------------------- LIV-ERR-01


@pytest.mark.parametrize(
    ("falha", "codigo"),
    [
        (lambda: fixture_response("error_server_500"), "http_error"),
        (lambda: fixture_response("error_server_503"), "http_error"),
        (lambda: httpx.ReadTimeout("timeout lendo a resposta"), "network_error"),
        (lambda: httpx.ConnectTimeout("timeout conectando"), "network_error"),
        (lambda: httpx.ConnectError("sem rota"), "network_error"),
        (lambda: httpx.RemoteProtocolError("conexão cortada"), "network_error"),
    ],
    ids=["http500", "http503", "read-timeout", "connect-timeout", "connect-error", "protocolo"],
)
def test_liv_err_01_falha_mantem_linhas_e_marca_degraded(
    falha, codigo, make_worker, youtube, db_path, clock
):
    worker, before = _primed(make_worker, db_path, clock, youtube)

    youtube.enqueue(falha())
    worker.run_cycle()  # não pode levantar exceção

    after = live_rows(db_path)
    for slot in (1, 2):  # slots ativos: inalterados (inclusive updated_at)
        assert after[slot] == before[slot]
    for slot in (3, 4):  # sem_url é reescrito a cada ciclo
        assert after[slot]["updated_at"] != before[slot]["updated_at"]
    status = worker_status(db_path)
    assert status["state"] == "degraded"
    assert status["last_error_code"] == codigo
    assert status["consecutive_failures"] == 1
    assert status["last_error_message"]
    assert status["next_attempt_at"] is not None

    clock.t += 60
    youtube.enqueue(falha())
    worker.run_cycle()
    assert worker_status(db_path)["consecutive_failures"] == 2
    assert live_rows(db_path)[1] == before[1]


def test_liv_err_01_primeiro_ciclo_com_falha_nao_grava_linhas_de_video(make_worker, youtube, db_path):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    youtube.enqueue(fixture_response("error_server_500"))

    make_worker().run_cycle()

    rows = live_rows(db_path)
    assert 1 not in rows  # nunca houve dado: a api responde 503
    assert {rows[s]["status"] for s in (2, 3, 4)} == {"sem_url"}
    assert worker_status(db_path)["state"] == "degraded"


def test_liv_err_01_chave_invalida_real_400_vira_config_error(make_worker, youtube, db_path, clock):
    """`error_invalid_key` é uma resposta REAL (400 `API key not valid`)."""
    worker, before = _primed(make_worker, db_path, clock, youtube)

    youtube.enqueue(fixture_response("error_invalid_key"))
    worker.run_cycle()

    status = worker_status(db_path)
    assert status["last_error_code"] == "config_error"
    assert status["state"] == "error"
    assert live_rows(db_path)[1] == before[1]


# ---------------------------------------------------------------------------- LIV-ERR-02


def test_liv_err_02_backoff_dobra_ate_900_e_volta_no_primeiro_sucesso(
    make_worker, youtube, db_path, clock
):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    sequencia = [
        ("error_quota_exceeded", "quota_exceeded", 60),
        ("error_rate_limited", "rate_limited", 120),
        ("error_quota_exceeded", "quota_exceeded", 240),
        ("error_server_500", "http_error", 480),
        ("error_quota_exceeded", "quota_exceeded", 900),  # teto
        ("error_quota_exceeded", "quota_exceeded", 900),
    ]
    youtube.enqueue(*[fixture_response(nome) for nome, _, _ in sequencia])

    for falhas, (_, codigo, espera) in enumerate(sequencia, start=1):
        worker.run_cycle()
        status = worker_status(db_path)
        assert status["last_error_code"] == codigo
        assert status["consecutive_failures"] == falhas
        assert status["state"] == "degraded"
        assert status["next_attempt_at"] - status["last_heartbeat_at"] == espera
        clock.t += espera

    worker.run_cycle()  # fila vazia: o YouTube responde normalmente
    status = worker_status(db_path)
    assert status["state"] == "ok"
    assert status["consecutive_failures"] == 0
    assert status["last_error_code"] is None
    assert status["last_error_message"] is None
    assert status["next_attempt_at"] is None
    assert status["last_success_at"] == int(clock.t)
    assert live_rows(db_path)[1]["video_id"] == LIVE_ID


@pytest.mark.parametrize(
    ("intervalo", "falhas", "espera"),
    [
        (30, 0, 30),
        (30, 1, 60),
        (30, 2, 120),
        (30, 3, 240),
        (30, 4, 480),
        (30, 5, 900),
        (30, 50, 900),
        (10, 7, 900),
        (600, 1, 900),
        (1000, 0, 1000),
        (1000, 3, 1000),
    ],
)
def test_liv_err_02_formula_do_backoff(intervalo, falhas, espera):
    assert backoff_seconds(intervalo, falhas) == espera


def test_liv_err_02_run_espera_o_tempo_do_backoff_entre_ciclos(make_worker, youtube, db_path, clock):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    youtube.enqueue(fixture_response("error_quota_exceeded"), fixture_response("error_rate_limited"))
    times: list[float] = []

    def on_request(fake, request):
        times.append(clock.now())
        if len(times) == 4:
            worker.stop()

    youtube.on_request = on_request

    worker.run()

    gaps = [round(b - a) for a, b in zip(times, times[1:], strict=False)]
    assert gaps == [60, 120, 30]  # 2 falhas dobram a espera; o sucesso volta ao intervalo
    assert worker_status(db_path)["state"] == "stopped"


# ---------------------------------------------------------------------------- LIV-ERR-03


def test_liv_err_03_video_que_sumiu_mantem_a_linha_e_os_outros_atualizam(
    make_worker, youtube, db_path, clock
):
    worker, before = _primed(make_worker, db_path, clock, youtube)

    del youtube.catalog[LIVE_ID]  # removido/privado: a API deixa de devolvê-lo
    youtube.catalog[ENDED_ID]["statistics"]["viewCount"] = "15300000"
    worker.run_cycle()

    after = live_rows(db_path)
    assert after[1] == before[1]  # inalterada
    assert after[2]["views"] == 15300000
    assert after[2]["updated_at"] != before[2]["updated_at"]
    status = worker_status(db_path)
    assert status["state"] == "ok"
    assert status["consecutive_failures"] == 0


def test_liv_err_03_resposta_real_gravada_com_id_inexistente(make_worker, youtube, db_path):
    set_slots(db_path, [watch(ENDED_ID), watch(MISSING_ID), "", ""])
    youtube.enqueue(fixture_response("videos_partial_missing"))  # gravada: só o 1º ID veio

    make_worker().run_cycle()

    rows = live_rows(db_path)
    assert rows[1]["video_id"] == ENDED_ID
    assert rows[1]["status"] == "encerrada"
    assert 2 not in rows  # nada inventado para o vídeo ausente
    assert worker_status(db_path)["state"] == "ok"


# ---------------------------------------------------------------------------- LIV-ERR-04


def _resposta_com_item_quebrado(youtube) -> httpx.Response:
    """Item REAL com `statistics.viewCount` corrompido ("abc")."""
    item = youtube.catalog[LIVE_ID]
    item["statistics"]["viewCount"] = "abc"
    return httpx.Response(
        200,
        headers={"Content-Type": "application/json; charset=UTF-8"},
        json={"kind": "youtube#videoListResponse", "items": [item]},
    )


@pytest.mark.parametrize("origem", ["invalid_body_not_json", "invalid_body_no_items", "item_quebrado"])
def test_liv_err_04_corpo_invalido_e_falha_sem_gravar_lixo(
    origem, make_worker, youtube, db_path, clock
):
    worker, before = _primed(make_worker, db_path, clock, youtube)

    if origem == "item_quebrado":
        youtube.enqueue(_resposta_com_item_quebrado(youtube))
    else:
        youtube.enqueue(fixture_response(origem))
    worker.run_cycle()

    after = live_rows(db_path)
    assert after[1] == before[1]
    assert after[2] == before[2]
    status = worker_status(db_path)
    assert status["state"] == "degraded"
    assert status["last_error_code"] == "invalid_response"
    assert status["consecutive_failures"] == 1


# ---------------------------------------------------------------------------- LIV-SEC-03


def _falhas_com_a_chave(api_key: str):
    url_com_chave = f"https://www.googleapis.com/youtube/v3/videos?id=x&key={api_key}"
    return [
        httpx.ConnectError(f"falha ao conectar em {url_com_chave}"),
        httpx.ReadTimeout(f"timeout em {url_com_chave}"),
        httpx.RemoteProtocolError(f"erro em {url_com_chave}"),
        httpx.Response(500, text=f"erro interno ao atender {url_com_chave}"),
        httpx.Response(403, json={"error": {"errors": [{"reason": "forbidden"}], "message": api_key}}),
        httpx.Response(200, text=f"<html>{api_key}</html>"),
    ]


def test_liv_sec_03_chave_nunca_aparece_em_logs_nem_no_status(
    make_worker, youtube, db_path, clock, api_key, caplog
):
    caplog.set_level(logging.DEBUG)
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    mensagens = []

    for falha in _falhas_com_a_chave(api_key):
        youtube.enqueue(falha)
        worker.run_cycle()
        status = worker_status(db_path)
        assert status["state"] in ("degraded", "error")
        mensagens.append(status["last_error_message"])
        clock.t += 1

    assert all(mensagens)
    for texto in [*mensagens, caplog.text]:
        assert api_key not in texto
        assert "key=" not in texto
    assert len(caplog.records) > 0
    assert all(record.exc_info is None for record in caplog.records)  # sem traceback com a URL


def test_liv_sec_03_repr_da_configuracao_nao_mostra_a_chave(make_worker, api_key):
    worker = make_worker()

    assert api_key not in repr(worker.config)
    assert api_key not in str(worker.config)


def test_liv_sec_03_execucao_completa_nao_loga_a_chave(make_worker, youtube, db_path, api_key, caplog):
    caplog.set_level(logging.DEBUG)
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    youtube.on_request = lambda fake, request: worker.stop()

    worker.run()

    assert "Lote: 1 vídeo(s) consultado(s)" in caplog.text  # o worker realmente logou o ciclo
    assert api_key not in caplog.text
