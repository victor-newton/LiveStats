"""LIV-SEC-01/02/04/05, LIV-DB-01/02: origem da chave, alvo das requisições, varreduras e autorizador."""

from __future__ import annotations

import dataclasses
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from lives_helpers import LIVE_ID, REPO_ROOT, FakeYouTube, live_rows, set_slots, watch, worker_status
from worker_lives import db
from worker_lives.config import Config, ConfigError, validate_base_url

PRODUCTION = REPO_ROOT / "worker_lives"
TESTS = REPO_ROOT / "tests" / "worker_lives"
TEXT_SUFFIXES = {".py", ".json", ".txt", ".in", ".md", ".toml", ".ini", ".cfg", ".yml", ".yaml"}


def _text_files(*roots: Path):
    for root in roots:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in TEXT_SUFFIXES and "__pycache__" not in path.parts:
                yield path


def _run_module(module: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", module],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
    )


def _clean_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("YOUTUBE_", "LIVE_", "DB_"))}
    env["DB_PATH"] = str(tmp_path / "livestats.db")
    env.update(extra)
    return env


# ---------------------------------------------------------------------------- LIV-SEC-01


def test_liv_sec_01_sem_variavel_do_arquivo_o_processo_termina_com_erro(tmp_path):
    result = _run_module("worker_lives", _clean_env(tmp_path))

    assert result.returncode != 0
    assert "YOUTUBE_API_KEY_FILE" in result.stderr


@pytest.mark.parametrize("conteudo", [None, "", "  \n\t\n", "chave com espaços no meio", "chave\x00nula"])
def test_liv_sec_01_arquivo_ausente_vazio_ou_invalido_termina_com_erro(tmp_path, conteudo):
    arquivo = tmp_path / "youtube_api_key"
    if conteudo is not None:
        arquivo.write_text(conteudo, encoding="utf-8")
    result = _run_module("worker_lives", _clean_env(tmp_path, YOUTUBE_API_KEY_FILE=str(arquivo)))

    assert result.returncode != 0
    assert "YOUTUBE_API_KEY_FILE" in result.stderr
    assert str(arquivo) not in result.stderr  # nem o caminho
    for pedaco in ("espaços no meio", "nula"):
        assert pedaco not in result.stderr  # nem o conteúdo do arquivo
    assert not (tmp_path / "livestats.db").exists()


def test_liv_sec_01_diretorio_no_lugar_do_arquivo(tmp_path):
    result = _run_module("worker_lives", _clean_env(tmp_path, YOUTUBE_API_KEY_FILE=str(tmp_path)))

    assert result.returncode != 0
    assert "YOUTUBE_API_KEY_FILE" in result.stderr


def test_liv_sec_01_sem_valor_padrao_na_configuracao():
    with pytest.raises(ConfigError, match="YOUTUBE_API_KEY_FILE"):
        Config.from_env({})
    with pytest.raises(ConfigError, match="YOUTUBE_API_KEY_FILE"):
        Config.from_env({"YOUTUBE_API_KEY_FILE": ""})


def test_liv_sec_01_chave_lida_do_arquivo_sem_espacos_nas_pontas(tmp_path):
    arquivo = tmp_path / "k"
    arquivo.write_text("  abc123-chave_x\r\n", encoding="utf-8")

    assert Config.from_env({"YOUTUBE_API_KEY_FILE": str(arquivo)}).api_key == "abc123-chave_x"


# ---------------------------------------------------------------------------- LIV-SEC-02


def test_liv_sec_02_variavel_antiga_no_ambiente_e_ignorada(tmp_path):
    sentinela = "sentinela-no-ambiente-" + os.urandom(8).hex()
    result = _run_module("worker_lives", _clean_env(tmp_path, YOUTUBE_API_KEY=sentinela))

    assert result.returncode != 0
    assert "YOUTUBE_API_KEY_FILE" in result.stderr
    assert sentinela not in result.stderr + result.stdout


def test_liv_sec_02_configuracao_ignora_a_variavel_antiga(key_file, api_key):
    with pytest.raises(ConfigError):
        Config.from_env({"YOUTUBE_API_KEY": "valor-do-ambiente"})

    config = Config.from_env({"YOUTUBE_API_KEY": "valor-do-ambiente", "YOUTUBE_API_KEY_FILE": str(key_file)})
    assert config.api_key == api_key


