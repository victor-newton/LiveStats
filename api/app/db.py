"""Acesso ao SQLite: criacao dos esquemas, conexoes e autorizador.

Dois bancos (D2): ``livestats.db`` (compartilhado com os workers) e ``admin.db`` (sessoes e tentativas
de login; volume exclusivo da api). O ``set_authorizer`` abaixo e uma DEFESA CONTRA BUG no proprio codigo
(ex.: SQL injection, query na tabela errada); a protecao contra processo comprometido e o isolamento por
volume e a criptografia dos cookies .
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

# Tabelas que a api pode ler (e, em alguns casos, escrever) em livestats.db.
_SHARED_READ = {
    "schema_meta", "settings", "sets", "lives", "live_stats", "channel_info",
    "subscriber_reading", "worker_status", "cookie_sets",
    # v6, D43: a api le tudo do historico e da saude
    "live_samples", "set_peaks", "subscriber_samples", "live_reads", "health_minutes", "events",
}
_SHARED_WRITE = {
    # tabela -> acoes permitidas
    "sets": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE},
    "lives": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE},
    "live_stats": {sqlite3.SQLITE_DELETE},  # so apaga (URL da live mudou); quem grava e o worker-lives
    "settings": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE},
    "cookie_sets": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE},
    # D43: apagar uma live/conjunto leva junto, em CASCADE, as amostras, leituras e picos (o SQLite exige DELETE autorizado nelas)
    "live_samples": {sqlite3.SQLITE_DELETE},
    "live_reads": {sqlite3.SQLITE_DELETE},
    "set_peaks": {sqlite3.SQLITE_DELETE},
    "events": {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE},  # D43: so eventos source='api' (a poda dos 500 e feita com WHERE source='api')
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


# Colunas acrescentadas ao esquema compartilhado nas versoes 2 (D34) e 3 (D35) de ``slots``. Usadas so para levar
# um banco v1/v2/v3 ate a v4 antes da v5 (a tabela slots some na v5). Devem ser identicas as do esquema original.
_HEX_CHECK = "length({c}) = 7 AND {c} GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'"
_SLOTS_ADDED_COLUMNS = (
    ("goal", "INTEGER CHECK (goal IS NULL OR goal BETWEEN 1 AND 1000000000)"),
    ("color", "TEXT NOT NULL DEFAULT '#FF0033' CHECK (" + _HEX_CHECK.format(c="color") + ")"),
    ("font", "TEXT NOT NULL DEFAULT 'Roboto' CHECK (length(font) BETWEEN 1 AND 40)"),
    ("text_color", "TEXT NOT NULL DEFAULT '#FFFFFF' CHECK (" + _HEX_CHECK.format(c="text_color") + ")"),
)
_COOKIE_SETS_ADDED_COLUMNS = (  # versao 4 (D37)
    ("source", "TEXT NOT NULL DEFAULT 'admin' CHECK (source IN ('admin', 'worker'))"),
    ("renewed_from", "INTEGER CHECK (renewed_from IS NULL OR renewed_from >= 1)"),
)
SCHEMA_VERSION = "6"



def _ddl_from_schema(schema_sql: str, table: str) -> str:
    """O ``CREATE TABLE`` de ``table`` como escrito em db/schema.sql (fonte unica), sem o ``IF NOT EXISTS``."""
    match = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \(.*?\) STRICT;", schema_sql, re.DOTALL)
    if match is None:
        raise RuntimeError(f"tabela {table} nao encontrada no esquema")
    return match.group(0).replace("IF NOT EXISTS ", "", 1).rstrip(";")


_NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def _table_sql(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone()
    return row[0] if row else None


def _rebuild(conn: sqlite3.Connection, name: str, ddl: str, columns: str, where: str = "") -> None:
    """Recria a tabela ``name`` com o DDL novo preservando as linhas (``columns`` = colunas copiadas)."""
    conn.execute(f"ALTER TABLE {name} RENAME TO {name}_old")  # noqa: S608 (constantes)
    conn.execute(ddl)
    conn.execute(f"INSERT INTO {name} ({columns}) SELECT {columns} FROM {name}_old {where}")  # noqa: S608
    conn.execute(f"DROP TABLE {name}_old")  # noqa: S608


def _migrate_slots_to_sets(conn: sqlite3.Connection) -> None:
    """Slots com URL -> conjunto "Principal" (meta, cor e fonte do slot 1) com uma live por slot; remove ``slots``."""
    from .platforms import extract_youtube_video_id  # import tardio: so a migracao usa

    slots = conn.execute("SELECT slot, url, goal, color, font, text_color FROM slots ORDER BY slot").fetchall()
    with_url = []
    for row in slots:
        video_id = extract_youtube_video_id(row[1]) if row[1] else None
        if video_id is not None:
            with_url.append((row, video_id))
    if with_url:
        first = next((r for r in slots if r[0] == 1), slots[0])
        cur = conn.execute(
            "INSERT INTO sets (name, goal, color, font, text_color, position, created_at) "  # noqa: S608
            f"VALUES ('Principal', ?, ?, ?, ?, 0, {_NOW})",
            (first[2], first[3], first[4], first[5]),
        )
        set_id = cur.lastrowid
        for row, video_id in with_url:
            url = row[1].strip()
            if url == video_id:  # o formato anterior aceitava so o ID: guarda a URL canonica
                url = f"https://www.youtube.com/watch?v={video_id}"
            conn.execute(
                "INSERT INTO lives (set_id, url, platform, ref, label, goal, color, font, text_color, position, created_at) "  # noqa: S608
                f"VALUES (?, ?, 'youtube', ?, '', ?, ?, ?, ?, ?, {_NOW})",
                (set_id, url[:2048], video_id, row[2], row[3], row[4], row[5], row[0]),
            )
    conn.execute("DROP TABLE slots")


def compact_live_positions(conn: sqlite3.Connection, set_id: int | None = None) -> None:
    """D44: ``lives.position`` vira 1..n sem buracos dentro de cada conjunto (ordem atual; empate pelo id). Idempotente."""
    conn.execute(
        "UPDATE lives SET position = (SELECT count(*) FROM lives o WHERE o.set_id = lives.set_id "
        "AND (o.position < lives.position OR (o.position = lives.position AND o.id <= lives.id))) "
        "WHERE (:sid IS NULL OR set_id = :sid)",
        {"sid": set_id},
    )


def _migrate_shared(conn: sqlite3.Connection, schema_sql: str) -> None:
    """Leva um banco compartilhado v1..v5 a v6 (idempotente; nada se perde). Roda so no banco compartilhado.

    As tabelas novas (sets, lives, channel_info; v6: historico, saude e eventos) ja foram criadas por ``CREATE TABLE IF NOT EXISTS`` no esquema.
    O ``live_stats`` de formato anterior (coluna ``slot``) bloqueou o ``IF NOT EXISTS``: e apagado aqui e recriado no formato novo.
    """
    if _table_sql(conn, "schema_meta") is None or _table_sql(conn, "worker_status") is None:
        return  # banco que nao e o compartilhado (admin.db)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _table_sql(conn, "slots") is not None:
            present = {row[1] for row in conn.execute("PRAGMA table_info(slots)")}
            for name, definition in _SLOTS_ADDED_COLUMNS:
                if name not in present:
                    conn.execute(f"ALTER TABLE slots ADD COLUMN {name} {definition}")  # noqa: S608 (constantes)
            _migrate_slots_to_sets(conn)
        present = {row[1] for row in conn.execute("PRAGMA table_info(cookie_sets)")}
        for name, definition in _COOKIE_SETS_ADDED_COLUMNS:
            if name not in present:
                conn.execute(f"ALTER TABLE cookie_sets ADD COLUMN {name} {definition}")  # noqa: S608 (constantes)
        if "slot" in {row[1] for row in conn.execute("PRAGMA table_info(live_stats)")}:
            conn.execute("DROP TABLE live_stats")
            conn.execute(_ddl_from_schema(schema_sql, "live_stats"))
        if "'blocked'" not in (_table_sql(conn, "worker_status") or ""):
            _rebuild(
                conn,
                "worker_status",
                _ddl_from_schema(schema_sql, "worker_status"),
                "worker, state, last_heartbeat_at, last_success_at, consecutive_failures, interval_seconds, "
                "next_attempt_at, last_error_code, last_error_message, auth_state, cookies_applied_id, version",
            )
        if "'channel_id'" in (_table_sql(conn, "settings") or ""):
            _rebuild(conn, "settings", _ddl_from_schema(schema_sql, "settings"), "key, value", "WHERE key <> 'channel_id'")
        version = conn.execute("SELECT value FROM schema_meta WHERE key = 'schema_version'").fetchone()
        if version is not None and version[0] != SCHEMA_VERSION:
            compact_live_positions(conn)  # v6 (D44): so na passagem de versao
        conn.execute(
            "UPDATE schema_meta SET value = ? WHERE key = 'schema_version' AND value <> ?",
            (SCHEMA_VERSION, SCHEMA_VERSION),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def bootstrap_schema(db_path: str, schema_file: str) -> None:
    """Aplica o esquema (idempotente) e migra bancos de versoes anteriores. Quem cria os bancos e SOMENTE a api, na subida."""
    sql = Path(schema_file).read_text(encoding="utf-8")
    conn = sqlite3.connect(db_path, timeout=5.0, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(sql)
        _migrate_shared(conn, sql)
    finally:
        conn.close()
