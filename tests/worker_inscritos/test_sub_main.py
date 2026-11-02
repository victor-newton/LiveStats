"""`python -m worker_inscritos` como processo real (entrada do container), com as esperas de producao."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

import pytest

from sub_support import REPO_ROOT, cookie, create_db, query

from worker_inscritos.config import ConfigError  # noqa: F401  (garante que o pacote importa)


def iniciar(env_extra: dict[str, str], tmp_path) -> tuple[subprocess.Popen, "object"]:
    ambiente = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUNBUFFERED": "1"}
    for nome in ("COOKIE_PRIVATE_KEY", "COOKIE_PRIVATE_KEY_FILE", "YOUTUBE_CHANNEL_ID", "SCRAPE_INTERVAL"):
        ambiente.pop(nome, None)
    ambiente.update(env_extra)
    log = open(tmp_path / "processo.log", "w+", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "worker_inscritos"],
        cwd=REPO_ROOT, env=ambiente, stdout=log, stderr=subprocess.STDOUT, text=True,
    )
    return proc, log


def encerrar(proc: subprocess.Popen, log) -> str:
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    log.seek(0)
    texto = log.read()
    log.close()
    return texto


def esperar(condicao, limite: float, passo: float = 0.5):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        valor = condicao()
        if valor:
            return valor
        time.sleep(passo)
    return condicao()


# ---------------------------------------------------------------------- erros de configuracao
@pytest.mark.parametrize(
    "remover,acrescentar",
    [
        ("COOKIE_PRIVATE_KEY_FILE", {}),
        ("YOUTUBE_CHANNEL_ID", {}),
        (None, {"YOUTUBE_CHANNEL_ID": "invalido"}),
        (None, {"SCRAPE_INTERVAL": "4"}),
        (None, {"YOUTUBE_STUDIO_BASE_URL": "http://example.com"}),
        (None, {"COOKIE_PRIVATE_KEY_FILE": "/nao/existe/cookie_private_key.txt"}),
    ],
    ids=["sem-arquivo-da-chave", "sem-canal", "canal-invalido", "intervalo-baixo", "url-http-externa", "chave-inexistente"],
)
def test_sub_sec_04_processo_termina_com_erro_na_configuracao_invalida(env, tmp_path, remover, acrescentar):
    variaveis = {k: v for k, v in env.env.items() if k != remover}
    variaveis.update(acrescentar)
    variaveis["COOKIE_PRIVATE_KEY"] = env.key_file.read_text().strip()  # o valor em variavel de ambiente e ignorado
    proc, log = iniciar(variaveis, tmp_path)
    try:
        assert proc.wait(timeout=60) == 2
    finally:
        texto = encerrar(proc, log)
    assert "Erro de configuracao" in texto
    assert variaveis["COOKIE_PRIVATE_KEY"] not in texto
    assert query(env.db, "SELECT * FROM worker_status") == []


# ---------------------------------------------------------------------- SUB-START-01 (processo)
def test_sub_start_01_processo_espera_o_esquema_e_depois_comeca(env, tmp_path):
    vazio = tmp_path / "data2" / "vazio.db"
    vazio.parent.mkdir()
    import sqlite3

    con = sqlite3.connect(vazio)
    con.execute("PRAGMA user_version = 1")
    con.close()

    proc, log = iniciar({**env.env, "DB_PATH": str(vazio)}, tmp_path)
    try:
        time.sleep(4)
        assert proc.poll() is None, "o processo deve esperar, nao sair"
        assert query(vazio, "SELECT name FROM sqlite_master WHERE type='table'") == []

        create_db(vazio)
        linha = esperar(lambda: query(vazio, "SELECT state, auth_state FROM worker_status"), 15)
        assert linha == [("error", "no_cookies")]  # esquema pronto, ainda sem cookies
    finally:
        texto = encerrar(proc, log)
    assert proc.returncode == 0, texto
    assert query(vazio, "SELECT state FROM worker_status") == [("stopped",)]


# ---------------------------------------------------------------------- fluxo completo
def test_sub_main_01_processo_completo_le_o_contador_e_encerra_com_sigterm(env, add_cookies, tmp_path):
    # esperas de producao: 3 s depois do www + 12 s depois do Studio
    env.server.page_name = "variante_b.html"
    cookies_set = add_cookies([cookie("sid", "ok")])
    proc, log = iniciar(env.env, tmp_path)
    try:
        leitura = esperar(lambda: query(env.db, "SELECT id, value, raw FROM subscriber_reading"), 60, passo=1)
        assert leitura == [(1, "460600", "460600")]
        assert query(env.db, "SELECT state, auth_state, cookies_applied_id, interval_seconds FROM worker_status") == [
            ("ok", "ok", cookies_set, 5)
        ]
        assert env.server.studio_requests()[0]["cookie"] == "sid=ok"

        saudavel = subprocess.run(
            [sys.executable, "-m", "worker_inscritos.healthcheck"],
            env={**os.environ, "DB_PATH": str(env.db), "PYTHONPATH": str(REPO_ROOT)},
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
        assert saudavel.returncode == 0, saudavel.stderr
    finally:
        texto = encerrar(proc, log)
    assert proc.returncode == 0, texto
    assert query(env.db, "SELECT state FROM worker_status") == [("stopped",)]
    assert "sid" not in texto.replace("signin", "")  # nenhum nome/valor de cookie no log do processo
    assert "ok" not in [linha.strip() for linha in texto.splitlines() if linha.strip() == "sid=ok"]
