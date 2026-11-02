"""LIV-HB-*, LIV-CFG-01, LIV-START-01: heartbeat, healthcheck, intervalo e espera pelo esquema."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from lives_helpers import (
    LIVE_ID,
    REPO_ROOT,
    create_schema,
    live_rows,
    set_slots,
    watch,
    worker_status,
)
from worker_lives import __version__
from worker_lives.config import DEFAULT_INTERVAL, Config, ConfigError
from worker_lives.healthcheck import check


def _env(tmp_path: Path, key_file: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("YOUTUBE_", "LIVE_", "DB_"))}
    env.update({"DB_PATH": str(tmp_path / "livestats.db"), "YOUTUBE_API_KEY_FILE": str(key_file)})
    env.update(extra)
    return env


# ---------------------------------------------------------------------------- LIV-HB-01


def test_liv_hb_01_heartbeat_a_cada_ciclo_mesmo_sem_novidade(make_worker, youtube, db_path, clock):
    set_slots(db_path, ["", "", "", ""])  # nenhum vídeo: nenhuma chamada à API
    worker = make_worker()

    worker.run_cycle()
    first = worker_status(db_path)
    clock.t += 30
    worker.run_cycle()
    second = worker_status(db_path)

    assert youtube.requests == []
    assert first["state"] == second["state"] == "ok"
    assert first["last_heartbeat_at"] == int(clock.t - 30)
    assert second["last_heartbeat_at"] == int(clock.t)
    assert second["last_success_at"] == int(clock.t)
    assert second["consecutive_failures"] == 0
    assert second["interval_seconds"] == 30
    assert second["next_attempt_at"] is None
    assert second["last_error_code"] is None and second["last_error_message"] is None
    assert second["auth_state"] is None and second["cookies_applied_id"] is None
    assert second["version"] == __version__


def test_liv_hb_01_interval_seconds_e_o_intervalo_configurado(make_worker, youtube, db_path):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])

    make_worker(interval=45).run_cycle()

    assert worker_status(db_path)["interval_seconds"] == 45


def test_liv_hb_01_start_e_stop_aparecem_no_status(make_worker, youtube, db_path, clock):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    seen = {}

    def on_request(fake, request):
        seen["during"] = worker_status(db_path)
        worker.stop()

    youtube.on_request = on_request

    worker.run()

    assert seen["during"]["state"] == "starting"  # gravado antes do 1º ciclo
    final = worker_status(db_path)
    assert final["state"] == "stopped"
    assert final["last_success_at"] is not None
    assert 1 in live_rows(db_path)


def test_liv_hb_01_heartbeat_continua_durante_a_espera_do_backoff(
    make_worker, youtube, db_path, clock
):
    from lives_helpers import fixture_response

    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker()
    youtube.enqueue(fixture_response("error_quota_exceeded"))
    start = int(clock.t)
    observed = []

    def hook(fake_clock):
        status = worker_status(db_path)
        observed.append(
            (int(fake_clock.t), status["last_heartbeat_at"], status["state"], status["last_error_code"])
        )
        if len(observed) == 2:
            worker.stop()

    clock.hook = hook

    worker.run()

    # espera de 60 s em fatias de 30 s: o heartbeat é renovado no meio da espera e o erro é mantido
    assert clock.sleeps[:2] == [30, 30]
    (t1, heartbeat1, state1, code1), (_, heartbeat2, state2, code2) = observed
    assert heartbeat1 == start  # durante a 1ª fatia ainda vale o heartbeat do ciclo
    assert heartbeat2 == t1 == start + 30  # renovado ao fim da 1ª fatia
    assert (state1, code1) == (state2, code2) == ("degraded", "quota_exceeded")


# ---------------------------------------------------------------------------- LIV-HB-02


def _write_heartbeat(db_path: Path, age_seconds: float, interval: int = 30) -> None:
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO worker_status (worker, state, last_heartbeat_at,"
                " interval_seconds, version) VALUES ('lives', 'ok', ?, ?, 't')",
                (int(time.time() - age_seconds), interval),
            )
    finally:
        conn.close()


def _healthcheck(tmp_path: Path, key_file: Path, db_path: Path) -> subprocess.CompletedProcess:
    env = _env(tmp_path, key_file, DB_PATH=str(db_path))
    return subprocess.run(
        [sys.executable, "-m", "worker_lives.healthcheck"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
    )


def test_liv_hb_02_exit_0_com_heartbeat_recente(tmp_path, key_file, db_path):
    _write_heartbeat(db_path, age_seconds=5)

    assert _healthcheck(tmp_path, key_file, db_path).returncode == 0


def test_liv_hb_02_exit_1_com_heartbeat_velho_ou_ausente(tmp_path, key_file, db_path):
    # intervalo 30 s => limite 3*30+30 = 120 s
    _write_heartbeat(db_path, age_seconds=100)
    assert _healthcheck(tmp_path, key_file, db_path).returncode == 0
    _write_heartbeat(db_path, age_seconds=130)
    result = _healthcheck(tmp_path, key_file, db_path)
    assert result.returncode == 1
    assert "heartbeat" in result.stderr

    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("DELETE FROM worker_status")
    conn.close()
    assert _healthcheck(tmp_path, key_file, db_path).returncode == 1  # sem linha


def test_liv_hb_02_exit_1_sem_banco(tmp_path, key_file):
    result = _healthcheck(tmp_path, key_file, tmp_path / "nao-existe.db")

    assert result.returncode == 1
    assert not (tmp_path / "nao-existe.db").exists()  # o healthcheck não cria o banco


def test_liv_hb_02_limite_e_tres_intervalos_mais_trinta(db_path):
    _write_heartbeat(db_path, age_seconds=0, interval=10)
    conn = sqlite3.connect(db_path)
    (heartbeat,) = conn.execute("SELECT last_heartbeat_at FROM worker_status").fetchone()
    conn.close()

    assert check(str(db_path), heartbeat + 60)[0] is True  # 3*10+30 = 60
    assert check(str(db_path), heartbeat + 61)[0] is False


# ---------------------------------------------------------------------------- LIV-CFG-01


def _config(key_file, **env):
    return Config.from_env({"YOUTUBE_API_KEY_FILE": str(key_file), **env})


def test_liv_cfg_01_padrao_30_e_validacao_do_minimo(key_file):
    assert DEFAULT_INTERVAL == 30
    assert _config(key_file).interval == 30
    assert _config(key_file, LIVE_INTERVAL="").interval == 30
    assert _config(key_file, LIVE_INTERVAL="10").interval == 10
    assert _config(key_file, LIVE_INTERVAL="3600").interval == 3600
    for invalido in ("9", "0", "-30", "abc", "1.5", "3601"):
        with pytest.raises(ConfigError, match="LIVE_INTERVAL"):
            _config(key_file, LIVE_INTERVAL=invalido)


def test_liv_cfg_01_intervalo_respeitado_entre_ciclos(make_worker, youtube, db_path, clock):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    worker = make_worker(interval=45)
    times = []

    def on_request(fake, request):
        times.append(clock.now())
        if len(times) == 3:
            worker.stop()

    youtube.on_request = on_request

    worker.run()

    assert [round(b - a) for a, b in zip(times, times[1:], strict=False)] == [45, 45]


# ---------------------------------------------------------------------------- LIV-START-01


def test_liv_start_01_espera_o_esquema_sem_criar_tabelas(make_worker, youtube, tmp_path, clock):
    path = tmp_path / "ainda-sem-esquema.db"
    sqlite3.connect(path).close()  # arquivo existe, mas vazio: a api ainda não aplicou o esquema
    worker = make_worker(path=path)
    tables_seen = []

    def hook(fake_clock):
        if len(fake_clock.sleeps) < 3:
            conn = sqlite3.connect(path)
            tables_seen.append(conn.execute("SELECT name FROM sqlite_master").fetchall())
            conn.close()
        else:
            if len(fake_clock.sleeps) == 3:  # a api cria o esquema e o admin salva uma URL
                create_schema(path)
                set_slots(path, [watch(LIVE_ID), "", "", ""])

    clock.hook = hook
    youtube.on_request = lambda fake, request: worker.stop()

    worker.run()

    assert clock.sleeps[:3] == [5, 5, 5]
    assert tables_seen == [[], []]  # esperou sem criar nenhuma tabela
    assert len(youtube.requests) == 1  # e começou assim que o esquema existiu
    assert live_rows(path)[1]["video_id"] == LIVE_ID


def test_liv_start_01_nao_cria_o_arquivo_do_banco(make_worker, youtube, tmp_path, clock):
    path = tmp_path / "nao-existe.db"
    worker = make_worker(path=path)
    existed = []

    def hook(fake_clock):
        existed.append(path.exists())
        if len(existed) == 3:
            worker.stop()

    clock.hook = hook

    worker.run()

    assert existed == [False, False, False]
    assert not path.exists()
    assert youtube.requests == []
