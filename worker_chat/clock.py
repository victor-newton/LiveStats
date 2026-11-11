"""Relogio do worker. Injetavel: os testes usam um relogio falso; a producao usa `SystemClock`."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    def now(self) -> float:
        """Segundos desde a epoca Unix (UTC): batimento do `worker_status`."""

    def monotonic(self) -> float:
        """Relogio monotonico: duracoes, limitador de ritmo e janela de 60 s do overlay fechado."""

    async def sleep(self, seconds: float) -> None:
        """Espera `seconds`."""


class SystemClock:
    def now(self) -> float:
        return time.time()

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)
