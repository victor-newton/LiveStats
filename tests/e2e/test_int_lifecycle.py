"""INT-05 (persistencia), INT-06 (subida do zero), INT-07 (degradacao) pela pilha real."""

from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
from e2e_stack import (
    CONTAINERS,
    LIVE_INTERVAL,
    SCRAPE_INTERVAL,
    VID_LIVE,
    docker,
    parse_ts,
    wait_until,
)

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

READ_STATUS = r"""
import json, sqlite3
con = sqlite3.connect("file:/data/livestats.db?mode=ro", uri=True, timeout=10)
rows = {r[0]: r[1] for r in con.execute("SELECT worker, last_heartbeat_at FROM worker_status")}
upd = con.execute("SELECT updated_at FROM live_stats WHERE slot = 1").fetchone()
print(json.dumps({"hb": rows, "live1": upd[0] if upd else None}))
"""


def _slot_urls(stack) -> list[tuple[int, str]]:
    body = json.loads(stack.api("GET", "/api/admin/slots")["body"])
    return [(s["slot"], s["url"]) for s in body["slots"]]


def _db_view(stack) -> dict:
    return json.loads(stack.py("worker-lives", READ_STATUS).stdout)


def _started_at(stack, name: str) -> datetime:
    raw = stack.inspect(name)["State"]["StartedAt"]
    return datetime.fromisoformat(raw[:26].rstrip("Z") + "+00:00")


def test_int_06_subida_do_zero_saudavel_em_ate_120s_na_ordem_certa(stack):
    # a pilha foi criada do zero por esta sessao (volumes novos): `up -d --wait` so volta com todos healthy
    assert stack.startup_seconds <= 120, f"subida levou {stack.startup_seconds:.0f}s"
    for name in CONTAINERS:
        state = stack.inspect(name)["State"]
        assert state["Running"] and state["Health"]["Status"] == "healthy", (name, state["Health"]["Status"])
        assert stack.inspect(name)["RestartCount"] == 0, f"{name} reiniciou durante a subida"

    # api -> web/workers: os dependentes so iniciam depois da api saudavel
    api = _started_at(stack, "api")
    for dependent in ("web", "worker-lives", "worker-inscritos"):
        assert _started_at(stack, dependent) >= api, f"{dependent} subiu antes da api"

    # volumes novos (sem permissao herdada de execucoes anteriores) e escreviveis pelos tres processos
    for volume in ("livestats-t8-data", "livestats-t8-admin"):
        created = json.loads(docker("volume", "inspect", volume).stdout)[0]["CreatedAt"]
        assert parse_ts(created.replace("+00:00", "Z")).timestamp() >= stack.up_started_at - 5, volume
    # nenhum erro de permissao nos logs de subida
    for name in CONTAINERS:
        text = stack.logs(name).lower()
        assert "permission denied" not in text and "readonly database" not in text, name


def test_int_05_persistencia_apos_reiniciar_api_e_workers(stack):
    stack.ensure_lives()
    stack.ensure_subs("460600")
    session = stack.session
    slots_before = _slot_urls(stack)
    csrf_before = json.loads(stack.api("GET", "/api/admin/session")["body"])["csrf_token"]
    assert csrf_before == session.csrf
    reading_before = stack.public_json("/inscritos.json")
    assert stack.workers_status()["cookies"]["count"] == 2

    stack.compose("restart", "api", timeout=200)
    stack.wait_healthy(("api", "web"))
    # a sessao de admin continua valida (vive no volume livestats-admin) e os dados continuam la, ANTES de os workers voltarem
    after_session = stack.api("GET", "/api/admin/session")
    assert after_session["status"] == 200 and json.loads(after_session["body"])["csrf_token"] == csrf_before
    assert _slot_urls(stack) == slots_before
    assert stack.public_json("/inscritos.json")["inscritos"] == reading_before["inscritos"]
    assert stack.workers_status()["cookies"]["count"] == 2

    stack.restart_workers()
    # workers retomam sozinhos: relem os slots, decifram os cookies guardados e voltam a gravar
    wait_until(lambda: stack.public_json("/live1.json")[0]["video_id"] == VID_LIVE, timeout=3 * LIVE_INTERVAL)
    wait_until(
        lambda: stack.workers_status()["cookies"]["applied"]
        and stack.public_json("/inscritos.json")["atualizado_em"] != reading_before["atualizado_em"],
        timeout=120,
        interval=1,
    )
    assert _slot_urls(stack) == slots_before
    assert stack.api("GET", "/api/admin/session")["status"] == 200


def test_int_07_degradacao_com_api_parada_e_com_worker_lives_parado(stack):
    stack.ensure_lives()
    stack.ensure_subs("460600")

    # --- (a) api parada: os workers continuam gravando (heartbeats avancam) e retomam ao voltar
    hb_before = _db_view(stack)
    docker("stop", CONTAINERS["api"], timeout=120)
    try:
        time.sleep(LIVE_INTERVAL + 4)
        assert stack.inspect("worker-lives")["State"]["Running"] and stack.inspect("worker-inscritos")["State"]["Running"]
        hb_during = _db_view(stack)
        assert hb_during["hb"]["lives"] > hb_before["hb"]["lives"], "worker-lives parou de gravar com a api parada"
        assert hb_during["hb"]["inscritos"] > hb_before["hb"]["inscritos"], "worker-inscritos parou de gravar com a api parada"
        assert parse_ts(hb_during["live1"]) > parse_ts(hb_before["live1"]), "live_stats nao foi atualizado com a api parada"
    finally:
        docker("start", CONTAINERS["api"], timeout=120)
    stack.wait_healthy(("api", "web"))
    # api de volta: o web volta a falar com ela e o que os workers gravaram durante a queda aparece
    wait_until(lambda: stack.public("/health")["status"] == 200, timeout=60, interval=1)
    live1 = stack.public_json("/live1.json")[0]
    assert parse_ts(live1["atualizado_em"]) >= parse_ts(hb_during["live1"])
    status = stack.workers_status()
    assert status["workers"]["lives"]["state"] == "ok" and status["workers"]["inscritos"]["state"] == "ok"

    # --- (b) worker-lives parado: o ultimo dado continua servido e o painel mostra heartbeat velho
    served = stack.public_json("/live1.json")
    docker("stop", CONTAINERS["worker-lives"], timeout=120)
    try:
        time.sleep(3 * LIVE_INTERVAL + 5)
        assert stack.public_json("/live1.json")[0]["video_id"] == served[0]["video_id"]
        assert stack.public_json("/live1.json")[0]["titulo"] == served[0]["titulo"]
        st = stack.workers_status()
        age = (parse_ts(st["server_time"]) - parse_ts(st["workers"]["lives"]["last_heartbeat_at"])).total_seconds()
        assert age >= 3 * LIVE_INTERVAL, f"heartbeat do lives deveria estar velho, idade={age}s"
        inscritos_age = (parse_ts(st["server_time"]) - parse_ts(st["workers"]["inscritos"]["last_heartbeat_at"])).total_seconds()
        assert inscritos_age <= 3 * SCRAPE_INTERVAL, "o outro worker nao deveria ser afetado"
        assert stack.public("/inscritos.json")["status"] == 200
    finally:
        stack.compose("start", "worker-lives", "fake-lives", timeout=200)
        stack.restart_workers(("worker-lives",))
    wait_until(
        lambda: (lambda s: (parse_ts(s["server_time"]) - parse_ts(s["workers"]["lives"]["last_heartbeat_at"])).total_seconds() < LIVE_INTERVAL + 5)(
            stack.workers_status()
        ),
        timeout=60,
        interval=1,
    )
