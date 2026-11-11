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

GRACE_SECONDS = 600  # D59: folga de 10 min sem nenhuma live do conjunto ao vivo, ainda gravada na mesma sessão
SESSION_GAP_SECONDS = 180  # D59: lacuna maior que isso entre amostras separa duas sessões
FULL_RESOLUTION_SECONDS = 86400  # D59: até 24 h as amostras ficam como foram lidas; depois, 1 por minuto por live
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


def set_has_live(conn: sqlite3.Connection, set_id: int) -> bool:
    """D59: há alguma live do conjunto `ao_vivo` agora (já com a leitura em andamento gravada)?"""
    return conn.execute(
        "SELECT 1 FROM lives l JOIN live_stats s ON s.live_id = l.id WHERE l.set_id = ? AND s.status = 'ao_vivo' LIMIT 1",
        (set_id,),
    ).fetchone() is not None


def session_start(conn: sqlite3.Connection, set_id: int) -> int:
    """D59: início da sessão mais recente do conjunto (primeira amostra depois da última lacuna > 3 min); 0 se não há lacuna."""
    row = conn.execute(
        "SELECT MAX(at) FROM (SELECT at, at - LAG(at) OVER (ORDER BY at) AS gap FROM"
        " (SELECT DISTINCT at FROM live_samples WHERE live_id IN (SELECT id FROM lives WHERE set_id = ?))) WHERE gap > ?",
        (set_id, SESSION_GAP_SECONDS),
    ).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def _prune_sessions(conn: sqlite3.Connection, now: int, retention_days: int) -> None:
    """D59: apaga amostras mais velhas que a retenção, exceto as da sessão ativa (a que ainda recebe amostras)."""
    cut = now - retention_days * 86400
    ativos = [
        int(r[0]) for r in conn.execute(
            "SELECT l.set_id FROM lives l JOIN live_samples s ON s.live_id = l.id GROUP BY l.set_id HAVING MAX(s.at) >= ?",
            (now - SESSION_GAP_SECONDS,),
        )
    ]
    marks = ",".join("?" * len(ativos))
    conn.execute(  # noqa: S608 (so "?" montados)
        f"DELETE FROM live_samples WHERE at < ? AND live_id NOT IN (SELECT id FROM lives WHERE set_id IN ({marks}))",
        (cut, *ativos),
    )
    for set_id in ativos:  # na sessão ativa só o que veio de sessões anteriores à ativa pode sair
        antigas = conn.execute(
            "SELECT 1 FROM live_samples WHERE at < ? AND live_id IN (SELECT id FROM lives WHERE set_id = ?) LIMIT 1", (cut, set_id)
        ).fetchone()
        if antigas is None:
            continue
        limite = min(cut, session_start(conn, set_id))
        conn.execute(
            "DELETE FROM live_samples WHERE at < ? AND live_id IN (SELECT id FROM lives WHERE set_id = ?)", (limite, set_id)
        )


def _compact(conn: sqlite3.Connection, now: int, full: bool) -> None:
    """D59: amostras com mais de 24 h ficam com a primeira de cada minuto por live (a janela repetida a cada minuto é curta)."""
    cut = now - FULL_RESOLUTION_SECONDS
    low = 0 if full else cut - 3 * 3600
    conn.execute(
        "DELETE FROM live_samples WHERE at < :cut AND at >= :low AND at > ("
        " SELECT MIN(s2.at) FROM live_samples s2 WHERE s2.live_id = live_samples.live_id"
        " AND s2.at >= live_samples.at - live_samples.at % 60 AND s2.at < live_samples.at - live_samples.at % 60 + 60)",
        {"cut": cut, "low": low},
    )


def cleanup(conn: sqlite3.Connection, now: int, oldest_peak_day: str, retention_days: int = 14, full: bool = False) -> None:
    """D43/D59, uma vez por minuto: compacta amostras > 24 h, apaga sessões encerradas além da retenção, saúde > 2 h e picos > 30 dias."""
    _compact(conn, now, full)
    _prune_sessions(conn, now, retention_days)
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
    mem_bytes: int | None = None  # D56: memoria do container no batimento


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
    " last_error_message, auth_state, cookies_applied_id, version, mem_bytes)"
    " VALUES ('lives', ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)"
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
            status.mem_bytes,
        ),
    )
