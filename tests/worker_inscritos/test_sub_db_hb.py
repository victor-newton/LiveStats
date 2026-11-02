"""SUB-DB-01/02 (autorizador, isolamento), SUB-HB-01 (heartbeat), SUB-START-01 (esquema) e SUB-CFG-02 (intervalo)."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from worker_inscritos import healthcheck
from worker_inscritos.config import ConfigError, load_config
from worker_inscritos.store import StatusRow, Store, connect, iso_utc, schema_ready, wait_for_schema
from sub_support import REPO_ROOT, cookie, create_db, query

PASTA_WORKER = REPO_ROOT / "worker_inscritos"


# ---------------------------------------------------------------------- SUB-DB-01
def test_sub_db_01_o_que_o_worker_pode_fazer(env, add_cookies):
    novo = add_cookies([cookie("a", "1")])
    con = connect(str(env.db))
    store = Store(con)
    try:
        # ler cookie_sets e schema_meta
        assert store.latest_cookie_set_id() == novo
        assert store.get_cookie_set(novo).id == novo
        assert con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone() == ("1",)
        # escrever subscriber_reading e worker_status (duas vezes: o UPSERT cobre INSERT e UPDATE)
        for _ in range(2):
            store.save_reading("460600", "460600", iso_utc(1_700_000_000))
        assert query(env.db, "SELECT id, value FROM subscriber_reading") == [(1, "460600")]
        linha = StatusRow("ok", 1_700_000_000, None, 0, 10, None, None, "ok", novo, "t")
        store.write_status(linha)
        store.write_status(linha)
        assert query(env.db, "SELECT worker, state, cookies_applied_id FROM worker_status") == [("inscritos", "ok", novo)]
    finally:
        con.close()


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO slots (slot, url) VALUES (9, 'x')",
        "UPDATE slots SET url = 'x' WHERE slot = 1",
        "DELETE FROM slots",
        "SELECT * FROM slots",
        "SELECT * FROM live_stats",
        "UPDATE live_stats SET title = 'x'",
        "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at) "
        "VALUES (zeroblob(60), '0123456789abcdef', 1, 'x', '2026-01-01T00:00:00Z')",
        "UPDATE cookie_sets SET uploaded_by = 'x'",
        "DELETE FROM cookie_sets",
        "SELECT value, raw FROM subscriber_reading",  # so a chave do UPSERT e legivel
        "SELECT state FROM worker_status",
        "CREATE TABLE intruso (x)",
        "DROP TABLE slots",
        "ATTACH DATABASE ':memory:' AS outro",
        "PRAGMA journal_mode=DELETE",
    ],
)
def test_sub_db_01_o_que_o_worker_nao_pode_fazer(env, add_cookies, sql):
    add_cookies([cookie("a", "1")])
    con = connect(str(env.db))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|is prohibited"):
            con.execute(sql).fetchall()
    finally:
        con.close()


def test_sub_db_01_conexao_nao_cria_o_arquivo_do_banco(tmp_path):
    inexistente = tmp_path / "nao_existe.db"
    with pytest.raises(sqlite3.Error):
        connect(str(inexistente))
    assert not inexistente.exists()


# ---------------------------------------------------------------------- SUB-DB-02
def test_sub_db_02_worker_nao_toca_o_estado_de_admin():
    proibidos = ["admin.db", "ADMIN_DB_PATH", "admin_sessions", "login_attempts", "/admin-state"]
    achados = []
    for caminho in sorted(PASTA_WORKER.rglob("*.py")):
        texto = caminho.read_text(encoding="utf-8")
        achados += [(caminho.name, p) for p in proibidos if p in texto]
    assert achados == []


def test_sub_db_02_configuracao_so_conhece_db_path(env):
    cfg = load_config({**env.env, "ADMIN_DB_PATH": "/admin-state/admin.db"})
    assert not any("admin" in campo.lower() for campo in cfg.__dataclass_fields__)
    assert cfg.db_path == str(env.db)


# ---------------------------------------------------------------------- SUB-HB-01
class RelogioManual:
    def __init__(self, t: float):
        self.t = t

    def __call__(self) -> float:
        return self.t


def status_row(env):
    return query(
        env.db,
        "SELECT state, last_heartbeat_at, last_success_at, consecutive_failures, interval_seconds, version "
        "FROM worker_status WHERE worker='inscritos'",
    )


def test_sub_hb_01_heartbeat_a_cada_ciclo_mesmo_sem_novidade(env, make_worker, add_cookies):
    relogio = RelogioManual(1_800_000_000.0)
    add_cookies([cookie("a", "1")])
    worker = make_worker(clock=relogio, SCRAPE_INTERVAL="8")

    worker.step()
    estado, hb1, ok1, falhas, intervalo, versao = status_row(env)[0]
    assert (estado, hb1, ok1, falhas, intervalo) == ("ok", 1_800_000_000, 1_800_000_000, 0, 8)
    assert versao

    relogio.t += 8  # mesmo valor lido, nenhuma novidade: o heartbeat ainda avanca
    worker.step()
    estado, hb2, ok2, *_ = status_row(env)[0]
    assert (estado, hb2, ok2) == ("ok", 1_800_000_008, 1_800_000_008)


def test_sub_hb_01_heartbeat_tambem_nas_falhas_e_sem_cookies(env, make_worker, add_cookies):
    relogio = RelogioManual(1_800_000_000.0)
    worker = make_worker(clock=relogio)
    worker.step()  # sem cookies
    assert status_row(env)[0][:2] == ("error", 1_800_000_000)
    relogio.t += 5
    worker.step()
    assert status_row(env)[0][:2] == ("error", 1_800_000_005)

    env.server.page_name = "sem_contador.html"
    add_cookies([cookie("a", "1")])
    relogio.t += 5
    worker.step()
    estado, hb, ok, falhas, *_ = status_row(env)[0]
    assert (estado, hb, ok, falhas) == ("degraded", 1_800_000_010, None, 1)


def test_sub_hb_01_encerrar_grava_stopped(env, make_worker):
    worker = make_worker()
    worker.stop.set()
    worker.run()
    assert status_row(env)[0][0] == "stopped"


def rodar_healthcheck(db):
    ambiente = {**os.environ, "DB_PATH": str(db), "PYTHONPATH": str(REPO_ROOT)}
    return subprocess.run(
        [sys.executable, "-m", "worker_inscritos.healthcheck"],
        env=ambiente, cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )


def test_sub_hb_02_healthcheck_exit_0_com_heartbeat_recente(env, make_worker):
    make_worker(SCRAPE_INTERVAL="10").step()  # heartbeat de agora (sem cookies: o heartbeat vale igual)
    resultado = rodar_healthcheck(env.db)
    assert resultado.returncode == 0, resultado.stderr


def test_sub_hb_02_healthcheck_exit_1_com_heartbeat_velho(env, make_worker):
    make_worker(clock=lambda: time.time() - 70, SCRAPE_INTERVAL="10").step()  # limite = 3*10+30 = 60 s
    resultado = rodar_healthcheck(env.db)
    assert resultado.returncode == 1
    assert "velho" in resultado.stderr


def test_sub_hb_02_healthcheck_exit_1_sem_heartbeat_ou_sem_banco(env, tmp_path):
    assert rodar_healthcheck(env.db).returncode == 1  # banco existe, nenhuma linha em worker_status
    inexistente = tmp_path / "nada" / "x.db"
    assert rodar_healthcheck(inexistente).returncode == 1
    assert not inexistente.exists()


def test_sub_hb_02_limite_exato_3x_intervalo_mais_30(env, make_worker):
    relogio = RelogioManual(1_800_000_000.0)
    make_worker(clock=relogio, SCRAPE_INTERVAL="10").step()
    assert healthcheck.check(str(env.db), now=1_800_000_000 + 59)[0] is True
    assert healthcheck.check(str(env.db), now=1_800_000_000 + 60)[0] is False
    make_worker(clock=relogio, SCRAPE_INTERVAL="20").step()  # intervalo maior -> limite 90 s
    assert healthcheck.check(str(env.db), now=1_800_000_000 + 89)[0] is True
    assert healthcheck.check(str(env.db), now=1_800_000_000 + 90)[0] is False


# ---------------------------------------------------------------------- SUB-START-01
def tabelas(db) -> list[str]:
    return [r[0] for r in query(db, "SELECT name FROM sqlite_master WHERE type='table'")]


def test_sub_start_01_espera_o_esquema_sem_cria_lo(tmp_path):
    db = tmp_path / "vazio.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 1")  # forca o arquivo a existir, sem nenhuma tabela
    con.close()
    assert tabelas(db) == []

    parar = threading.Event()
    resultado: list[bool] = []
    fio = threading.Thread(target=lambda: resultado.append(wait_for_schema(str(db), parar, poll=0.1)))
    fio.start()
    time.sleep(0.8)
    assert fio.is_alive() and resultado == [], "o worker deve esperar enquanto nao houver schema_meta"
    assert tabelas(db) == [], "o worker nunca cria tabelas"
    assert schema_ready(str(db)) is False

    create_db(db)  # a api aplica db/schema.sql
    fio.join(timeout=10)
    assert not fio.is_alive()
    assert resultado == [True]
    assert schema_ready(str(db)) is True


def test_sub_start_01_banco_inexistente_nao_e_criado_e_parar_interrompe_a_espera(tmp_path):
    db = tmp_path / "ainda_nao_existe.db"
    parar = threading.Event()
    resultado: list[bool] = []
    fio = threading.Thread(target=lambda: resultado.append(wait_for_schema(str(db), parar, poll=0.1)))
    fio.start()
    time.sleep(0.5)
    assert fio.is_alive() and not db.exists()
    parar.set()
    fio.join(timeout=5)
    assert resultado == [False]
    assert not db.exists()


def test_sub_db_01_espera_o_banco_ocupado_em_vez_de_falhar(env):
    # busy_timeout de 5 s por conexao: uma escrita concorrente de ~1 s nao pode derrubar o worker
    con = connect(str(env.db))
    store = Store(con)
    outro = sqlite3.connect(env.db, isolation_level=None, check_same_thread=False)
    outro.execute("BEGIN IMMEDIATE")
    threading.Timer(1.0, lambda: outro.execute("COMMIT")).start()
    inicio = time.monotonic()
    try:
        store.save_reading("460600", "460600", iso_utc(1_700_000_000))
    finally:
        con.close()
    assert time.monotonic() - inicio >= 0.8
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]
    outro.close()


# ---------------------------------------------------------------------- SUB-CFG-02
def test_sub_cfg_02_scrape_interval_padrao_10_minimo_5(env):
    sem = {k: v for k, v in env.env.items() if k != "SCRAPE_INTERVAL"}
    assert load_config(sem).scrape_interval == 10
    for ruim in ("4", "0", "-1", "abc", "5.5", "3601"):
        with pytest.raises(ConfigError):
            load_config({**env.env, "SCRAPE_INTERVAL": ruim})
    assert load_config({**env.env, "SCRAPE_INTERVAL": "5"}).scrape_interval == 5
    assert load_config({**env.env, "SCRAPE_INTERVAL": "3600"}).scrape_interval == 3600


def test_sub_cfg_02_intervalo_e_respeitado_entre_ciclos(env, make_worker):
    worker = make_worker(SCRAPE_INTERVAL="5")  # sem cookies: o ciclo e barato e nao abre navegador
    instantes: list[float] = []
    step_real = worker.step

    def step_contado():
        instantes.append(time.monotonic())
        step_real()

    worker.step = step_contado
    fio = threading.Thread(target=worker.run)
    fio.start()
    time.sleep(6.2)
    worker.stop.set()
    fio.join(timeout=10)
    assert not fio.is_alive()
    assert len(instantes) == 2
    assert 4.9 <= instantes[1] - instantes[0] < 5.6
