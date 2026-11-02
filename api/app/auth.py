"""Login, sessoes e limite de tentativas do admin (D3, D4, D5).

Todo o estado (sessoes e tentativas) vive em ``admin.db`` (volume exclusivo da api, D2).
O id da sessao so existe no cookie do navegador; no banco fica o SHA-256 dele.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass

import argon2
from argon2.exceptions import InvalidHashError, VerificationError

from . import db
from .clock import Clock
from .config import Settings

SESSION_COOKIE = "__Host-livestats_session"
ABSOLUTE_TTL = 8 * 3600       # Max-Age do cookie e limite absoluto
IDLE_TTL = 30 * 60            # ociosidade
TOUCH_INTERVAL = 60           # last_seen_at e atualizado no maximo 1x/min
FAIL_LIMIT = 5                # falhas na janela => proxima tentativa recebe 429
FAIL_WINDOW = 60
ATTEMPT_RETENTION = 24 * 3600
SWEEP_INTERVAL = 10 * 60

_COOKIE_SHAPE = re.compile(r"[A-Za-z0-9_-]{16,128}")
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class InvalidCredentials(Exception):
    """Usuario ou senha incorretos (indistinguiveis)."""


class LoginBlocked(Exception):
    def __init__(self, retry_after: int) -> None:
        super().__init__("login bloqueado")
        self.retry_after = max(1, int(retry_after))


@dataclass(frozen=True)
class SessionData:
    id_hash: str
    csrf_token: str
    username: str
    created_at: int
    last_seen_at: int
    expires_at: int

    @property
    def idle_expires_at(self) -> int:
        return min(self.last_seen_at + IDLE_TTL, self.expires_at)


def hash_session_id(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def user_key(username: str) -> str:
    """Chave do contador por usuario: minusculo e truncado em 64 (``data-model.md``)."""
    return username.lower()[:64]


class AdminAuth:
    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._db_path = settings.admin_db_path
        self._username = settings.admin_username.encode("utf-8")
        self._hash = settings.password_hash
        self._hasher = argon2.PasswordHasher()
        self._clock = clock
        self._last_sweep = 0.0
        self._sweep_lock = threading.Lock()

    # ------------------------------------------------------------------ credenciais
    def _check_credentials(self, username: str, password: str) -> bool:
        """Sempre executa a verificacao argon2, mesmo com usuario inexistente (tempo uniforme)."""
        try:
            password_ok = self._hasher.verify(self._hash, password)
        except (VerificationError, InvalidHashError):
            password_ok = False
        user_ok = hmac.compare_digest(username.encode("utf-8"), self._username)
        return bool(password_ok and user_ok)

    # ------------------------------------------------------------------ login
    def _reserve_attempt(self, conn: sqlite3.Connection, ip: str, ukey: str, now: int) -> int:
        """Confere o bloqueio e registra a tentativa (como falha) na mesma transacao.

        Reservar antes de verificar a senha faz tentativas paralelas contarem. Tentativas bloqueadas
        (429) nao sao registradas.
        """
        with db.transaction(conn, immediate=True):
            retry = 0
            for column, value in (("ip", ip), ("username", ukey)):
                rows = conn.execute(
                    f"SELECT attempted_at FROM login_attempts "  # noqa: S608 - coluna fixa, valor parametrizado
                    f"WHERE {column} = ? AND success = 0 AND attempted_at > ? ORDER BY attempted_at",
                    (value, now - FAIL_WINDOW),
                ).fetchall()
                if len(rows) >= FAIL_LIMIT:
                    oldest_blocking = rows[len(rows) - FAIL_LIMIT]["attempted_at"]
                    retry = max(retry, oldest_blocking + FAIL_WINDOW - now)
            if retry:
                raise LoginBlocked(retry)
            cur = conn.execute(
                "INSERT INTO login_attempts (attempted_at, ip, username, success) VALUES (?, ?, ?, 0)",
                (now, ip[:64], ukey),
            )
            return int(cur.lastrowid)

    def login(self, username: str, password: str, ip: str, previous_session_id: str | None) -> tuple[str, SessionData]:
        """Devolve (id da sessao para o cookie, dados da sessao) ou levanta ``LoginBlocked``/``InvalidCredentials``."""
        now = int(self._clock.now())
        ukey = user_key(username)
        ip = ip[:64]
        with db.connection(db.open_admin, self._db_path) as conn:
            self._maybe_sweep(conn, now)
            attempt_id = self._reserve_attempt(conn, ip, ukey, now)
            if not self._check_credentials(username, password):
                raise InvalidCredentials
            session_id = secrets.token_urlsafe(32)
            data = SessionData(
                id_hash=hash_session_id(session_id),
                csrf_token=secrets.token_urlsafe(32),
                username=self._username.decode("utf-8"),
                created_at=now,
                last_seen_at=now,
                expires_at=now + ABSOLUTE_TTL,
            )
            with db.transaction(conn, immediate=True):
                conn.execute("UPDATE login_attempts SET success = 1 WHERE id = ?", (attempt_id,))
                if previous_session_id and _COOKIE_SHAPE.fullmatch(previous_session_id):
                    conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (hash_session_id(previous_session_id),))
                conn.execute(
                    "INSERT INTO admin_sessions (id_hash, csrf_token, username, client_ip, created_at, last_seen_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (data.id_hash, data.csrf_token, data.username, ip[:64], now, now, data.expires_at),
                )
            return session_id, data

    # ------------------------------------------------------------------ sessao
    def load_session(self, session_id: str | None) -> SessionData | None:
        """Valida o cookie: existe, nao passou do limite absoluto nem da ociosidade. Renova a ociosidade."""
        if not session_id or not _COOKIE_SHAPE.fullmatch(session_id):
            return None
        now = int(self._clock.now())
        id_hash = hash_session_id(session_id)
        with db.connection(db.open_admin, self._db_path) as conn:
            try:
                return self._read_session(conn, id_hash, now)
            finally:
                self._maybe_sweep(conn, now)  # varredura DEPOIS da checagem propria

    @staticmethod
    def _read_session(conn: sqlite3.Connection, id_hash: str, now: int) -> SessionData | None:
        with db.transaction(conn, immediate=True):
            row = conn.execute(
                "SELECT id_hash, csrf_token, username, created_at, last_seen_at, expires_at "
                "FROM admin_sessions WHERE id_hash = ?",
                (id_hash,),
            ).fetchone()
            if row is None:
                return None
            if now >= row["expires_at"] or now >= row["last_seen_at"] + IDLE_TTL:
                conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (id_hash,))
                return None
            last_seen = row["last_seen_at"]
            if now - last_seen >= TOUCH_INTERVAL:
                conn.execute("UPDATE admin_sessions SET last_seen_at = ? WHERE id_hash = ?", (now, id_hash))
                last_seen = now
            return SessionData(
                id_hash=row["id_hash"],
                csrf_token=row["csrf_token"],
                username=row["username"],
                created_at=row["created_at"],
                last_seen_at=last_seen,
                expires_at=row["expires_at"],
            )

    def logout(self, session: SessionData) -> None:
        with db.connection(db.open_admin, self._db_path) as conn:
            conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (session.id_hash,))

    @staticmethod
    def csrf_matches(session: SessionData, supplied: str | None) -> bool:
        if not supplied:
            return False
        return hmac.compare_digest(supplied.encode("utf-8"), session.csrf_token.encode("utf-8"))

    # ------------------------------------------------------------------ limpeza
    def _maybe_sweep(self, conn: sqlite3.Connection, now: int) -> None:
        with self._sweep_lock:
            if now - self._last_sweep < SWEEP_INTERVAL:
                return
            self._last_sweep = now
        self.sweep(conn, now)

    @staticmethod
    def sweep(conn: sqlite3.Connection, now: int) -> None:
        with db.transaction(conn, immediate=True):
            conn.execute("DELETE FROM admin_sessions WHERE expires_at <= ? OR last_seen_at + ? <= ?", (now, IDLE_TTL, now))
            conn.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - ATTEMPT_RETENTION,))


def is_unsafe_method(method: str) -> bool:
    return method.upper() in _UNSAFE_METHODS
