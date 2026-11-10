"""Usuarios (D48): leitura do registro, formato publico e limites. A senha nao mora aqui (``credentials`` no admin.db)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

USER_COLUMNS = "id, username, display_name, role, status, has_counter, max_sets, max_lives, created_at, last_login_at, last_seen_at"


@dataclass(frozen=True)
class UserRecord:
    id: int
    username: str
    display_name: str
    role: str
    status: str
    has_counter: bool
    max_sets: int
    max_lives: int
    created_at: int
    last_login_at: int | None
    last_seen_at: int | None
    must_change: bool = False

    @property
    def is_master(self) -> bool:
        return self.role == "master"

    def public(self) -> dict:
        """O objeto ``user`` de ``GET /api/admin/session`` e do login (D48)."""
        return {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "must_change_password": self.must_change,
            "has_counter": self.has_counter,
            "max_sets": self.max_sets,
            "max_lives": self.max_lives,
        }


def from_row(row: sqlite3.Row, must_change: bool = False) -> UserRecord:
    return UserRecord(
        id=row["id"],
        username=row["username"],
        display_name=row["display_name"],
        role=row["role"],
        status=row["status"],
        has_counter=bool(row["has_counter"]),
        max_sets=row["max_sets"],
        max_lives=row["max_lives"],
        created_at=row["created_at"],
        last_login_at=row["last_login_at"],
        last_seen_at=row["last_seen_at"],
        must_change=must_change,
    )


def load_user(conn: sqlite3.Connection, user_id: int) -> UserRecord | None:
    row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()  # noqa: S608 (constante)
    return from_row(row) if row else None


def load_user_by_name(conn: sqlite3.Connection, username: str) -> UserRecord | None:
    row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE username = ?", (username,)).fetchone()  # noqa: S608
    return from_row(row) if row else None


def purge_owner_data(conn: sqlite3.Connection, user_id: int) -> dict[str, int]:
    """Apaga TUDO que o usuario criou e mantem a conta: conjuntos e lives (leituras, amostras e picos saem em CASCADE) e, no
    contador dele, cookies, leitura, canal detectado, amostras e estado; a aparencia volta ao padrao e o canal escolhido
    (D54) e solto. Nunca toca em usuario, senha, sessoes nem limites. Chamar DENTRO de uma transacao.

    Devolve as contagens apagadas: ``sets``, ``lives``, ``samples`` (amostras de inscritos) e ``cookies`` (conjuntos).
    """
    counts = {
        "sets": conn.execute("SELECT count(*) FROM sets WHERE owner_id = ?", (user_id,)).fetchone()[0],
        "lives": conn.execute(
            "SELECT count(*) FROM lives l JOIN sets s ON s.id = l.set_id WHERE s.owner_id = ?", (user_id,)
        ).fetchone()[0],
        "samples": 0,
        "cookies": 0,
    }
    counter = conn.execute("SELECT id FROM counters WHERE owner_id = ?", (user_id,)).fetchone()
    if counter is not None:
        cid = counter[0]
        counts["samples"] = conn.execute("SELECT count(*) FROM subscriber_samples WHERE counter_id = ?", (cid,)).fetchone()[0]
        counts["cookies"] = conn.execute("SELECT count(*) FROM cookie_sets WHERE counter_id = ?", (cid,)).fetchone()[0]
    conn.execute("DELETE FROM sets WHERE owner_id = ?", (user_id,))
    if counter is not None:
        for table in ("cookie_sets", "subscriber_reading", "channel_info", "subscriber_samples", "counter_status"):
            conn.execute(f"DELETE FROM {table} WHERE counter_id = ?", (cid,))  # noqa: S608 (nomes fixos)
        conn.execute(
            "UPDATE counters SET font = 'Roboto', text_color = '#FFFFFF', channel_override = NULL WHERE id = ?", (cid,)
        )
    return counts
