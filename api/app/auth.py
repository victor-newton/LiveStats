"""Login, sessoes e limite de tentativas do admin (D3, D4, D5; v4: D48 usuarios e papeis).

O estado de sessao e de credenciais vive em ``admin.db`` (volume exclusivo da api, D2); o cadastro de usuarios
(``status``, ``role``) vive em ``livestats.db`` e e RECARREGADO a cada requisicao: suspenso ou apagado perde a sessao
na hora. O id da sessao so existe no cookie do navegador; no banco fica o SHA-256 dele. Nenhuma senha (nem hash)
vai a log ou a resposta.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass, replace

import argon2
from argon2.exceptions import InvalidHashError, VerificationError

from . import audit, db
from .clock import Clock
from .config import Settings
from .users import UserRecord, load_user, load_user_by_name

SESSION_COOKIE = "__Host-livestats_session"
ABSOLUTE_TTL = 8 * 3600       # Max-Age do cookie e limite absoluto
IDLE_TTL = 30 * 60            # ociosidade
TOUCH_INTERVAL = 60           # last_seen_at e atualizado no maximo 1x/min
FAIL_LIMIT = 5                # falhas na janela => proxima tentativa recebe 429
FAIL_WINDOW = 60
ATTEMPT_RETENTION = 24 * 3600
SWEEP_INTERVAL = 10 * 60
MIN_PASSWORD = 8              # D48
TEMP_PASSWORD_LENGTH = 16
_TEMP_ALPHABET = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sem 0/O, 1/l/I

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
    user_id: int
    username: str
    created_at: int
    last_seen_at: int
    expires_at: int

    @property
    def idle_expires_at(self) -> int:
        return min(self.last_seen_at + IDLE_TTL, self.expires_at)


@dataclass(frozen=True)
class Ctx:
    """Sessao validada + usuario recarregado do banco nesta requisicao."""

    session: SessionData
    user: UserRecord

    @property
    def is_master(self) -> bool:
        return self.user.is_master


def hash_session_id(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()


def user_key(username: str) -> str:
    """Chave do contador por usuario: minusculo e truncado em 64 (``data-model.md``)."""
    return username.strip().lower()[:64]


def ip_allowed(networks: tuple, ip: str) -> bool:
    """``MASTER_ALLOW_CIDRS``: vazio = qualquer IP; senao o IP precisa estar em alguma das redes IPv4."""
    if not networks:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.version == 4 and any(address in net for net in networks)


def generate_temp_password() -> str:
    return "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(TEMP_PASSWORD_LENGTH))


class AdminAuth:
    def __init__(self, settings: Settings, clock: Clock) -> None:
        self._db_path = settings.admin_db_path
        self._shared_path = settings.db_path
        self._master_cidrs = settings.master_allow_cidrs
        self._hasher = argon2.PasswordHasher()
        self._dummy_hash = self._hasher.hash(secrets.token_urlsafe(24))  # tempo uniforme para usuario inexistente
        self._clock = clock
        self._last_sweep = 0.0
        self._sweep_lock = threading.Lock()

    # ------------------------------------------------------------------ senhas
    def hash_password(self, password: str) -> str:
        return self._hasher.hash(password)

    def _verify(self, stored_hash: str, password: str) -> bool:
        try:
            return bool(self._hasher.verify(stored_hash, password))
        except (VerificationError, InvalidHashError):
            return False

    def set_password(self, user_id: int, password: str, must_change: bool) -> None:
        """Grava (ou troca) a credencial do usuario. Quem chama decide sobre as sessoes."""
        hashed = self.hash_password(password)
        with db.connection(db.open_admin, self._db_path) as conn:
            with db.transaction(conn, immediate=True):
                conn.execute(
                    "INSERT INTO credentials (user_id, password_hash, must_change, updated_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id) DO UPDATE SET password_hash = excluded.password_hash, "
                    "must_change = excluded.must_change, updated_at = excluded.updated_at",
                    (user_id, hashed, 1 if must_change else 0, int(self._clock.now())),
                )

    def drop_sessions(self, user_id: int, keep_id_hash: str | None = None) -> None:
        with db.connection(db.open_admin, self._db_path) as conn:
            conn.execute(
                "DELETE FROM admin_sessions WHERE user_id = ? AND (? IS NULL OR id_hash <> ?)",
                (user_id, keep_id_hash, keep_id_hash),
            )

    def delete_credentials(self, user_id: int) -> None:
        with db.connection(db.open_admin, self._db_path) as conn:
            with db.transaction(conn, immediate=True):
                conn.execute("DELETE FROM credentials WHERE user_id = ?", (user_id,))
                conn.execute("DELETE FROM admin_sessions WHERE user_id = ?", (user_id,))

    def must_change_map(self) -> dict[int, bool]:
        with db.connection(db.open_admin, self._db_path) as conn:
            return {r["user_id"]: bool(r["must_change"]) for r in conn.execute("SELECT user_id, must_change FROM credentials")}

    # ------------------------------------------------------------------ login
    def _reserve_attempt(self, conn: sqlite3.Connection, ip: str, ukey: str, now: int) -> int:
        """Confere o bloqueio e registra a tentativa (como falha) na mesma transacao.

        Reservar antes de verificar a senha faz tentativas paralelas contarem. Tentativas bloqueadas
        (429) nao sao registradas. O bloqueio vale por IP E por usuario (D48/D49).
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

    def _audit(self, now: int, actor_id: int | None, actor_name: str, action: str, target: str = "", detail: str = "") -> None:
        try:
            with db.connection(db.open_shared, self._shared_path) as shared:
                audit.record_now(shared, now, actor_id, actor_name, action, target, detail)
        except sqlite3.Error:  # a trilha nunca derruba o login
            pass

    def login(self, username: str, password: str, ip: str, previous_session_id: str | None) -> tuple[str, SessionData, UserRecord]:
        """Devolve (id da sessao para o cookie, dados da sessao, usuario) ou levanta ``LoginBlocked``/``InvalidCredentials``."""
        now = int(self._clock.now())
        ukey = user_key(username)
        ip = ip[:64]
        with db.connection(db.open_admin, self._db_path) as conn:
            self._maybe_sweep(conn, now)
            attempt_id = self._reserve_attempt(conn, ip, ukey, now)
            with db.connection(db.open_shared, self._shared_path) as shared:
                user = load_user_by_name(shared, ukey)
            eligible = user is not None and user.status == "active" and (
                not user.is_master or ip_allowed(self._master_cidrs, ip)
            )
            stored = None
            if eligible:
                row = conn.execute("SELECT password_hash FROM credentials WHERE user_id = ?", (user.id,)).fetchone()
                stored = row["password_hash"] if row else None
            # Sempre uma verificacao argon2, exista o usuario ou nao (tempo uniforme).
            password_ok = self._verify(stored or self._dummy_hash, password)
            if not (eligible and stored and password_ok):
                shown = ukey if db.valid_username(ukey) else "(invalido)"
                self._audit(now, user.id if user else None, shown, "login_failed", shown)
                raise InvalidCredentials
            session_id = secrets.token_urlsafe(32)
            data = SessionData(
                id_hash=hash_session_id(session_id),
                csrf_token=secrets.token_urlsafe(32),
                user_id=user.id,
                username=user.username,
                created_at=now,
                last_seen_at=now,
                expires_at=now + ABSOLUTE_TTL,
            )
            with db.transaction(conn, immediate=True):
                conn.execute("UPDATE login_attempts SET success = 1 WHERE id = ?", (attempt_id,))
                if previous_session_id and _COOKIE_SHAPE.fullmatch(previous_session_id):
                    conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (hash_session_id(previous_session_id),))
                conn.execute(
                    "INSERT INTO admin_sessions (id_hash, csrf_token, user_id, username, client_ip, created_at, last_seen_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (data.id_hash, data.csrf_token, user.id, data.username, ip[:64], now, now, data.expires_at),
                )
            cred = conn.execute("SELECT must_change FROM credentials WHERE user_id = ?", (user.id,)).fetchone()
            must_change = bool(cred["must_change"]) if cred else False
            with db.connection(db.open_shared, self._shared_path) as shared:
                with db.transaction(shared, immediate=True):
                    shared.execute("UPDATE users SET last_login_at = ?, last_seen_at = ? WHERE id = ?", (now, now, user.id))
                    audit.record(shared, now, user.id, user.username, "login_ok", user.username)
            return session_id, data, replace(user, must_change=must_change, last_login_at=now)

    # ------------------------------------------------------------------ sessao
    def resolve(self, session_id: str | None) -> Ctx | None:
        """Valida o cookie (existe, dentro dos limites) E recarrega o usuario: suspenso ou apagado perde a sessao na hora."""
        if not session_id or not _COOKIE_SHAPE.fullmatch(session_id):
            return None
        now = int(self._clock.now())
        id_hash = hash_session_id(session_id)
        with db.connection(db.open_admin, self._db_path) as conn:
            try:
                session = self._read_session(conn, id_hash, now)
                if session is None:
                    return None
                with db.connection(db.open_shared, self._shared_path) as shared:
                    user = load_user(shared, session.user_id)
                    if user is not None and user.status == "active":
                        if user.last_seen_at is None or now - user.last_seen_at >= TOUCH_INTERVAL:
                            shared.execute("UPDATE users SET last_seen_at = ? WHERE id = ?", (now, user.id))
                if user is None or user.status != "active":
                    conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (id_hash,))
                    return None
                cred = conn.execute("SELECT must_change FROM credentials WHERE user_id = ?", (user.id,)).fetchone()
                must_change = bool(cred["must_change"]) if cred else False
                return Ctx(session=session, user=replace(user, must_change=must_change))
            finally:
                self._maybe_sweep(conn, now)  # varredura DEPOIS da checagem propria

    @staticmethod
    def _read_session(conn: sqlite3.Connection, id_hash: str, now: int) -> SessionData | None:
        with db.transaction(conn, immediate=True):
            row = conn.execute(
                "SELECT id_hash, csrf_token, user_id, username, created_at, last_seen_at, expires_at "
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
                user_id=row["user_id"],
                username=row["username"],
                created_at=row["created_at"],
                last_seen_at=last_seen,
                expires_at=row["expires_at"],
            )

    def logout(self, session: SessionData) -> None:
        with db.connection(db.open_admin, self._db_path) as conn:
            conn.execute("DELETE FROM admin_sessions WHERE id_hash = ?", (session.id_hash,))

    # ------------------------------------------------------------------ troca de senha
    def change_password(self, ctx: Ctx, current: str, new: str, ip: str) -> None:
        """Confere a senha atual (com o mesmo limite de tentativas do login), grava a nova e derruba as OUTRAS sessoes do usuario."""
        now = int(self._clock.now())
        ukey = user_key(ctx.user.username)
        with db.connection(db.open_admin, self._db_path) as conn:
            attempt_id = self._reserve_attempt(conn, ip[:64], ukey, now)
            row = conn.execute("SELECT password_hash FROM credentials WHERE user_id = ?", (ctx.user.id,)).fetchone()
            if not self._verify(row["password_hash"] if row else self._dummy_hash, current) or row is None:
                raise InvalidCredentials
            hashed = self.hash_password(new)
            with db.transaction(conn, immediate=True):
                conn.execute("UPDATE login_attempts SET success = 1 WHERE id = ?", (attempt_id,))
                conn.execute(
                    "UPDATE credentials SET password_hash = ?, must_change = 0, updated_at = ? WHERE user_id = ?",
                    (hashed, now, ctx.user.id),
                )
                conn.execute(
                    "DELETE FROM admin_sessions WHERE user_id = ? AND id_hash <> ?", (ctx.user.id, ctx.session.id_hash)
                )

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
