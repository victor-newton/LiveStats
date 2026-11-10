"""Trilha de auditoria do painel mestre (D52): ``audit_log`` no banco compartilhado, escrita SOMENTE pela api.

Registra login ok/falha (nunca a senha), troca e reset de senha, conta criada/suspensa/reativada/apagada/purgada,
conjunto e live criados/apagados e cookies enviados. Mantem as 5000 linhas mais recentes. Chamar DENTRO da transacao
da acao (como ``history.record_event``) ou numa transacao propria curta (``record_now``).
"""

from __future__ import annotations

import sqlite3

from . import db

AUDIT_MAX = 5000
DETAIL_MAX = 200


def record(
    conn: sqlite3.Connection, now: float, actor_id: int | None, actor_name: str, action: str, target: str = "", detail: str = ""
) -> None:
    conn.execute(
        "INSERT INTO audit_log (at, actor_id, actor_name, action, target, detail) VALUES (?, ?, ?, ?, ?, ?)",
        (int(now), actor_id, actor_name[:64], action[:40], target[:80], detail[:DETAIL_MAX]),
    )
    conn.execute(
        "DELETE FROM audit_log WHERE id <= (SELECT id FROM audit_log ORDER BY id DESC LIMIT 1 OFFSET ?)", (AUDIT_MAX,)
    )


def record_now(conn: sqlite3.Connection, now: float, actor_id: int | None, actor_name: str, action: str, target: str = "", detail: str = "") -> None:
    """Grava numa transacao propria (para quem ainda nao esta numa)."""
    with db.transaction(conn, immediate=True):
        record(conn, now, actor_id, actor_name, action, target, detail)


def list_events(conn: sqlite3.Connection, limit: int) -> list[dict]:
    rows = conn.execute(
        "SELECT at, actor_name, action, target, detail FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        {"at": r["at"], "actor": r["actor_name"], "action": r["action"], "target": r["target"], "detail": r["detail"]}
        for r in rows
    ]
