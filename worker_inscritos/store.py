"""Acesso ao SQLite compartilhado (/data/livestats.db) pelo worker-inscritos.

- Nunca executa DDL e nunca cria o arquivo: quem aplica db/schema.sql e a api (data-model.md 1).
- Toda conexao instala `set_authorizer` com as permissoes de data-model.md 2: leitura de `cookie_sets`, `schema_meta` e,
  so nas colunas de que precisa, `counters` e `users` (D51); escrita em `subscriber_reading`, `counter_status` e
  `worker_status`; em `cookie_sets` so INSERT e DELETE (D37); nada mais. E uma defesa contra BUG do proprio codigo
  (SQL errada), nao contra processo comprometido (o isolamento real e por volume e criptografia).
- D51: toda leitura e escrita de contador leva `counter_id` explicito; nada do contador de um usuario toca o de outro.
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

# Leitura permitida (tabela -> colunas, None = qualquer). Em `worker_status.worker` so a chave: o SQLite le a coluna do
# conflito ao executar o UPSERT; os demais campos nao sao legiveis.
_READ_OK: dict[str, set[str] | None] = {
    "cookie_sets": None,
    "schema_meta": None,
    "worker_status": {"worker"},
    "subscriber_reading": None,  # D51: o UPSERT le a coluna do conflito (counter_id)
    "channel_info": None,  # D38: idem
    "counter_status": None,  # D51: so este worker escreve
    # D51: quais contadores existem e se o dono os mantem ativos (so as colunas necessarias)
    "counters": {"id", "owner_id", "channel_override"},  # D54: o override so e lido aqui (a api escreve)
    "users": {"id", "role", "status", "has_counter"},
    # D43/D47 (esquema v7): historico por canal, saude e eventos; lidos pelas limpezas e pela poda
    "subscriber_samples": None,
    "health_minutes": None,
    "events": None,
}
_WRITE_OK = {
    "subscriber_reading",
    "worker_status",
    "channel_info",
    "counter_status",
    "subscriber_samples",
    "health_minutes",
    "events",
}

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
    mem_bytes: int | None = None  # D56: memoria do container (cgroup) neste batimento


@dataclass
class CounterStatusRow:
    """Estado de um contador (D51). `state` em starting/ok/degraded/error/paused."""

    state: str
    auth_state: str | None
    last_success_at: int | None
    last_attempt_at: int | None
    next_read_at: int | None
    consecutive_failures: int
    last_error_code: str | None
    last_error_message: str | None
    cookies_applied_id: int | None
    loads_last_hour: int


@dataclass(frozen=True)
class CounterInfo:
    """Contador de usuario visto pelo pool: ativo (conta ativa e com contador) e o conjunto de cookies mais novo."""

    counter_id: int
    enabled: bool
    latest_cookie_id: int | None
    channel_override: str | None = None  # D54


class Store:
    def __init__(self, con: sqlite3.Connection):
        self.con = con

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------ contadores (D51)
    def master_counter_id(self) -> int:
        """Contador do dono com papel `master` (id 1 no esquema v8)."""
        row = self.con.execute(
            "SELECT c.id FROM counters c JOIN users u ON u.id = c.owner_id WHERE u.role = 'master' ORDER BY c.id LIMIT 1"
        ).fetchone()
        return 1 if row is None else int(row[0])

    def user_counters(self) -> list[CounterInfo]:
        """Contadores dos usuarios comuns. `enabled` = conta ativa e `has_counter = 1`."""
        linhas = self.con.execute(
            "SELECT c.id, u.status, u.has_counter, (SELECT MAX(s.id) FROM cookie_sets s WHERE s.counter_id = c.id), c.channel_override "
            "FROM counters c JOIN users u ON u.id = c.owner_id WHERE u.role <> 'master' ORDER BY c.id"
        ).fetchall()
        return [
            CounterInfo(int(i), status == "active" and int(tem) == 1, None if ck is None else int(ck), None if ov is None else str(ov))
            for i, status, tem, ck, ov in linhas
        ]

    def get_channel_override(self, counter_id: int) -> str | None:
        """D54: canal que o dono escolheu para este contador (ID UC...), ou None para usar o detectado pelos cookies."""
        row = self.con.execute("SELECT channel_override FROM counters WHERE id = ?", (counter_id,)).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    def get_auth_state(self, counter_id: int) -> str | None:
        row = self.con.execute("SELECT auth_state FROM counter_status WHERE counter_id = ?", (counter_id,)).fetchone()
        return None if row is None or row[0] is None else str(row[0])

    def write_counter_status(self, counter_id: int, s: CounterStatusRow) -> None:
        self.con.execute(
            "INSERT INTO counter_status (counter_id, state, auth_state, last_success_at, last_attempt_at, next_read_at, "
            "consecutive_failures, last_error_code, last_error_message, cookies_applied_id, loads_last_hour) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(counter_id) DO UPDATE SET state = excluded.state, auth_state = excluded.auth_state, "
            "last_success_at = excluded.last_success_at, last_attempt_at = excluded.last_attempt_at, "
            "next_read_at = excluded.next_read_at, consecutive_failures = excluded.consecutive_failures, "
            "last_error_code = excluded.last_error_code, last_error_message = excluded.last_error_message, "
            "cookies_applied_id = excluded.cookies_applied_id, loads_last_hour = excluded.loads_last_hour",
            (
                counter_id,
                s.state,
                s.auth_state,
                s.last_success_at,
                s.last_attempt_at,
                s.next_read_at,
                s.consecutive_failures,
                s.last_error_code[:40] if s.last_error_code else None,
                s.last_error_message[:200] if s.last_error_message else None,
                s.cookies_applied_id,
                max(0, s.loads_last_hour),
            ),
        )

    # ------------------------------------------------------------------ cookies
    def latest_cookie_set_id(self, counter_id: int) -> int | None:
        row = self.con.execute(
            "SELECT id FROM cookie_sets WHERE counter_id = ? ORDER BY id DESC LIMIT 1", (counter_id,)
        ).fetchone()
        return None if row is None else int(row[0])

    def get_cookie_set(self, counter_id: int, set_id: int) -> CookieSetRow | None:
        row = self.con.execute(
            "SELECT id, key_id, ciphertext, source, created_at FROM cookie_sets WHERE id = ? AND counter_id = ?",
            (set_id, counter_id),
        ).fetchone()
        return (
            None
            if row is None
            else CookieSetRow(int(row[0]), str(row[1]), bytes(row[2]), str(row[3]), str(row[4]))
        )

    def get_channel_info(self, counter_id: int) -> tuple[str, str] | None:
        """(channel_id, channel_name) gravados (D38) do contador; None se ainda nao houve deteccao."""
        row = self.con.execute(
            "SELECT channel_id, channel_name FROM channel_info WHERE counter_id = ?", (counter_id,)
        ).fetchone()
        return None if row is None else (str(row[0]), str(row[1]))

    # ------------------------------------------------------------------ escrita
    def save_reading(self, counter_id: int, value: str, raw: str, read_at: str) -> None:
        """Linha unica por contador, reescrita a cada leitura valida (mesmo com valor igual). `raw` <= 256 (D20)."""
        self.con.execute(
            "INSERT INTO subscriber_reading (counter_id, value, raw, read_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(counter_id) DO UPDATE SET value = excluded.value, raw = excluded.raw, read_at = excluded.read_at",
            (counter_id, value, raw[:256], read_at),
        )

    def save_channel_info(self, counter_id: int, channel_id: str, channel_name: str, detected_at: str) -> None:
        """D38: linha unica por contador, so este worker escreve. Nome limitado a 200 caracteres."""
        self.con.execute(
            "INSERT INTO channel_info (counter_id, channel_id, channel_name, detected_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(counter_id) DO UPDATE SET channel_id = excluded.channel_id, "
            "channel_name = excluded.channel_name, detected_at = excluded.detected_at",
            (counter_id, channel_id, channel_name[:200], detected_at),
        )

    def activate_channel(
        self, counter_id: int, channel_id: str, channel_name: str, detected_at: str
    ) -> tuple[int, int] | None:
        """Ativa canal detectado e restaura sua ultima amostra; nunca reutiliza o contador de outro canal."""
        with self.transaction():
            self.save_channel_info(counter_id, channel_id, channel_name, detected_at)
            row = self.con.execute(
                "SELECT at, value FROM subscriber_samples WHERE counter_id = ? AND channel_id = ? "
                "ORDER BY at DESC LIMIT 1",
                (counter_id, channel_id),
            ).fetchone()
            if row is None:
                self.con.execute("DELETE FROM subscriber_reading WHERE counter_id = ?", (counter_id,))
                return None
            at, value = int(row[0]), int(row[1])
            self.save_reading(counter_id, str(value), str(value), iso_utc(at))
            return at, value

    def save_subscriber_sample(self, counter_id: int, channel_id: str, at: int, value: int) -> bool:
        """Uma amostra por minuto, contador e canal, substituida pela leitura mais recente; apaga as > 8 dias."""
        minuto = at // 60 * 60
        with self.transaction():
            cur = self.con.execute(
                "INSERT INTO subscriber_samples (counter_id, channel_id, at, value) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(counter_id, channel_id, at) DO UPDATE SET value = excluded.value",
                (counter_id, channel_id, minuto, value),
            )
            self.con.execute(
                "DELETE FROM subscriber_samples WHERE counter_id = ? AND at < ?",
                (counter_id, at - SAMPLES_KEEP_SECONDS),
            )
        return cur.rowcount > 0

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
        self, counter_id: int, ciphertext: bytes, key_id: str, count: int, applied_id: int, created_at: str
    ) -> int | None:
        """D37: grava a renovacao so se o conjunto mais recente DO CONTADOR ainda e o aplicado (`MAX(id) = applied_id`).

        INSERT da nova linha e DELETE das anteriores (so deste contador) na mesma transacao.
        None = descartada (o dono colou outro).
        """
        with self.transaction():
            atual = self.con.execute(
                "SELECT MAX(id) FROM cookie_sets WHERE counter_id = ?", (counter_id,)
            ).fetchone()[0]
            if atual is None or int(atual) != applied_id:
                return None
            cur = self.con.execute(
                "INSERT INTO cookie_sets (counter_id, ciphertext, key_id, cookie_count, uploaded_by, created_at, "
                "source, renewed_from) VALUES (?, ?, ?, ?, 'worker', ?, 'worker', ?)",
                (counter_id, ciphertext, key_id, count, created_at, applied_id),
            )
            novo = int(cur.lastrowid)
            self.con.execute("DELETE FROM cookie_sets WHERE counter_id = ? AND id <> ?", (counter_id, novo))
        return novo

    def write_status(self, s: StatusRow) -> None:
        self.con.execute(
            "INSERT INTO worker_status (worker, state, last_heartbeat_at, last_success_at, consecutive_failures, "
            "interval_seconds, next_attempt_at, last_error_code, last_error_message, auth_state, "
            "cookies_applied_id, version, mem_bytes) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(worker) DO UPDATE SET state = excluded.state, "
            "last_heartbeat_at = excluded.last_heartbeat_at, last_success_at = excluded.last_success_at, "
            "consecutive_failures = excluded.consecutive_failures, interval_seconds = excluded.interval_seconds, "
            "next_attempt_at = excluded.next_attempt_at, last_error_code = excluded.last_error_code, "
            "last_error_message = excluded.last_error_message, auth_state = excluded.auth_state, "
            "cookies_applied_id = excluded.cookies_applied_id, version = excluded.version, mem_bytes = excluded.mem_bytes",
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
                s.mem_bytes,
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
