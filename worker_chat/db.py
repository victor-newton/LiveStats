"""Acesso ao SQLite compartilhado (`/data/livestats.db`) pelo worker-chat (papel `chat`, D57).

* O worker NUNCA cria nem altera tabelas: o banco e aberto com `mode=rw` (nao cria o arquivo) e so a api aplica o esquema.
* Toda conexao instala `set_authorizer`. Leitura: so as colunas que o worker usa de `lives`, `live_stats` e `sets`, e
  `set_chat` inteira (mais `schema_meta`, para saber se o esquema existe). Escrita: SOMENTE `worker_status`, e o codigo
  so grava a linha `'chat'`. O autorizador e uma defesa contra bug no proprio codigo, nao contra um processo comprometido.
* Nenhuma mensagem de chat chega aqui: o banco guarda so configuracao e o batimento.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

from worker_chat.rules import LiveInfo, SetChat, Snapshot

READ_TABLES = frozenset({"set_chat", "schema_meta"})
# Tabelas legiveis so em certas colunas (menor privilegio: nada de url, label, titulo, espectadores...).
READ_COLUMNS: Mapping[str, frozenset[str]] = {
    "lives": frozenset({"id", "set_id", "platform", "ref", "chat_enabled"}),
    "live_stats": frozenset({"live_id", "status"}),
    "sets": frozenset({"id"}),
}
WRITE_TABLES = frozenset({"worker_status"})

_ALLOWED_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_SELECT,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_SAVEPOINT,
        sqlite3.SQLITE_RECURSIVE,
    }
)
_WRITE_ACTIONS = frozenset({sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE})


def make_authorizer(read_tables: Collection[str], read_columns: Mapping[str, Collection[str]], write_tables: Collection[str]):
    def authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001 - assinatura do SQLite
        if action == sqlite3.SQLITE_READ:
            if arg1 in read_columns:
                # coluna vazia: o SQLite so confere a existencia da linha (JOIN/COUNT); nenhum dado e lido
                return sqlite3.SQLITE_OK if arg2 in read_columns[arg1] or arg2 == "" else sqlite3.SQLITE_DENY
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
    read_columns: Mapping[str, Collection[str]] = READ_COLUMNS,
    write_tables: Collection[str] = WRITE_TABLES,
    read_only: bool = False,
) -> sqlite3.Connection:
    """Abre o banco existente (nao cria o arquivo) e instala o autorizador."""
    mode = "ro" if read_only else "rw"
    uri = Path(db_path).resolve().as_uri() + f"?mode={mode}"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5.0)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.set_authorizer(make_authorizer(read_tables, read_columns, write_tables))
    except Exception:
        conn.close()
        raise
    return conn


def schema_ready(conn: sqlite3.Connection) -> bool:
    """Verdadeiro se a api ja aplicou o esquema v10 (`set_chat` existe)."""
    try:
        conn.execute("SELECT set_id FROM set_chat LIMIT 1").fetchone()
        conn.execute("SELECT chat_enabled FROM lives LIMIT 1").fetchone()
    except sqlite3.OperationalError:
        return False
    return True


def parse_ignored(raw: str) -> frozenset[str]:
    return frozenset(item for item in (p.strip().lower() for p in raw.split(",")) if item)


def read_snapshot(conn: sqlite3.Connection) -> Snapshot:
    """Configuracao do chat: conjuntos com linha em `set_chat` e as lives marcadas para o chat (com o status da leitura)."""
    sets: dict[int, SetChat] = {}
    rows = conn.execute(
        "SELECT c.set_id, c.enabled, c.hide_commands, c.ignored_users, c.max_rate,"
        " c.theme, c.font_size, c.logo_style, c.name_color, c.lifetime_s, c.show_roles, c.updated_at"
        " FROM set_chat c JOIN sets s ON s.id = c.set_id ORDER BY c.set_id"
    ).fetchall()
    for set_id, enabled, hide, ignored, max_rate, *aparencia in rows:
        sets[int(set_id)] = SetChat(
            set_id=int(set_id),
            enabled=bool(enabled),
            hide_commands=bool(hide),
            ignored=parse_ignored(str(ignored)),
            max_rate=int(max_rate),
            version=(int(enabled), int(hide), str(ignored), int(max_rate), *aparencia),
        )
    lives = tuple(
        LiveInfo(int(i), int(s), str(p), str(r), bool(ce), str(st))
        for i, s, p, r, ce, st in conn.execute(
            "SELECT l.id, l.set_id, l.platform, l.ref, l.chat_enabled, COALESCE(st.status, '')"
            " FROM lives l LEFT JOIN live_stats st ON st.live_id = l.id"
            " WHERE l.chat_enabled = 1 ORDER BY l.id"
        ).fetchall()
    )
    return Snapshot(sets=sets, lives=lives)


@dataclass(frozen=True)
class StatusRow:
    state: str  # starting | ok | degraded | error | stopped
    last_heartbeat_at: int
    last_success_at: int | None
    consecutive_failures: int
    interval_seconds: int
    last_error_code: str | None
    last_error_message: str | None
    version: str
    mem_bytes: int | None = None  # D56: memoria do container no batimento


_UPSERT_STATUS = (
    "INSERT OR REPLACE INTO worker_status (worker, state, last_heartbeat_at, last_success_at,"
    " consecutive_failures, interval_seconds, next_attempt_at, last_error_code,"
    " last_error_message, auth_state, cookies_applied_id, version, mem_bytes)"
    " VALUES ('chat', ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL, ?, ?)"
)


def write_status(conn: sqlite3.Connection, status: StatusRow) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            _UPSERT_STATUS,
            (
                status.state,
                status.last_heartbeat_at,
                status.last_success_at,
                status.consecutive_failures,
                status.interval_seconds,
                status.last_error_code,
                status.last_error_message,
                status.version,
                status.mem_bytes,
            ),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
