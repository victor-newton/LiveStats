"""Espera crescente entre reconexoes (D57): dobra a cada falha, ate 2 minutos."""

from __future__ import annotations

BACKOFF_START = 1.0
BACKOFF_MAX = 120.0
STABLE_SECONDS = 30.0  # conexao que durou isto (ou mais) zera a espera


class Backoff:
    def __init__(self, start: float = BACKOFF_START, maximum: float = BACKOFF_MAX) -> None:
        self._start = start
        self._max = maximum
        self._next = start

    def next(self) -> float:
        """Espera desta falha; a seguinte sera o dobro, limitada ao maximo."""
        delay = self._next
        self._next = min(self._max, self._next * 2)
        return delay

    def reset(self) -> None:
        self._next = self._start
