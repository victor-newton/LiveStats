"""Acesso ao SQLite: criacao dos esquemas, conexoes e autorizador.

Dois bancos (D2): ``livestats.db`` (compartilhado com os workers) e ``admin.db`` (sessoes e tentativas
de login; volume exclusivo da api). O ``set_authorizer`` abaixo e uma DEFESA CONTRA BUG no proprio codigo
(ex.: SQL injection, query na tabela errada); a protecao contra processo comprometido e o isolamento por
volume e a criptografia dos cookies (``docs/data-model.md`` 2.1).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Tabelas que a api pode ler (e, em alguns casos, escrever) em livestats.db.
_SHARED_READ = {"schema_meta", "slots", "live_stats", "subscriber_reading", "worker_status", "cookie_sets"}
_SHARED_WRITE = {
    # tabela -> acoes permitidas
    "slots": {sqlite3.SQLITE_UPDATE},
    "cookie_sets": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE},
}
# A api so GRAVA o texto cifrado; nunca le de volta.
_DENIED_COLUMNS = {("cookie_sets", "ciphertext")}

_ADMIN_TABLES = {"admin_sessions", "login_attempts"}
_ADMIN_READ = _ADMIN_TABLES | {"schema_meta"}

_ALWAYS_OK = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_RECURSIVE,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
}
_WRITE_ACTIONS = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE}
_OK = sqlite3.SQLITE_OK
_DENY = sqlite3.SQLITE_DENY


def _shared_authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001
    if action in _ALWAYS_OK:
        return _OK
    if action == sqlite3.SQLITE_READ:
        if arg1 not in _SHARED_READ or (arg1, arg2) in _DENIED_COLUMNS:
            return _DENY
        return _OK
    if action in _WRITE_ACTIONS:
        if action == sqlite3.SQLITE_UPDATE and arg1 == "sqlite_sequence":
            return _OK
        return _OK if action in _SHARED_WRITE.get(arg1, ()) else _DENY
    return _DENY


def _admin_authorizer(action, arg1, arg2, dbname, source):  # noqa: ARG001
    if action in _ALWAYS_OK:
        return _OK
    if action == sqlite3.SQLITE_READ:
        return _OK if arg1 in _ADMIN_READ or arg1 == "sqlite_sequence" else _DENY
    if action in _WRITE_ACTIONS:
        return _OK if arg1 in _ADMIN_TABLES or arg1 == "sqlite_sequence" else _DENY
    return _DENY


def _connect(path: str, authorizer) -> sqlite3.Connection:
    # mode=rw: nunca cria um banco vazio por engano (o esquema e criado so em bootstrap_*).
    uri = Path(path).resolve().as_uri() + "?mode=rw"
    conn = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None, check_same_thread=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.set_authorizer(authorizer)
    return conn


def open_shared(path: str) -> sqlite3.Connection:
    """Conexao da api a ``livestats.db`` com as permissoes de ``data-model.md`` 2.1."""
    return _connect(path, _shared_authorizer)


def open_admin(path: str) -> sqlite3.Connection:
    """Conexao da api a ``admin.db`` (acesso total as duas tabelas de admin)."""
    return _connect(path, _admin_authorizer)


@contextmanager
def transaction(conn: sqlite3.Connection, immediate: bool = False) -> Iterator[sqlite3.Connection]:
    """Transacao curta: COMMIT se o bloco termina, ROLLBACK se levanta."""
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextmanager
def connection(opener, path: str) -> Iterator[sqlite3.Connection]:
    conn = opener(path)
    try:
        yield conn
    finally:
        conn.close()


# Colunas acrescentadas ao esquema compartilhado na versao 2 (D34). Devem ser identicas as de db/schema.sql.
_SLOTS_V2_COLUMNS = (
    ("goal", "INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000)"),
    (
        "color",
        "TEXT NOT NULL DEFAULT '#FF0033' "
        "CHECK (length(color) = 7 AND color GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]')",
    ),
)
SCHEMA_VERSION = "2"


def _migrate_shared(conn: sqlite3.Connection) -> None:
    """Migra um banco compartilhado v1 para v2 (idempotente; nada se perde). Roda so se ``slots`` existe."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'slots'").fetchone() is None:
        return  # banco que nao e o compartilhado (admin.db)
    conn.execute("BEGIN IMMEDIATE")
    try:
        present = {row[1] for row in conn.execute("PRAGMA table_info(slots)")}
        for name, definition in _SLOTS_V2_COLUMNS:
            if name not in present:
                conn.execute(f"ALTER TABLE slots ADD COLUMN {name} {definition}")  # noqa: S608 (constantes)
        conn.execute("UPDATE schema_meta SET value = ? WHERE key = 'schema_version' AND value = '1'", (SCHEMA_VERSION,))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def bootstrap_schema(db_path: str, schema_file: str) -> None:
    """Aplica o esquema (idempotente) e migra bancos antigos. Quem cria os bancos e SOMENTE a api, na subida."""
    sql = Path(schema_file).read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    try:
        conn.executescript(sql)
        _migrate_shared(conn)
    finally:
        conn.close()
