"""C14 (D37): a sessao do Studio e renovada e persistida; recargas nunca trocam uma sessao viva por cookies velhos.

IDs: SUB-REN-01..12. Fronteira simulada: SOMENTE o YouTube (servidor local que emite `Set-Cookie` como o Google faz).
Chromium, Playwright, SQLite e PyNaCl sao reais; o relogio do worker e injetavel (parametro `clock` de criar_worker).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path

import pytest
from nacl.public import PrivateKey, SealedBox
from sub_support import (
    PLACEHOLDER_CHANNEL,
    cookie,
    dump_text,
    insert_cookie_set,
    public_key_id,
    query,
    write_pub_file,
)

from worker_inscritos.config import ConfigError, ler_chave_publica, load_config
from worker_inscritos.store import connect

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SEM_CONTADOR = (FIXTURES / "sem_contador.html").read_text("utf-8")
NOVO = "UC" + "N" * 22


class Relogio:
    def __init__(self) -> None:
        self.t = time.time()

    def __call__(self) -> float:
        return self.t

    def avancar(self, segundos: float) -> None:
        self.t += segundos


def renova_sempre(env, prefixo: str = "r") -> list[int]:
    """O Studio falso emite um valor NOVO de SID a cada abertura (r1, r2...). Devolve a lista de aberturas."""
    contador: list[int] = []

    def hook() -> None:
        contador.append(1)
        env.server.set_cookie_headers = [f"SID={prefixo}{len(contador)}; Path=/"]

    env.server.on_studio_request = hook
    return contador


def linhas(env) -> list[tuple]:
    return query(env.db, "SELECT id, source, renewed_from, uploaded_by, cookie_count FROM cookie_sets ORDER BY id")


def decifrado(env, set_id: int) -> list[dict]:
    (blob,) = query(env.db, "SELECT ciphertext FROM cookie_sets WHERE id = ?", (set_id,))[0]
    return json.loads(SealedBox(env.keypair).decrypt(bytes(blob)).decode("utf-8"))


def cookie_do_servidor(env, indice: int = -1) -> str:
    return env.server.studio_requests()[indice]["cookie"]


# ------------------------------------------------------------------------------------------ SUB-REN-01
def test_sub_ren_01_recarga_por_30_falhas_nao_apaga_a_sessao_viva(env, make_worker, add_cookies):
    """O servidor renova SID na 1a abertura; leituras ruins (sem contador) nunca gravam renovacao; a recarga por
    30 falhas deve reabrir o Studio com o cookie RENOVADO do Chromium, nao com o conjunto colado."""
    env.server.html_override = SEM_CONTADOR
    renova_sempre(env)
    add_cookies([cookie("SID", "colado"), cookie("OUTRO", "fixo")])
    worker = make_worker()

    for _ in range(30):
        worker.step()
    abertura_1 = cookie_do_servidor(env, 0)
    assert "SID=colado" in abertura_1
    antes = env.server.studio_hits()

    assert query(env.db, "SELECT COUNT(*) FROM subscriber_reading") == [(0,)]
    assert worker.falhas == 0, "a recarga por falhas aconteceu no 30o ciclo"
    ultima = cookie_do_servidor(env)
    assert antes >= 2, "houve ao menos a recarga por falhas"
    assert "SID=colado" not in ultima and "SID=r" in ultima, ultima
    assert "OUTRO=fixo" in ultima
    assert linhas(env) == [(1, "admin", None, "admin", 2)], "leituras ruins nao geram renovacao"


# ------------------------------------------------------------------------------------------ SUB-REN-02
def test_sub_ren_02_renovacao_gravada_cifrada_com_origem_worker_e_5_campos(env, make_worker, add_cookies):
    renova_sempre(env)
    add_cookies([cookie("SID", "colado"), cookie("OUTRO", "fixo", secure=False)])
    worker = make_worker()
    worker.step()

    assert linhas(env) == [(2, "worker", 1, "worker", 2)]
    (key_id, created_at) = query(env.db, "SELECT key_id, created_at FROM cookie_sets")[0]
    assert key_id == public_key_id(env.keypair.public_key)
    assert created_at.endswith("Z")
    cookies = decifrado(env, 2)
    assert all(set(c) == {"name", "value", "domain", "path", "secure"} for c in cookies)
    por_nome = {c["name"]: c for c in cookies}
    assert por_nome["SID"] == {"name": "SID", "value": "r1", "domain": "127.0.0.1", "path": "/", "secure": False}
    assert por_nome["OUTRO"]["value"] == "fixo"
    assert query(env.db, "SELECT cookies_applied_id FROM worker_status") == [(2,)]
    assert worker.applied_id == 2


# ------------------------------------------------------------------------------------------ SUB-REN-03
def test_sub_ren_03_intervalo_minimo_de_10_minutos_entre_renovacoes(env, make_worker, add_cookies):
    relogio = Relogio()
    renova_sempre(env)
    add_cookies([cookie("SID", "colado")])
    worker = make_worker(clock=relogio)

    worker.step()
    assert [l[0] for l in linhas(env)] == [2]

    worker.session.reload_studio()  # o Google devolve outro SID (r2); a sessao do Chromium muda de novo
    relogio.avancar(599)
    worker.step()
    assert [l[0] for l in linhas(env)] == [2], "antes de 10 min nao grava"

    relogio.avancar(1)
    worker.step()
    assert linhas(env) == [(3, "worker", 2, "worker", 1)], "com 600 s grava e substitui a anterior"
    assert decifrado(env, 3)[0]["value"] == "r2"


# ------------------------------------------------------------------------------------------ SUB-REN-04
def test_sub_ren_04_sem_mudanca_no_conjunto_nao_grava(env, make_worker, add_cookies):
    relogio = Relogio()
    add_cookies([cookie("SID", "colado")])  # o servidor nao renova nada
    worker = make_worker(clock=relogio)
    for _ in range(3):
        relogio.avancar(700)
        worker.step()
    assert linhas(env) == [(1, "admin", None, "admin", 1)]


# ------------------------------------------------------------------------------------------ SUB-REN-05
def test_sub_ren_05_corrida_admin_cola_antes_da_gravacao_descarta_a_renovacao(env, make_worker, add_cookies, caplog):
    renova_sempre(env)
    add_cookies([cookie("SID", "colado")])
    colado_pelo_admin = {}

    def hook_corrida() -> None:
        if not colado_pelo_admin:  # so na 1a abertura: o admin cola um conjunto novo durante a carga
            colado_pelo_admin["id"] = insert_cookie_set(env.db, env.keypair.public_key, [cookie("SID", "do-admin")])
        env.server.set_cookie_headers = ["SID=r1; Path=/"]

    env.server.on_studio_request = hook_corrida
    worker = make_worker()
    with caplog.at_level(logging.INFO, logger="worker_inscritos"):
        worker.step()

    assert colado_pelo_admin["id"] == 2
    assert linhas(env) == [(2, "admin", None, "admin", 1)], "a renovacao do conjunto 1 foi descartada"
    assert any("renovacao descartada" in r.getMessage() for r in caplog.records)
    assert "do-admin" not in caplog.text and "SID" not in caplog.text

    worker.step()  # ciclo seguinte: o conjunto do admin e o aplicado (limpa e injeta so o dele)
    assert cookie_do_servidor(env) == "SID=do-admin"
    assert linhas(env) == [(3, "worker", 2, "worker", 1)], "a renovacao nova parte do conjunto do admin (id 2)"


# ------------------------------------------------------------------------------------------ SUB-REN-06
@pytest.mark.parametrize("variante", ["login", "sem-permissao-ptbr", "sem-permissao-en"])
def test_sub_ren_06_pagina_de_login_ou_sem_permissao_nunca_gera_renovacao(env, make_worker, add_cookies, variante):
    renova_sempre(env)
    if variante == "login":
        env.server.require_cookie = ("EXIGIDO", "1")  # o conjunto colado nao o tem: redireciona ao login
    else:
        sufixo = "ptbr" if variante.endswith("ptbr") else "en"
        env.server.html_override = (FIXTURES / f"sem_permissao_{sufixo}.html").read_text("utf-8")
    add_cookies([cookie("SID", "colado")])
    worker = make_worker()
    for _ in range(4):
        worker.step()
    # O Chromium mudou de fato (o servidor renovou SID), mas nada foi gravado.
    atuais = {c["name"]: c["value"] for c in worker.session.export_cookies(worker.hosts_sessao)}
    assert atuais["SID"].startswith("r"), "o Chromium foi mesmo renovado pelo servidor"
    assert linhas(env) == [(1, "admin", None, "admin", 1)]
    estado = query(env.db, "SELECT state, auth_state FROM worker_status")[0]
    assert estado[0] == "error"


def test_sub_ren_06b_valor_invalido_e_contador_ausente_tambem_nao_renovam(env, make_worker, add_cookies):
    renova_sempre(env)
    env.server.html_override = SEM_CONTADOR
    add_cookies([cookie("SID", "colado")])
    worker = make_worker()
    for _ in range(3):
        worker.step()
    assert linhas(env) == [(1, "admin", None, "admin", 1)]


# ------------------------------------------------------------------------------------------ SUB-REN-07
def test_sub_ren_07_reinicio_do_processo_usa_o_conjunto_renovado(env, make_worker, add_cookies):
    renova_sempre(env)
    add_cookies([cookie("SID", "colado")])
    w1 = make_worker()
    w1.step()
    assert linhas(env)[0][:3] == (2, "worker", 1)
    w1.session.close()
    w1.store.close()

    antes = env.server.studio_hits()
    w2 = make_worker()  # "reinicio": processo novo, sem memoria
    w2.step()
    primeira_carga = env.server.studio_requests()[antes]["cookie"]
    assert "SID=r1" in primeira_carga and "SID=colado" not in primeira_carga, primeira_carga
    assert w2.applied_id == 2
    assert [l[0] for l in linhas(env)] == [2], "o instante da renovacao vem do banco: sem nova gravacao em < 10 min"
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]


# ------------------------------------------------------------------------------------------ SUB-REN-08
def test_sub_ren_08_logs_e_worker_status_sem_nome_nem_valor_de_cookie(env, make_worker, add_cookies, caplog):
    env.server.set_cookie_headers = ["NOMEZZSECRETO=VALORZZSECRETO9; Path=/"]
    add_cookies([cookie("SID", "SEGREDOCOLADO123"), cookie("OUTROZZ", "OUTROVALORZZ")])
    worker = make_worker()
    with caplog.at_level(logging.DEBUG):
        worker.step()
    assert linhas(env)[0][:3] == (2, "worker", 1)

    textos = caplog.text + "\n" + dump_text(env.db) + "\n" + repr(query(env.db, "SELECT * FROM worker_status"))
    for proibido in ("NOMEZZSECRETO", "VALORZZSECRETO9", "SEGREDOCOLADO123", "OUTROZZ", "OUTROVALORZZ"):
        assert proibido not in textos, proibido
    assert any(r.getMessage() == "[COOKIES] sessao renovada: 3 cookies" for r in caplog.records)
    (blob,) = query(env.db, "SELECT ciphertext FROM cookie_sets")[0]
    assert b"VALORZZSECRETO9" not in bytes(blob) and b"NOMEZZSECRETO" not in bytes(blob)


# ------------------------------------------------------------------------------------------ SUB-REN-09
def test_sub_ren_09_autorizador_permite_insert_e_delete_em_cookie_sets_e_nada_mais(env, add_cookies):
    add_cookies([cookie("a", "1")])
    con = connect(str(env.db))
    try:
        con.execute(
            "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at, source, renewed_from) "
            "VALUES (zeroblob(60), '0123456789abcdef', 1, 'worker', '2026-01-01T00:00:00Z', 'worker', 1)"
        )
        con.execute("DELETE FROM cookie_sets WHERE id = 1")
        con.execute("DELETE FROM cookie_sets")
        assert query(env.db, "SELECT COUNT(*) FROM cookie_sets") == [(0,)]
    finally:
        con.close()


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE cookie_sets SET uploaded_by = 'x'",
        "UPDATE cookie_sets SET source = 'worker'",
        "UPDATE cookie_sets SET ciphertext = zeroblob(60)",
        "INSERT INTO slots (slot, url) VALUES (9, 'x')",
        "UPDATE slots SET url = 'x'",
        "DELETE FROM slots",
        "INSERT INTO settings (key, value) VALUES ('channel_id', 'x')",
        "UPDATE settings SET value = 'x'",
        "DELETE FROM settings",
        "UPDATE schema_meta SET value = '9'",
        "INSERT INTO live_stats (slot) VALUES (1)",
        "DELETE FROM live_stats",
        "CREATE TABLE intruso (x)",
        "DROP TABLE cookie_sets",
        "ALTER TABLE cookie_sets ADD COLUMN x TEXT",
        "CREATE TRIGGER t AFTER INSERT ON cookie_sets BEGIN SELECT 1; END",
    ],
)
def test_sub_ren_09b_autorizador_do_worker_nega_o_resto(env, add_cookies, sql):
    add_cookies([cookie("a", "1")])
    con = connect(str(env.db))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized|is prohibited"):
            con.execute(sql)
    finally:
        con.close()


def test_sub_ren_09c_papel_healthcheck_continua_sem_escrita(env, add_cookies):
    add_cookies([cookie("a", "1")])
    con = connect(str(env.db), role="healthcheck")
    try:
        for sql in (
            "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at) "
            "VALUES (zeroblob(60), '0123456789abcdef', 1, 'x', '2026-01-01T00:00:00Z')",
            "DELETE FROM cookie_sets",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="not authorized|is prohibited"):
                con.execute(sql)
    finally:
        con.close()


# ------------------------------------------------------------------------------------------ SUB-REN-10
def test_sub_ren_10_troca_de_canal_reabre_o_studio_sem_trocar_a_sessao_viva(env, make_worker, add_cookies):
    renova_sempre(env)
    add_cookies([cookie("SID", "colado")])
    worker = make_worker()
    worker.step()
    assert linhas(env)[0][:3] == (2, "worker", 1)

    con = sqlite3.connect(env.db)
    con.execute("INSERT INTO settings (key, value) VALUES ('channel_id', ?)", (NOVO,))
    con.commit()
    con.close()
    antes = env.server.studio_hits()
    worker.step()

    nova = env.server.studio_requests()[antes]
    assert nova["path"].split("/")[2] == NOVO
    assert "SID=r1" in nova["cookie"] and "SID=colado" not in nova["cookie"], nova["cookie"]


# ------------------------------------------------------------------------------------------ SUB-REN-11
def test_sub_ren_11_conjunto_novo_do_admin_limpa_e_injeta_so_o_dele(env, make_worker, add_cookies):
    renova_sempre(env)
    add_cookies([cookie("SID", "colado"), cookie("VELHO", "x")])
    worker = make_worker()
    worker.step()
    env.server.on_studio_request = None
    env.server.set_cookie_headers = []
    admin = add_cookies([cookie("SID", "do-admin")])
    worker.step()
    ultimo = cookie_do_servidor(env)
    assert ultimo == "SID=do-admin", ultimo
    assert worker.applied_id == admin


# ------------------------------------------------------------------------------------------ SUB-REN-12
def test_sub_ren_12_chave_publica_obrigatoria_sem_padrao_e_do_mesmo_par(env, tmp_path):
    sem = {k: v for k, v in env.env.items() if k != "COOKIE_PUBLIC_KEY_FILE"}
    with pytest.raises(ConfigError, match="COOKIE_PUBLIC_KEY_FILE"):
        load_config(sem)

    cfg = load_config(env.env)
    assert cfg.public_key_file == str(env.pub_file)

    outro = write_pub_file(tmp_path / "outra.txt", PrivateKey.generate())
    with pytest.raises(ConfigError, match="nao e o par"):
        ler_chave_publica(str(outro), env.keypair)
    with pytest.raises(ConfigError, match="nao encontrado"):
        ler_chave_publica(str(tmp_path / "nao_existe.txt"), env.keypair)
    lixo = tmp_path / "lixo.txt"
    lixo.write_text("isto nao e base64!!", encoding="ascii")
    with pytest.raises(ConfigError, match="invalida"):
        ler_chave_publica(str(lixo), env.keypair)
    assert bytes(ler_chave_publica(str(env.pub_file), env.keypair)) == bytes(env.keypair.public_key)
