"""Fixtures compartilhadas dos testes do worker-inscritos."""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest
from nacl.public import PrivateKey

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
for caminho in (str(REPO_ROOT), str(HERE)):
    if caminho not in sys.path:
        sys.path.insert(0, caminho)

from sub_support import (  # noqa: E402
    PLACEHOLDER_CHANNEL,
    FixtureServer,
    create_db,
    insert_cookie_set,
    write_key_file,
)
from worker_inscritos.browser import Timings  # noqa: E402
from worker_inscritos.config import ler_chave_privada, load_config  # noqa: E402
from worker_inscritos.worker import criar_worker  # noqa: E402

# Esperas curtas so para os testes terem velocidade; o fluxo e o mesmo (www -> espera -> cookies -> Studio -> espera).
FAST = Timings(after_www=0.05, ready_timeout=10.0, after_studio=0.15, nav_timeout=20.0)


@dataclass
class Env:
    tmp: Path
    db: Path
    key_file: Path
    keypair: PrivateKey
    server: FixtureServer
    env: dict[str, str]


@pytest.fixture
def server():
    srv = FixtureServer()
    yield srv
    srv.close()


@pytest.fixture
def env(tmp_path, server):
    """Banco real com o esquema, par de chaves gerado pelo teste, arquivo da chave privada e variaveis de ambiente."""
    keypair = PrivateKey.generate()
    db = create_db(tmp_path / "data" / "livestats.db")
    key_file = write_key_file(tmp_path / "cookie_private_key.txt", keypair)
    (tmp_path / "tmp").mkdir()
    variaveis = {
        "DB_PATH": str(db),
        "COOKIE_PRIVATE_KEY_FILE": str(key_file),
        "YOUTUBE_CHANNEL_ID": PLACEHOLDER_CHANNEL,
        "SCRAPE_INTERVAL": "5",
        "YOUTUBE_WWW_BASE_URL": server.www_base,
        "YOUTUBE_STUDIO_BASE_URL": server.studio_base,
        "TMPDIR": str(tmp_path / "tmp"),
    }
    return Env(tmp_path, db, key_file, keypair, server, variaveis)


@pytest.fixture
def make_worker(env):
    criados = []

    def _make(clock=time.time, timings=FAST, **overrides):
        config = load_config({**env.env, **overrides})
        worker = criar_worker(config, ler_chave_privada(config.private_key_file), timings=timings, clock=clock)
        criados.append(worker)
        return worker

    yield _make
    for worker in criados:
        worker.session.close()
        worker.store.close()


@pytest.fixture
def add_cookies(env):
    def _add(cookies, **kw):
        return insert_cookie_set(env.db, env.keypair.public_key, cookies, **kw)

    return _add
