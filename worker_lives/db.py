"""Acesso ao SQLite compartilhado (`/data/livestats.db`) pelo worker-lives.

* O worker NUNCA cria nem altera tabelas: o banco é aberto com `mode=rw` (não cria o arquivo) e só a
  api aplica `db/schema.sql`.
* Toda conexão instala `set_authorizer` com as permissões de cada tabela. Isso é uma
  defesa contra bug no próprio código (ex.: SQL escrito na tabela errada), não contra um processo
  comprometido.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

# worker-lives: lê `lives` (e `schema_meta` para saber se o esquema existe); escreve `live_stats` e
# `worker_status` e, desde o esquema v6 (D43), o histórico e a saúde: `live_samples`, `set_peaks`, `live_reads`,
# `health_minutes` e `events`. Nada mais (nem leitura de `cookie_sets`). O worker nunca faz DDL.
_V6_TABLES = frozenset({"live_samples", "set_peaks", "live_reads", "health_minutes", "events"})
# live_stats: o UPSERT lê a chave do conflito e o worker compara o estado anterior para gerar eventos.
# As tabelas v6 são lidas pelos UPSERTs, pelas limpezas (`WHERE at < ?`) e pela poda de `events`.
READ_TABLES = frozenset({"lives", "schema_meta", "live_stats"}) | _V6_TABLES
WRITE_TABLES = frozenset({"live_stats", "worker_status"}) | _V6_TABLES

_READ_COLUMNS = {"sets": frozenset({"id"})}  # tabelas legíveis só em certas colunas (a FK de set_peaks)

SAMPLES_KEEP_SECONDS = 7200  # D43: amostras com mais de 2 h saem
HEALTH_KEEP_SECONDS = 7200
PEAKS_KEEP_DAYS = 30
EVENTS_KEEP = 500

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
            if arg1 in _READ_COLUMNS:  # o SQLite lê a chave do pai ao checar a FK de `set_peaks`; só ela é legível
                return sqlite3.SQLITE_OK if arg2 in _READ_COLUMNS[arg1] else sqlite3.SQLITE_DENY
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


@dataclass(frozen=True)
class LiveRow:
    id: int
    platform: str
    ref: str
    set_id: int
    label: str


def read_lives(conn: sqlite3.Connection) -> list[LiveRow]:
    """Todas as lives, na ordem do id."""
    rows = conn.execute("SELECT id, platform, ref, set_id, label FROM lives ORDER BY id").fetchall()
    return [LiveRow(int(i), str(p), str(r), int(s), str(lb)) for i, p, r, s, lb in rows]


def previous_states(conn: sqlite3.Connection, live_ids: list[int]) -> dict[int, tuple[str, str | None]]:
    """(status, error_code) gravados em `live_stats` ANTES da leitura nova: base para gerar eventos só de mudança."""
    out: dict[int, tuple[str, str | None]] = {}
    for live_id in live_ids:
        row = conn.execute("SELECT status, error_code FROM live_stats WHERE live_id = ?", (live_id,)).fetchone()
        if row is not None:
            out[live_id] = (str(row[0]), row[1])
    return out


def write_sample(conn: sqlite3.Connection, live_id: int, at: int, viewers: int | None, likes: int | None) -> None:
    """D43: uma amostra por leitura bem-sucedida (live apagada no meio: FK falha e é ignorada)."""
    try:
        conn.execute(
            "INSERT OR REPLACE INTO live_samples (live_id, at, viewers, likes) VALUES (?, ?, ?, ?)",
            (live_id, at, viewers, likes),
        )
    except sqlite3.IntegrityError:
        pass


def write_read(
    conn: sqlite3.Connection, live_id: int, last_read_at: int, next_read_at: int, latency_ms: int | None, error_code: str | None
) -> None:
    """D43: última leitura (ok ou erro) da live, com a latência e a próxima leitura agendada."""
    try:
        conn.execute(
            "INSERT INTO live_reads (live_id, last_read_at, next_read_at, latency_ms, error_code) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT(live_id) DO UPDATE SET last_read_at = excluded.last_read_at,"
            " next_read_at = excluded.next_read_at, latency_ms = excluded.latency_ms, error_code = excluded.error_code",
            (live_id, last_read_at, max(next_read_at, last_read_at), latency_ms, error_code),
        )
    except sqlite3.IntegrityError:
        pass


def write_health(conn: sqlite3.Connection, minute: int, avg_ms: int, max_ms: int, ok: int, errors: int) -> None:
    """D43: agregado do minuto deste worker (reescrito a cada gravação do minuto em andamento)."""
    conn.execute(
        "INSERT OR REPLACE INTO health_minutes (worker, minute, avg_ms, max_ms, ok, errors) VALUES ('lives', ?, ?, ?, ?, ?)",
        (minute, avg_ms, max_ms, ok, errors),
    )


def write_event(conn: sqlite3.Connection, at: int, level: str, code: str, message: str, live_id: int | None) -> None:
    """D43: evento (só mudanças) e poda para no máximo 500 linhas deste worker."""
    conn.execute(
        "INSERT INTO events (at, source, level, code, message, live_id) VALUES (?, 'lives', ?, ?, ?, ?)",
        (at, level, code[:40], message[:200], live_id),
    )
    conn.execute(
        "DELETE FROM events WHERE source = 'lives' AND id <= "
        "(SELECT id FROM events WHERE source = 'lives' ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (EVENTS_KEEP,),
    )


def update_peaks(conn: sqlite3.Connection, day: str, at: int) -> None:
    """D43: guarda o maior total de espectadores do dia por conjunto (soma das lives sem erro no momento)."""
    conn.execute(
        "INSERT INTO set_peaks (set_id, day, viewers, at)"
        " SELECT l.set_id, :day, SUM(COALESCE(s.viewers, 0)) AS total, :at"
        " FROM lives l JOIN live_stats s ON s.live_id = l.id WHERE s.status <> 'erro'"
        " GROUP BY l.set_id HAVING total > 0"
        " ON CONFLICT(set_id, day) DO UPDATE SET viewers = excluded.viewers, at = excluded.at"
        " WHERE excluded.viewers > set_peaks.viewers",
        {"day": day, "at": at},
    )


def cleanup(conn: sqlite3.Connection, now: int, oldest_peak_day: str) -> None:
    """D43, uma vez por minuto: amostras > 7200 s, saúde > 2 h e picos > 30 dias."""
    conn.execute("DELETE FROM live_samples WHERE at < ?", (now - SAMPLES_KEEP_SECONDS,))
    conn.execute("DELETE FROM health_minutes WHERE worker = 'lives' AND minute < ?", (now - HEALTH_KEEP_SECONDS,))
    conn.execute("DELETE FROM set_peaks WHERE day < ?", (oldest_peak_day,))


@dataclass(frozen=True)
class Reading:
    """Resultado de uma leitura (ou o erro dela) a gravar em `live_stats`."""

    status: str  # ao_vivo | agendada | encerrada | offline | erro
    title: str = ""
    viewers: int | None = None
    likes: int | None = None
    error_code: str | None = None


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
    "INSERT INTO live_stats (live_id, status, title, viewers, likes, updated_at, error_code)"
    " VALUES (?, ?, ?, ?, ?, ?, ?)"
    " ON CONFLICT(live_id) DO UPDATE SET status = excluded.status, title = excluded.title,"
    " viewers = excluded.viewers, likes = excluded.likes, updated_at = excluded.updated_at,"
    " error_code = excluded.error_code"
)
# Em erro, os últimos valores bons (título, espectadores, likes) ficam; só estado, código e hora mudam.
_UPSERT_ERROR = (
    "INSERT INTO live_stats (live_id, status, title, viewers, likes, updated_at, error_code)"
    " VALUES (?, 'erro', '', NULL, NULL, ?, ?)"
    " ON CONFLICT(live_id) DO UPDATE SET status = 'erro', updated_at = excluded.updated_at,"
    " error_code = excluded.error_code"
)
_UPSERT_STATUS = (
    "INSERT OR REPLACE INTO worker_status (worker, state, last_heartbeat_at, last_success_at,"
    " consecutive_failures, interval_seconds, next_attempt_at, last_error_code,"
    " last_error_message, auth_state, cookies_applied_id, version)"
    " VALUES ('lives', ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?)"
)


def write_readings(
    conn: sqlite3.Connection, live_ids: list[int], reading: Reading, updated_at: str
) -> int:
    """UPSERT de `live_stats` para cada live do par lido; devolve quantas foram gravadas.

    Live apagada entre a leitura e o UPSERT (FK falha) é ignorada, sem erro.
    """
    gravadas = 0
    for live_id in live_ids:
        try:
            if reading.status == "erro":
                conn.execute(_UPSERT_ERROR, (live_id, updated_at, reading.error_code))
            else:
                conn.execute(
                    _UPSERT_LIVE,
                    (live_id, reading.status, reading.title, reading.viewers, reading.likes, updated_at, None),
                )
        except sqlite3.IntegrityError:
            continue
        gravadas += 1
    return gravadas


def write_status(conn: sqlite3.Connection, status: StatusRow) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        write_status_na_transacao(conn, status)
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def write_status_na_transacao(conn: sqlite3.Connection, status: StatusRow) -> None:
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
