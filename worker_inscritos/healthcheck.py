"""Healthcheck do container: `python -m worker_inscritos.healthcheck`.

Saudavel (exit 0) se o heartbeat da linha `inscritos` de `worker_status` for mais novo que
3 x interval_seconds + 30 s (D23). Sem linha, banco inacessivel ou heartbeat velho: exit 1.
Le apenas DB_PATH (caminho, nao segredo); abre o banco so para leitura de `worker_status`.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

from .config import DEFAULT_DB_PATH
from .store import read_heartbeat


def check(db_path: str, now: float | None = None) -> tuple[bool, str]:
    agora = time.time() if now is None else now
    try:
        beat = read_heartbeat(db_path)
    except sqlite3.Error as exc:
        return False, f"banco inacessivel ({type(exc).__name__})"
    if beat is None:
        return False, "sem heartbeat do worker inscritos"
    heartbeat, intervalo = beat
    idade = agora - heartbeat
    limite = 3 * intervalo + 30
    if idade >= limite:
        return False, f"heartbeat velho: {idade:.0f}s (limite {limite}s)"
    return True, f"ok: heartbeat ha {idade:.0f}s"


def main() -> int:
    db_path = os.environ.get("DB_PATH", "").strip() or DEFAULT_DB_PATH
    saudavel, mensagem = check(db_path)
    print(mensagem, file=sys.stdout if saudavel else sys.stderr)
    return 0 if saudavel else 1


if __name__ == "__main__":
    sys.exit(main())
