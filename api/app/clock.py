"""Relogio da aplicacao. Injetavel para que os testes avancem o tempo sem dormir."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Epoch em segundos (UTC)."""
        ...


class SystemClock:
    def now(self) -> float:
        return time.time()


def timestamp_publico(epoch: float) -> str:
    """Formato dos JSON publicos (D24): UTC, ``isoformat()`` + ``Z``, com microssegundos."""
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def iso_seconds(epoch: float) -> str:
    """``date-time`` UTC sem fracao: ``2026-11-02T14:32:00Z`` (campos de admin)."""
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
