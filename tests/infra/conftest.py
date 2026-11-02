"""Fixtures e marcadores compartilhados de tests/infra.

Os marcadores tambem sao declarados no pytest.ini da raiz (T2); declarar aqui so evita aviso quando a suite
roda isolada. Nada aqui desliga, pula ou relaxa teste algum.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: exige Docker (falha, nunca pula, se nao houver)")
    config.addinivalue_line("markers", "e2e: exige a pilha completa")
    config.addinivalue_line("markers", "slow: teste lento")


@pytest.fixture(scope="session")
def repo() -> Path:
    return REPO


@pytest.fixture(scope="session")
def compose_text(repo) -> str:
    return (repo / "docker-compose.yml").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def compose(compose_text) -> dict:
    return yaml.safe_load(compose_text)
