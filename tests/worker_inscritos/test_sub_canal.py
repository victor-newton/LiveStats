"""C11 (D35): o canal vem de `settings.channel_id` (painel); o ambiente so vale com a tabela vazia.

IDs: SUB-CAN-01..05. Servidor de fixture local; banco, Chromium e Playwright reais.
"""

from __future__ import annotations

import sqlite3

import pytest
from sub_support import PLACEHOLDER_CHANNEL, cookie, query

NOVO = "UC" + "N" * 22
OUTRO = "UC" + "O" * 22


def set_channel(db, value: str) -> None:
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO settings (key, value) VALUES ('channel_id', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (value,),
    )
    con.commit()
    con.close()


def canais(env) -> list[str]:
    """Canal de cada abertura do Studio, na ordem."""
    return [r["path"].split("/")[2] for r in env.server.studio_requests()]


def test_sub_can_01_tabela_vazia_usa_o_canal_do_ambiente(env, make_worker, add_cookies):
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    assert set(canais(env)) == {PLACEHOLDER_CHANNEL}
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]


def test_sub_can_02_canal_do_banco_vence_o_do_ambiente_na_primeira_carga(env, make_worker, add_cookies):
    set_channel(env.db, NOVO)
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    assert set(canais(env)) == {NOVO}
    assert query(env.db, "SELECT state, last_error_code FROM worker_status") == [("ok", None)]


def test_sub_can_03_troca_de_canal_no_banco_recarrega_o_studio_do_canal_novo(env, make_worker, add_cookies):
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    worker.step()  # ciclo normal: nao recarrega
    assert set(canais(env)) == {PLACEHOLDER_CHANNEL}
    antes = env.server.studio_hits()
    cookies_antes = query(env.db, "SELECT COUNT(*) FROM cookie_sets")

    set_channel(env.db, NOVO)
    worker.step()
    assert env.server.studio_hits() > antes, "troca de canal deve reabrir o Studio"
    assert canais(env)[-1] == NOVO and canais(env)[antes - 1] == PLACEHOLDER_CHANNEL
    assert query(env.db, "SELECT COUNT(*) FROM cookie_sets") == cookies_antes
    assert env.server.requests[-1]["cookie"], "os cookies sao reinjetados na recarga"

    depois = env.server.studio_hits()
    worker.step()
    assert env.server.studio_hits() == depois, "sem nova troca, nao recarrega de novo"

    set_channel(env.db, OUTRO)
    worker.step()
    assert canais(env)[-1] == OUTRO
    assert query(env.db, "SELECT state FROM worker_status") == [("ok",)]


def test_sub_can_04_valor_invalido_no_banco_cai_para_o_ambiente(env, make_worker, add_cookies):
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    con = sqlite3.connect(env.db)  # o CHECK so limita o tamanho; a validacao de formato e da api
    con.execute("INSERT INTO settings (key, value) VALUES ('channel_id', 'abc')")
    con.commit()
    con.close()
    antes = env.server.studio_hits()
    worker.step()
    assert env.server.studio_hits() == antes, "valor invalido nao troca o canal"
    assert set(canais(env)) == {PLACEHOLDER_CHANNEL}


def test_sub_can_05_autorizador_le_settings_mas_nao_escreve(env, make_worker):
    worker = make_worker()
    set_channel(env.db, NOVO)
    assert worker.store.get_channel_id() == NOVO
    with pytest.raises(sqlite3.DatabaseError):
        worker.store.con.execute("INSERT INTO settings (key, value) VALUES ('subs_font', 'Arial')")
    with pytest.raises(sqlite3.DatabaseError):
        worker.store.con.execute("UPDATE settings SET value = 'x'")
    with pytest.raises(sqlite3.DatabaseError):
        worker.store.con.execute("DELETE FROM settings")
