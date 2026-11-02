"""Healthcheck do container: `python -m worker_lives.healthcheck` (D23).

Saudável = existe heartbeat de `worker_status('lives')` com idade <= 3 x intervalo + 30 s.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import time

from worker_lives import db
from worker_lives.config import DEFAULT_DB_PATH


def check(db_path: str, now: float) -> tuple[bool, str]:
    try:
        conn = db.connect(db_path, read_tables={"worker_status"}, write_tables=(), read_only=True)
    except sqlite3.OperationalError:
        return False, "banco indisponível"
    try:
        row = conn.execute(
            "SELECT last_heartbeat_at, interval_seconds FROM worker_status WHERE worker = 'lives'"
        ).fetchone()
    except sqlite3.OperationalError:
        return False, "tabela worker_status indisponível"
    finally:
        conn.close()
    if row is None:
        return False, "sem heartbeat do worker-lives"
    heartbeat, interval = row
    age = now - heartbeat
    limit = 3 * interval + 30
    if age > limit:
        return False, f"heartbeat de {age:.0f}s atrás (limite {limit}s)"
    return True, "ok"


def main() -> int:
    healthy, message = check(os.environ.get("DB_PATH") or DEFAULT_DB_PATH, time.time())
    if not healthy:
        print(f"worker-lives não saudável: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