# ---------------------------------------------------------------------------- LIV-SEC-04


def test_liv_sec_04_nenhuma_chave_literal_no_codigo_nem_nos_testes():
    padrao = re.compile("AI" + "za[0-9A-Za-z_-]{35}")
    achados = [p for p in _text_files(PRODUCTION, TESTS) if padrao.search(p.read_text(encoding="utf-8"))]
    assert achados == []

    chave_real = REPO_ROOT / "secrets" / "youtube_api_key.txt"
    if chave_real.exists():  # se a chave real estiver presente, o valor dela não pode estar em arquivo algum
        valor = chave_real.read_text(encoding="utf-8").strip()
        assert valor
        assert [p for p in _text_files(PRODUCTION, TESTS) if valor in p.read_text(encoding="utf-8")] == []


def test_liv_sec_04_varredura_encontra_arquivos():
    nomes = {p.name for p in _text_files(PRODUCTION, TESTS)}

    assert {"worker.py", "config.py", "youtube.py", "requirements.txt", "videos_mixed.json"} <= nomes


# ---------------------------------------------------------------------------- LIV-SEC-05


@pytest.mark.parametrize(
    "valida",
    [
        "https://www.googleapis.com/youtube/v3",
        "https://exemplo.test/youtube/v3/",
        "http://localhost:8099/youtube/v3",
        "http://127.0.0.1:8099",
        "http://[::1]:8099/youtube/v3",
    ],
)
def test_liv_sec_05_urls_aceitas(valida):
    assert validate_base_url(valida) == valida.rstrip("/")


@pytest.mark.parametrize(
    "invalida",
    [
        "http://www.googleapis.com/youtube/v3",
        "http://exemplo.test/youtube/v3",
        "http://localhost.exemplo.test/youtube/v3",
        "http://10.0.0.5:8099/youtube/v3",
        "ftp://localhost/x",
        "file:///etc/passwd",
        "ws://localhost/x",
        "https://usuario:senha@exemplo.test/youtube/v3",
        "https://exemplo.test/youtube/v3?key=x",
        "https://exemplo.test/youtube/v3#frag",
        "https://",
        "//exemplo.test/youtube/v3",
        "www.googleapis.com/youtube/v3",
        "https://exemplo.test:porta/youtube/v3",
    ],
)
def test_liv_sec_05_urls_recusadas(invalida, key_file):
    with pytest.raises(ConfigError, match="YOUTUBE_API_BASE_URL"):
        validate_base_url(invalida)
    with pytest.raises(ConfigError, match="YOUTUBE_API_BASE_URL"):
        Config.from_env({"YOUTUBE_API_KEY_FILE": str(key_file), "YOUTUBE_API_BASE_URL": invalida})


def test_liv_sec_05_padrao_e_o_endereco_oficial(key_file):
    config = Config.from_env({"YOUTUBE_API_KEY_FILE": str(key_file)})

    assert config.api_base_url == "https://www.googleapis.com/youtube/v3"


def test_liv_sec_05_requisicoes_vao_so_para_a_url_configurada(make_worker, router, db_path, api_key):
    base = "http://127.0.0.1:8099/youtube/v3"
    fake = FakeYouTube(router, base)  # qualquer outro host quebraria: respx recusa o que não foi mapeado
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])

    make_worker(base_url=base).run_cycle()

    assert len(fake.requests) == 1
    url = fake.requests[0].url
    assert (url.scheme, url.host, url.port, url.path) == ("http", "127.0.0.1", 8099, "/youtube/v3/videos")
    assert live_rows(db_path)[1]["video_id"] == LIVE_ID


def test_liv_sec_05_nao_segue_redirecionamento_para_outro_host(make_worker, youtube, router, db_path):
    set_slots(db_path, [watch(LIVE_ID), "", "", ""])
    outro = router.get("https://coletor.exemplo.test/").mock(return_value=httpx.Response(200, json={}))
    youtube.enqueue(httpx.Response(302, headers={"Location": "https://coletor.exemplo.test/"}))

    make_worker().run_cycle()

    assert not outro.called  # a chave (no cabeçalho) não é reenviada a outro host
    assert worker_status(db_path)["last_error_code"] == "http_error"


# ---------------------------------------------------------------------------- LIV-DB-01


