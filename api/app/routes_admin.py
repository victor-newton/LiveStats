"""Rotas de admin (``/api/admin/*``). Somente a porta 8081 do Nginx as encaminha.

Todo caminho sob ``/api/admin/`` passa pela dependencia ``require_session`` do roteador, inclusive a rota
pega-tudo no fim (D30): sem sessao valida -> 401; com sessao e rota inexistente -> 404.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from . import db
from .auth import ABSOLUTE_TTL, SESSION_COOKIE, InvalidCredentials, LoginBlocked, SessionData
from .clock import iso_seconds, legacy_timestamp
from .cookies import CookieBadRequest, CookieInvalid, normalize_cookies, store_cookie_set
from .deps import (
    AppState,
    ValidationFailed,
    client_ip,
    get_state,
    parse_json,
    read_body,
    require_csrf,
    require_session,
    validate_body,
)
from .legacy import live_object
from .fonts import DEFAULT_FONT, DEFAULT_TEXT_COLOR, FONTS
from .schemas import LoginRequest, SettingsWrite, SlotsWrite

log = logging.getLogger("livestats.api")

TOO_MANY = "Muitas tentativas. Tente novamente em instantes."

# Login: unica rota admin sem sessao previa e sem CSRF (ela cria a sessao).
login_router = APIRouter(prefix="/api/admin")
# Todas as demais exigem sessao valida (checada ANTES do corpo e de qualquer outra validacao).
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_session)])

_COOKIE_FLAGS = "Path=/; HttpOnly; Secure; SameSite=Strict"
_LIVE_COLUMNS = "slot, active, video_id, title, status, views, likes, views_formatted, likes_formatted, updated_at"


def _session_cookie(value: str, max_age: int) -> str:
    return f"{SESSION_COOKIE}={value}; Max-Age={max_age}; {_COOKIE_FLAGS}"


# ---------------------------------------------------------------------------- login / logout / sessao
@login_router.post("/login")
def login(
    request: Request,
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> JSONResponse:
    body = validate_body(LoginRequest, parse_json(raw))
    ip = client_ip(request)
    try:
        session_id, session = state.auth.login(
            body.username, body.password, ip, request.cookies.get(SESSION_COOKIE)
        )
    except LoginBlocked as blocked:
        log.warning("login bloqueado por excesso de falhas (ip=%s)", ip)
        return JSONResponse(
            {"detail": TOO_MANY}, status_code=429, headers={"Retry-After": str(blocked.retry_after)}
        )
    except InvalidCredentials:
        log.warning("login recusado (ip=%s)", ip)
        raise HTTPException(status_code=401, detail="Credenciais inválidas") from None
    log.info("login ok (ip=%s)", ip)
    response = JSONResponse(
        {
            "ok": True,
            "username": session.username,
            "csrf_token": session.csrf_token,
            "expires_at": iso_seconds(session.expires_at),
        }
    )
    response.headers.append("set-cookie", _session_cookie(session_id, ABSOLUTE_TTL))
    return response


@router.post("/logout")
def logout(session: SessionData = Depends(require_csrf), state: AppState = Depends(get_state)) -> JSONResponse:
    state.auth.logout(session)
    response = JSONResponse({"ok": True})
    response.headers.append("set-cookie", _session_cookie("", 0))
    return response


@router.get("/session")
def get_session(session: SessionData = Depends(require_session), state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {
        "authenticated": True,
        "username": session.username,
        "csrf_token": session.csrf_token,
        "expires_at": iso_seconds(session.expires_at),
        "idle_expires_at": iso_seconds(session.idle_expires_at),
        "public_base_url": state.settings.public_base_url,
    }


# ---------------------------------------------------------------------------- slots
@router.get("/slots")
def get_slots(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        slot_rows = conn.execute("SELECT slot, url, goal, color, font, text_color, updated_at FROM slots ORDER BY slot").fetchall()
        stats = {
            row["slot"]: live_object(row)
            for row in conn.execute(f"SELECT {_LIVE_COLUMNS} FROM live_stats").fetchall()  # noqa: S608
        }
    saved = [row["updated_at"] for row in slot_rows if row["updated_at"]]
    return {
        "slots": [
            {
                "slot": row["slot"],
                "url": row["url"],
                "goal": row["goal"],
                "color": row["color"],
                "font": row["font"],
                "text_color": row["text_color"],
                "stats": stats.get(row["slot"]),
            }
            for row in slot_rows
        ],
        "saved_at": max(saved) if saved else None,
    }


@router.put("/slots")
def put_slots(
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(SlotsWrite, parse_json(raw))
    saved_at = legacy_timestamp(state.clock.now())
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            for number, item in enumerate(body.slots, start=1):
                conn.execute(
                    "UPDATE slots SET url = ?, goal = ?, color = ?, font = COALESCE(?, font), "
                    "text_color = COALESCE(?, text_color), updated_at = ? WHERE slot = ?",
                    (item.url, item.goal, item.color, item.font, item.text_color, saved_at, number),
                )
        saved = conn.execute("SELECT url, goal, color, font, text_color FROM slots ORDER BY slot").fetchall()
    return {
        "ok": True,
        "slots": [{k: row[k] for k in ("url", "goal", "color", "font", "text_color")} for row in saved],
        "saved_at": saved_at,
    }


# ---------------------------------------------------------------------------- configuracoes (D35)
def read_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    return {
        "channel_id": stored.get("channel_id"),
        "subs_font": stored.get("subs_font", DEFAULT_FONT),
        "subs_text_color": stored.get("subs_text_color", DEFAULT_TEXT_COLOR),
    }


@router.get("/settings")
def get_settings(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return read_settings(conn)


@router.put("/settings")
def put_settings(
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(SettingsWrite, parse_json(raw))
    changed = [key for key in ("channel_id", "subs_font", "subs_text_color") if getattr(body, key) is not None]
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            for key in changed:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, getattr(body, key)),
                )
        result = read_settings(conn)
    log.info("configuracoes salvas (%s)", ",".join(changed))
    return {"ok": True, **result}


@router.get("/fonts")
def get_fonts() -> dict[str, Any]:
    return {"fonts": list(FONTS), "default": DEFAULT_FONT}


# ---------------------------------------------------------------------------- cookies
@router.post("/cookies")
def post_cookies(
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    payload = parse_json(raw)
    try:
        cookies = normalize_cookies(payload)
    except CookieBadRequest as bad:
        raise HTTPException(status_code=400, detail=bad.detail) from None
    except CookieInvalid as invalid:
        raise ValidationFailed(invalid.errors) from None
    ciphertext = state.sealer.seal(cookies)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        store_cookie_set(conn, ciphertext, state.sealer.key_id, len(cookies), session.username, state.clock.now())
    count = len(cookies)
    return {
        "ok": True,
        "message": f"{count} cookies salvos com sucesso! O scraper vai recarregar automaticamente em alguns segundos.",
        "cookies_count": count,
    }


# ---------------------------------------------------------------------------- status dos workers
def _iso(epoch: int | None) -> str | None:
    return iso_seconds(epoch) if epoch is not None else None


def _worker_state(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {
            "state": "unknown",
            "last_heartbeat_at": None,
            "last_success_at": None,
            "consecutive_failures": 0,
            "interval_seconds": None,
            "next_attempt_at": None,
            "last_error_code": None,
            "last_error_message": None,
        }
    return {
        "state": row["state"],
        "last_heartbeat_at": _iso(row["last_heartbeat_at"]),
        "last_success_at": _iso(row["last_success_at"]),
        "consecutive_failures": row["consecutive_failures"],
        "interval_seconds": row["interval_seconds"],
        "next_attempt_at": _iso(row["next_attempt_at"]),
        "last_error_code": row["last_error_code"],
        "last_error_message": row["last_error_message"],
    }


@router.get("/workers/status")
def workers_status(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        workers = {
            row["worker"]: row
            for row in conn.execute(
                "SELECT worker, state, last_heartbeat_at, last_success_at, consecutive_failures, interval_seconds, "
                "next_attempt_at, last_error_code, last_error_message, auth_state, cookies_applied_id FROM worker_status"
            ).fetchall()
        }
        cookie_row = conn.execute(
            "SELECT id, cookie_count, created_at FROM cookie_sets ORDER BY id DESC LIMIT 1"
        ).fetchone()
        reading = conn.execute("SELECT value, raw, read_at FROM subscriber_reading WHERE id = 1").fetchone()

    inscritos = _worker_state(workers.get("inscritos"))
    inscritos["auth_state"] = (workers["inscritos"]["auth_state"] if "inscritos" in workers else None) or "unknown"
    applied_id = workers["inscritos"]["cookies_applied_id"] if "inscritos" in workers else None
    return {
        "server_time": iso_seconds(state.clock.now()),
        "workers": {"lives": _worker_state(workers.get("lives")), "inscritos": inscritos},
        "cookies": {
            "configured": cookie_row is not None,
            "count": cookie_row["cookie_count"] if cookie_row else 0,
            "uploaded_at": cookie_row["created_at"] if cookie_row else None,
            "applied": bool(cookie_row is not None and applied_id is not None and applied_id == cookie_row["id"]),
        },
        "subscribers": (
            {"inscritos": reading["value"], "raw": reading["raw"], "atualizado_em": reading["read_at"]}
            if reading
            else None
        ),
    }


# ---------------------------------------------------------------------------- pega-tudo (D30)
@router.api_route("", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def unknown_admin_route() -> JSONResponse:
    # Chegou aqui = a dependencia do roteador ja validou a sessao. Sem sessao nunca se chega (401).
    return JSONResponse({"detail": "Not Found"}, status_code=404)
