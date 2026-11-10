"""Agregado de saude por minuto do worker inteiro (D43): o contador do mestre e o pool de usuarios somam no mesmo."""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from .store import Store

log = logging.getLogger("worker_inscritos")


class Saude:
    """minuto -> [leituras, soma_ms, max_ms, ok, erros]. Seguro entre threads; cada chamador usa a sua conexao."""

    def __init__(self, clock: Callable[[], float] = time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._agregados: dict[int, list[int]] = {}

    def registrar(self, store: Store, ms: int, ok: bool) -> None:
        """Soma a leitura no agregado do minuto e regrava a linha de `health_minutes` (melhor esforco)."""
        minuto = int(self.clock()) // 60 * 60
        with self._lock:
            agregado = self._agregados.setdefault(minuto, [0, 0, 0, 0, 0])
            agregado[0] += 1
            agregado[1] += max(0, ms)
            agregado[2] = max(agregado[2], ms)
            agregado[3 if ok else 4] += 1
            for velho in [m for m in self._agregados if m < minuto - 60]:
                del self._agregados[velho]
            leituras, soma, maximo, oks, erros = agregado
        try:
            store.save_health_minute(minuto, round(soma / leituras), maximo, oks, erros)
        except sqlite3.Error as exc:
            log.warning("[DB] nao foi possivel gravar a saude do minuto (%s)", type(exc).__name__)