# Mensagens do SQLite quando o autorizador nega: "not authorized" (escrita/DDL/pragma) e
# "access to <tabela>.<coluna> is prohibited" (leitura de coluna). Só o autorizador as produz.
DENIED = r"not authorized|access to \S+ is prohibited"

NEGADAS = [
    "INSERT INTO slots (slot, url) VALUES (1, 'x')",
    "UPDATE slots SET url = 'x'",
    "DELETE FROM slots",
    "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at)"
    " VALUES (zeroblob(60), '0123456789abcdef', 1, 'x', '2026-11-02T00:00:00Z')",
    "INSERT INTO subscriber_reading (id, value, raw, read_at) VALUES (1, '12', '12', '2026-11-02T00:00:00Z')",
    "SELECT * FROM cookie_sets",
    "SELECT ciphertext FROM cookie_sets",
    "SELECT count(*) FROM cookie_sets",
    "SELECT value FROM subscriber_reading",
    "SELECT name FROM sqlite_master",
    "CREATE TABLE intruso (x)",
    "DROP TABLE slots",
    "ALTER TABLE slots ADD COLUMN y TEXT",
    "ATTACH DATABASE ':memory:' AS outro",
    "PRAGMA journal_mode=DELETE",
]


@pytest.mark.parametrize("sql", NEGADAS)
def test_liv_db_01_autorizador_nega_o_que_o_worker_nao_deve_fazer(db_path, sql):
    conn = db.connect(str(db_path))
    try:
        with pytest.raises(sqlite3.DatabaseError, match=DENIED):
            conn.execute(sql)
    finally:
        conn.close()


def test_liv_db_01_autorizador_permite_o_que_o_worker_precisa(db_path):
    conn = db.connect(str(db_path))
    try:
        assert [row[0] for row in conn.execute("SELECT slot FROM slots ORDER BY slot")] == [1, 2, 3, 4]
        assert conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone() == ("3",)
        conn.execute(
            "INSERT OR REPLACE INTO live_stats (slot, active, video_id, title, status, views, likes,"
            " views_formatted, likes_formatted, updated_at) VALUES"
            " (1, 0, '', '', 'sem_url', 0, 0, '0', '0', '2026-11-02T00:00:00.000001Z')"
        )
        conn.execute(
            "INSERT OR REPLACE INTO worker_status (worker, state, last_heartbeat_at, interval_seconds)"
            " VALUES ('lives', 'ok', 1, 30)"
        )
    finally:
        conn.close()
    assert live_rows(db_path)[1]["status"] == "sem_url"


def test_liv_db_01_helper_somente_leitura_do_healthcheck(db_path):
    conn = db.connect(str(db_path), read_tables={"worker_status"}, write_tables=(), read_only=True)
    try:
        assert conn.execute("SELECT count(*) FROM worker_status").fetchone() == (0,)
        for sql in ("SELECT * FROM slots", "INSERT INTO worker_status (worker) VALUES ('lives')"):
            with pytest.raises(sqlite3.DatabaseError, match=DENIED):
                conn.execute(sql)
    finally:
        conn.close()


def test_liv_db_01_o_worker_nao_cria_o_arquivo_do_banco(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        db.connect(str(tmp_path / "nao-existe.db"))
    assert not (tmp_path / "nao-existe.db").exists()


# ---------------------------------------------------------------------------- LIV-DB-02


def test_liv_db_02_worker_nao_referencia_o_estado_de_admin():
    proibidos = ["admin.db", "ADMIN_DB_PATH", "admin_sessions", "login_attempts", "/admin-state"]
    achados = [
        (p.name, termo)
        for p in _text_files(PRODUCTION)
        for termo in proibidos
        if termo in p.read_text(encoding="utf-8")
    ]
    assert achados == []
    assert len(list(_text_files(PRODUCTION))) >= 8  # a varredura realmente leu o pacote


def test_liv_db_02_configuracao_so_conhece_db_path():
    campos = {f.name for f in dataclasses.fields(Config)}

    assert campos == {"db_path", "api_key", "interval", "api_base_url"}


def test_liv_db_02_variavel_de_admin_no_ambiente_e_ignorada(key_file):
    config = Config.from_env(
        {"YOUTUBE_API_KEY_FILE": str(key_file), "DB_PATH": "/x/livestats.db", "ADMIN_DB_PATH": "/y/admin.db"}
    )

    assert config.db_path == "/x/livestats.db"
    assert "/y/admin.db" not in repr(config)
