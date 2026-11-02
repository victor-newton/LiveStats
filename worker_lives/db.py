"""Acesso ao SQLite compartilhado (`/data/livestats.db`) pelo worker-lives.

* O worker NUNCA cria nem altera tabelas: o banco é aberto com `mode=rw` (não cria o arquivo) e só a
  api aplica `db/schema.sql`.
* Toda conexão instala `set_authorizer` com as permissões de `docs/data-model.md` §2.1. Isso é uma
  defesa contra bug no próprio código (ex.: SQL escrito na tabela errada), não contra um processo
  comprometido.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

# worker-lives: lê `slots` (e `schema_meta` para saber se o esquema existe); escreve `live_stats` e
# `worker_status`. Nada mais (nem leitura de `cookie_sets`).
READ_TABLES = frozenset({"slots", "schema_meta"})
WRITE_TABLES = frozenset({"live_stats", "worker_status"})

_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_RECURSIVE,
    }
)
_WRITE_ACTIONS = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})


def make_authorizer(read_tables: Collection[str], write_tables: Collection[str]):
    def authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001 - assinatura do SQLite
        if action == sqlite3.SQLITE_READ:
            return sqlite3.SQLITE_OK if arg1 in read_tables else sqlite3.SQLITE_DENY
        if action in _WRITE_ACTIONS:
            return sqlite3.SQLITE_OK if arg1 in write_tables else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION:
            return sqlite3.SQLITE_DENY if arg2 == "load_extension" else sqlite3.SQLITE_OK
        if action in _ALLOWED_ACTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    return authorizer


def connect(
    db_path: str,
    *,
    read_tables: Collection[str] = READ_TABLES,
    write_tables: Collection[str] = WRITE_TABLES,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Abre o banco existente (não cria o arquivo) e instala o autorizador."""
    mode = "ro" if read_only else "rw"
    uri = Path(db_path).resolve().as_uri() + f"?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.set_authorizer(make_authorizer(read_tables, write_tables))
    except Exception:
        conn.close()
        raise
    return conn


def schema_ready(db_path: str) -> bool:
    """Verdadeiro se o banco existe e a api já aplicou o esquema (`schema_meta` com a versão)."""
    try:
        conn = connect(db_path)
    except sqlite3.OperationalError:
        return False  # arquivo ainda não existe
    try:
        row = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
    except sqlite3.OperationalError:
        return False  # tabela ainda não existe
    finally:
        conn.close()
    return row is not None


def read_slots(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute("SELECT slot, url FROM slots ORDER BY slot").fetchall()
    return {int(slot): str(url) for slot, url in rows}


@dataclass(frozen=True)
class LiveRow:
    slot: int
    active: int
    video_id: str
    title: str
    status: str
    views: int
    likes: int
    views_formatted: str
    likes_formatted: str
    updated_at: str


@dataclass(frozen=True)
class StatusRow:
    state: str
    last_heartbeat_at: int
    last_success_at: int | None
    consecutive_failures: int
    interval_seconds: int
    next_attempt_at: int | None
    last_error_code: str | None
    last_error_message: str | None
    version: str


_UPSERT_LIVE = (
    "INSERT OR REPLACE INTO live_stats (slot, active, video_id, title, status, views, likes,"
    " views_formatted, likes_formatted, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_UPSERT_STATUS = (
    "INSERT OR REPLACE INTO worker_status (worker, state, last_heartbeat_at, last_success_at,"
    " consecutive_failures, interval_seconds, next_attempt_at, last_error_code,"
    " last_error_message, auth_state, cookies_applied_id, version)"
    " VALUES ('lives', ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)"
)


def write_cycle(conn: sqlite3.Connection, rows: list[LiveRow], status: StatusRow) -> None:
    """Grava as linhas do ciclo e o heartbeat numa única transação curta."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            conn.execute(
                _UPSERT_LIVE,
                (
                    row.slot,
                    row.active,
                    row.video_id,
                    row.title,
                    row.status,
                    row.views,
                    row.likes,
                    row.views_formatted,
                    row.likes_formatted,
                    row.updated_at,
                ),
            )
        _write_status(conn, status)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def write_status(conn: sqlite3.Connection, status: StatusRow) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        _write_status(conn, status)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def _write_status(conn: sqlite3.Connection, status: StatusRow) -> None:
    conn.execute(
        _UPSERT_STATUS,
        (
            status.state,
            status.last_heartbeat_at,
            status.last_success_at,
            status.consecutive_failures,
            status.interval_seconds,
            status.next_attempt_at,
            status.last_error_code,
            status.last_error_message,
            status.version,
        ),
    )
