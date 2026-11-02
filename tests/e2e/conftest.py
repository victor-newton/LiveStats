# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""Fixtures da suite de integracao (T8): a pilha real sobe uma vez por sessao e e removida no fim."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_stack import E2EStack, get_stack  # noqa: E402


@pytest.fixture(scope="session")
def stack() -> E2EStack:
    return get_stack()
