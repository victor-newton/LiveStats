"""Fixtures da suite de seguranca (T8): usa a MESMA pilha real de tests/e2e (singleton por sessao)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "e2e"))

from e2e_stack import E2EStack, get_stack  # noqa: E402


@pytest.fixture(scope="session")
def stack() -> E2EStack:
    return get_stack()
