"""Acesso ao SQLite compartilhado (/data/livestats.db) pelo worker-inscritos.

- Nunca executa DDL e nunca cria o arquivo: quem aplica db/schema.sql e a api (data-model.md 1).
- Toda conexao instala `set_authorizer` com as permissoes de data-model.md 2: leitura de `cookie_sets` e
  `schema_meta`; escrita em `subscriber_reading` e `worker_status`; em `cookie_sets` so INSERT e DELETE (D37); nada mais. E uma defesa contra BUG do
  proprio codigo (SQL errada), nao contra processo comprometido (o isolamento real e por volume e criptografia).
- Este modulo desconhece o estado de admin (outro banco, outro volume).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("worker_inscritos.store")

WORKER_NAME = "inscritos"

# Leitura permitida (tabela -> colunas, None = qualquer). Em `worker_status.worker` e `subscriber_reading.id`
# so a chave: o SQLite le a coluna do conflito ao executar o UPSERT; os demais campos nao sao legiveis.
_READ_OK: dict[str, set[str] | None] = {
    "cookie_sets": None,
    "schema_meta": None,
    "worker_status": {"worker"},
    "subscriber_reading": {"id"},
    "channel_info": None,  # D38: o UPSERT le a coluna do conflito
    # D43 (esquema v6): historico, saude e eventos; lidos pelas limpezas (`WHERE at < ?`), pelo guarda de 1 por minuto e pela poda
    "subscriber_samples": None,
    "health_minutes": None,
    "events": None,
}
_WRITE_OK = {"subscriber_reading", "worker_status", "channel_info", "subscriber_samples", "health_minutes", "events"}

SAMPLES_KEEP_SECONDS = 8 * 86400  # D43: amostras de inscritos com mais de 8 dias saem
HEALTH_KEEP_SECONDS = 7200  # D43: saude com mais de 2 h sai
EVENTS_KEEP = 500  # D43: no maximo 500 eventos deste worker

# Papel do healthcheck: so le o heartbeat.
_HEALTHCHECK_READ_OK: dict[str, set[str] | None] = {"worker_status": None, "schema_meta": None}

_ALWAYS_OK_ACTIONS = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
}

def _make_authorizer(role: str):
    leitura = _HEALTHCHECK_READ_OK if role == "healthcheck" else _READ_OK
    escrita = set() if role == "healthcheck" else _WRITE_OK
    insert_delete = escrita | ({"cookie_sets"} if escrita else set())  # D37: renovacao da sessao

    def authorizer(action, arg1, arg2, _db, _trigger):
        if action in _ALWAYS_OK_ACTIONS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            colunas = leitura.get(arg1, set())
            if arg1 in leitura and (colunas is None or arg2 in colunas):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_DELETE):
            return sqlite3.SQLITE_OK if arg1 in insert_delete else sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_UPDATE:  # D37: nunca UPDATE em cookie_sets
            return sqlite3.SQLITE_OK if arg1 in escrita else sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_DENY  # DDL, PRAGMA, ATTACH, etc.

    return authorizer


def connect(db_path: str, role: str = "worker") -> sqlite3.Connection:
    """Abre o banco EXISTENTE (nunca cria) com PRAGMAs de conexao e autorizador. Levanta sqlite3.Error."""
    uri = Path(db_path).absolute().as_uri() + "?mode=rw"
    con = sqlite3.connect(uri, uri=True, timeout=5.0, isolation_level=None)
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        con.set_authorizer(_make_authorizer(role))
    except Exception:
        con.close()
        raise
    return con


def schema_ready(db_path: str) -> bool:
    """True se o banco existe e a tabela schema_meta ja foi criada pela api."""
    try:
        con = connect(db_path)
    except sqlite3.Error:
        return False
    try:
        con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        return True
    except sqlite3.Error:
        return False
    finally:
        con.close()


def wait_for_schema(db_path: str, stop: threading.Event, poll: float = 2.0) -> bool:
    """Espera a api criar o esquema (sem criar nada). False se `stop` for acionado antes."""
    avisado = False
    while not stop.is_set():
        if schema_ready(db_path):
            return True
        if not avisado:
            log.info("[DB] esquema ainda nao existe; aguardando a api...")
            avisado = True
        stop.wait(poll)
    return False


def iso_utc(epoch: float) -> str:
    """UTC `isoformat()` + `Z` (D24): mesmo texto de `datetime.utcnow().isoformat() + "Z"`."""
    return datetime.fromtimestamp(epoch, timezone.utc).replace(tzinfo=None).isoformat() + "Z"


@dataclass(frozen=True)
class CookieSetRow:
    id: int
    key_id: str
    ciphertext: bytes
    source: str
    created_at: str


@dataclass
class StatusRow:
    state: str
    last_heartbeat_at: int
    last_success_at: int | None
    consecutive_failures: int
    interval_seconds: int
    last_error_code: str | None
    last_error_message: str | None
    auth_state: str
    cookies_applied_id: int | None
    version: str


class Store:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------ cookies
    def latest_cookie_set_id(self) -> int | None:
        row = self.con.execute("SELECT id FROM cookie_sets ORDER BY id DESC LIMIT 1").fetchone()
        return None if row is None else int(row[0])

    def get_cookie_set(self, set_id: int) -> CookieSetRow | None:
        row = self.con.execute(
            "SELECT id, key_id, ciphertext, source, created_at FROM cookie_sets WHERE id = ?", (set_id,)
        ).fetchone()
        return (
            None
            if row is None
            else CookieSetRow(int(row[0]), str(row[1]), bytes(row[2]), str(row[3]), str(row[4]))
        )

    def get_channel_info(self) -> tuple[str, str] | None:
        """(channel_id, channel_name) gravados (D38); None se ainda nao houve deteccao."""
        row = self.con.execute("SELECT channel_id, channel_name FROM channel_info WHERE id = 1").fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    # ------------------------------------------------------------------ escrita
    def save_reading(self, value: str, raw: str, read_at: str) -> None:
        """Linha unica id=1, reescrita a cada leitura valida (mesmo com valor igual). `raw` <= 256 (D20)."""
        self.con.execute(
            "INSERT INTO subscriber_reading (id, value, raw, read_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET value = excluded.value, raw = excluded.raw, read_at = excluded.read_at",
            (value, raw[:256], read_at),
        )

    def save_channel_info(self, channel_id: str, channel_name: str, detected_at: str) -> None:
        """D38: linha unica id=1, so este worker escreve. Nome limitado a 200 caracteres."""
        self.con.execute(
            "INSERT INTO channel_info (id, channel_id, channel_name, detected_at) VALUES (1, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET channel_id = excluded.channel_id, "
            "channel_name = excluded.channel_name, detected_at = excluded.detected_at",
            (channel_id, channel_name[:200], detected_at),
        )

    def save_subscriber_sample(self, at: int, value: int) -> bool:
        """D43: no maximo uma amostra por minuto (guarda no SQL, vale mesmo apos reinicio); apaga as > 8 dias."""
        minuto = at // 60 * 60
        with self.transaction():
            cur = self.con.execute(
                "INSERT OR IGNORE INTO subscriber_samples (at, value) "
                "SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM subscriber_samples WHERE at >= ?)",
                (at, value, minuto),
            )
            gravou = cur.rowcount > 0
            if gravou:
                self.con.execute("DELETE FROM subscriber_samples WHERE at < ?", (at - SAMPLES_KEEP_SECONDS,))
        return gravou

    def save_health_minute(self, minute: int, avg_ms: int, max_ms: int, ok: int, errors: int) -> None:
        """D43: agregado do minuto (reescrito a cada leitura do minuto em andamento); apaga o que tem > 2 h."""
        with self.transaction():
            self.con.execute(
                "INSERT OR REPLACE INTO health_minutes (worker, minute, avg_ms, max_ms, ok, errors) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (WORKER_NAME, minute, avg_ms, max_ms, ok, errors),
            )
            self.con.execute(
                "DELETE FROM health_minutes WHERE worker = ? AND minute < ?", (WORKER_NAME, minute - HEALTH_KEEP_SECONDS)
            )

    def add_event(self, at: int, level: str, code: str, message: str) -> None:
        """D43: evento (so mudancas) e poda para no maximo 500 linhas deste worker."""
        with self.transaction():
            self.con.execute(
                "INSERT INTO events (at, source, level, code, message, live_id) VALUES (?, ?, ?, ?, ?, NULL)",
                (at, WORKER_NAME, level, code[:40], message[:200]),
            )
            self.con.execute(
                "DELETE FROM events WHERE source = ? AND id <= "
                "(SELECT id FROM events WHERE source = ? ORDER BY id DESC LIMIT 1 OFFSET ?)",
                (WORKER_NAME, WORKER_NAME, EVENTS_KEEP),
            )

    def renew_cookie_set(
        self, ciphertext: bytes, key_id: str, count: int, applied_id: int, created_at: str
    ) -> int | None:
        """D37: grava a renovacao so se o conjunto mais recente ainda e o aplicado (`MAX(id) = applied_id`).

        INSERT da nova linha e DELETE das anteriores na mesma transacao. None = descartada (o admin colou outro).
        """
        with self.transaction():
            atual = self.con.execute("SELECT MAX(id) FROM cookie_sets").fetchone()[0]
            if atual is None or int(atual) != applied_id:
                return None
            cur = self.con.execute(
                "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at, source, renewed_from) "
                "VALUES (?, ?, ?, 'worker', ?, 'worker', ?)",
                (ciphertext, key_id, count, created_at, applied_id),
            )
            novo = int(cur.lastrowid)
            self.con.execute("DELETE FROM cookie_sets WHERE id <> ?", (novo,))
        return novo

    def write_status(self, s: StatusRow) -> None:
        self.con.execute(
            "INSERT INTO worker_status (worker, state, last_heartbeat_at, last_success_at, consecutive_failures, "
            "interval_seconds, next_attempt_at, last_error_code, last_error_message, auth_state, "
            "cookies_applied_id, version) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?) "
            "ON CONFLICT(worker) DO UPDATE SET state = excluded.state, "
            "last_heartbeat_at = excluded.last_heartbeat_at, last_success_at = excluded.last_success_at, "
            "consecutive_failures = excluded.consecutive_failures, interval_seconds = excluded.interval_seconds, "
            "next_attempt_at = excluded.next_attempt_at, last_error_code = excluded.last_error_code, "
            "last_error_message = excluded.last_error_message, auth_state = excluded.auth_state, "
            "cookies_applied_id = excluded.cookies_applied_id, version = excluded.version",
            (
                WORKER_NAME,
                s.state,
                s.last_heartbeat_at,
                s.last_success_at,
                s.consecutive_failures,
                s.interval_seconds,
                s.last_error_code,
                s.last_error_message[:200] if s.last_error_message else None,
                s.auth_state,
                s.cookies_applied_id,
                s.version,
            ),
        )

    def transaction(self) -> "_Tx":
        return _Tx(self.con)


class _Tx:
    """BEGIN IMMEDIATE ... COMMIT/ROLLBACK (um ciclo = uma transacao curta)."""

    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def __enter__(self) -> sqlite3.Connection:
        self.con.execute("BEGIN IMMEDIATE")
        return self.con

    def __exit__(self, exc_type, exc, tb) -> bool:
        if exc_type is None:
            self.con.execute("COMMIT")
        else:
            self.con.execute("ROLLBACK")
        return False


def read_heartbeat(db_path: str) -> tuple[int, int] | None:
    """(last_heartbeat_at, interval_seconds) da linha `inscritos`, ou None se nao houver."""
    con = connect(db_path, role="healthcheck")
    try:
        row = con.execute(
            "SELECT last_heartbeat_at, interval_seconds FROM worker_status WHERE worker = ?", (WORKER_NAME,)
        ).fetchone()
    finally:
        con.close()
    return None if row is None else (int(row[0]), int(row[1]))
