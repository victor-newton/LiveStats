"""Fixtures pytest do worker-lives. Cada teste cria o próprio banco, chave e relógio."""

from __future__ import annotations

import secrets
import threading
from pathlib import Path

import pytest
import respx

from lives_helpers import DEFAULT_BASE, FakeClock, FakeYouTube, create_schema
from worker_lives.config import Config
from worker_lives.worker import Worker


@pytest.fixture
def api_key() -> str:
    """Sentinela aleatória usada como chave nos testes (nunca a chave real)."""
    return "test-key-" + secrets.token_hex(16)


@pytest.fixture
def key_file(tmp_path: Path, api_key: str) -> Path:
    path = tmp_path / "youtube_api_key"
    path.write_text(api_key + "\n", encoding="utf-8")
    return path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "livestats.db"
    create_schema(path)
    return path


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False) as mock_router:
        yield mock_router


@pytest.fixture
def youtube(router) -> FakeYouTube:
    return FakeYouTube(router, DEFAULT_BASE)


@pytest.fixture
def make_worker(tmp_path: Path, key_file: Path, db_path: Path, clock: FakeClock):
    """Fábrica de workers reais (config lida do ambiente, como em produção)."""
    created: list[Worker] = []

    def factory(*, interval: int | None = None, base_url: str | None = None, path: Path | None = None):
        env = {"DB_PATH": str(path or db_path), "YOUTUBE_API_KEY_FILE": str(key_file)}
        if interval is not None:
            env["LIVE_INTERVAL"] = str(interval)
        if base_url is not None:
            env["YOUTUBE_API_BASE_URL"] = base_url
        worker = Worker(Config.from_env(env), clock, threading.Event())
        created.append(worker)
        return worker

    yield factory
    for worker in created:
        worker.client.close()
