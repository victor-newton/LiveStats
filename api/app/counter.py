"""Contador de inscritos por usuario (D51): leitura do contador, do estado que o worker grava e das URLs publicas."""

from __future__ import annotations

import sqlite3
from typing import Any

from .clock import iso_seconds
from .config import Settings

COUNTER_COLUMNS = "id, owner_id, font, text_color, channel_override"
MASTER_COUNTER_ID = 1
MASTER_INTERVAL_S = 10  # a pagina persistente do mestre le a cada 10 s (D51)


def counter_of_owner(conn: sqlite3.Connection, owner_id: int) -> sqlite3.Row | None:
    return conn.execute(f"SELECT {COUNTER_COLUMNS} FROM counters WHERE owner_id = ?", (owner_id,)).fetchone()  # noqa: S608 (constante)


def _iso(epoch: int | None) -> str | None:
    return iso_seconds(epoch) if epoch is not None else None


def public_links(counter_id: int) -> dict[str, str]:
    """Caminhos relativos publicos. O contador 1 (mestre) segue nos enderecos de sempre; os demais, por id."""
    if counter_id == MASTER_COUNTER_ID:
        return {"json": "/inscritos.json", "subscribers_api": "/api/public/subscribers", "overlay": "/contador"}
    return {
        "json": f"/inscritos/{counter_id}.json",
        "subscribers_api": f"/api/public/subscribers/{counter_id}",
        "overlay": f"/overlay/contador.html?contador={counter_id}",
    }


def _status(conn: sqlite3.Connection, counter_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT state, auth_state, last_success_at, next_read_at, last_error_code, last_error_message, cookies_applied_id "
        "FROM counter_status WHERE counter_id = ?",
        (counter_id,),
    ).fetchone()
    if row is not None:
        return {key: row[key] for key in row.keys()}
    if counter_id != MASTER_COUNTER_ID:
        return {}
    # O contador do mestre pode ainda ser reportado so pelo batimento geral do worker ('inscritos').
    worker = conn.execute(
        "SELECT state, auth_state, last_success_at, next_attempt_at, last_error_code, last_error_message, cookies_applied_id, "
        "interval_seconds FROM worker_status WHERE worker = 'inscritos'"
    ).fetchone()
    if worker is None:
        return {}
    return {
        "state": "paused" if worker["state"] == "stopped" else worker["state"],
        "auth_state": worker["auth_state"],
        "last_success_at": worker["last_success_at"],
        "next_read_at": worker["next_attempt_at"],
        "last_error_code": worker["last_error_code"],
        "last_error_message": worker["last_error_message"],
        "cookies_applied_id": worker["cookies_applied_id"],
        "interval_s": worker["interval_seconds"],
    }


def read_counter(conn: sqlite3.Connection, row: sqlite3.Row, settings: Settings) -> dict[str, Any]:
    """``CounterRead`` (D51): aparencia, canal, ultima leitura, estado do worker, cookies (sem conteudo) e links publicos."""
    counter_id = row["id"]
    channel = conn.execute(
        "SELECT channel_id, channel_name, detected_at FROM channel_info WHERE counter_id = ?", (counter_id,)
    ).fetchone()
    reading = conn.execute("SELECT value, raw, read_at FROM subscriber_reading WHERE counter_id = ?", (counter_id,)).fetchone()
    cookie_row = conn.execute(
        "SELECT id, cookie_count, created_at FROM cookie_sets WHERE counter_id = ? ORDER BY id DESC LIMIT 1", (counter_id,)
    ).fetchone()
    status = _status(conn, counter_id)
    configured = cookie_row is not None
    applied_id = status.get("cookies_applied_id")
    interval = status.get("interval_s") or (MASTER_INTERVAL_S if counter_id == MASTER_COUNTER_ID else settings.user_counter_interval)
    return {
        "id": counter_id,
        "owner_id": row["owner_id"],
        "font": row["font"],
        "text_color": row["text_color"],
        "channel_override": row["channel_override"],  # D54: ID escolhido pelo dono (None = canal detectado pelos cookies)
        "channel": (
            {"id": channel["channel_id"], "name": channel["channel_name"], "detected_at": channel["detected_at"]}
            if channel
            else None
        ),
        "reading": {"value": reading["value"], "raw": reading["raw"], "read_at": reading["read_at"]} if reading else None,
        "state": status.get("state") or "starting",
        "auth_state": status.get("auth_state") or ("unknown" if configured else "no_cookies"),
        "last_success_at": _iso(status.get("last_success_at")),
        "next_read_at": _iso(status.get("next_read_at")),
        "error_code": status.get("last_error_code"),
        "error_message": status.get("last_error_message"),
        "interval_s": interval,
        "cookies": {
            "configured": configured,
            "count": cookie_row["cookie_count"] if cookie_row else 0,
            "uploaded_at": cookie_row["created_at"] if cookie_row else None,
            "applied": bool(configured and applied_id is not None and applied_id == cookie_row["id"]),
        },
        "public": public_links(counter_id),
    }
