"""Relógio do worker. Injetável: os testes usam um relógio falso; a produção usa `SystemClock`."""

from __future__ import annotations

import threading
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Segundos desde a época Unix (UTC)."""

    def sleep(self, seconds: float) -> None:
        """Espera `seconds` (pode voltar antes se o processo foi mandado parar)."""


class SystemClock:
    """Relógio real. `sleep` acorda imediatamente quando `wakeup` é sinalizado (SIGTERM)."""

    def __init__(self, wakeup: threading.Event) -> None:
        self._wakeup = wakeup

    def now(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        self._wakeup.wait(seconds)
