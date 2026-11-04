"""Rotas de admin (``/api/admin/*``). Somente a porta 8081 do Nginx as encaminha.

Todo caminho sob ``/api/admin/`` passa pela dependencia ``require_session`` do roteador, inclusive a rota
pega-tudo no fim (D30): sem sessao valida -> 401; com sessao e rota inexistente -> 404.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db
from .auth import ABSOLUTE_TTL, SESSION_COOKIE, InvalidCredentials, LoginBlocked, SessionData
from .clock import iso_seconds
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
from . import history
from .fonts import DEFAULT_FONT, DEFAULT_TEXT_COLOR, FONTS
from .platforms import detect
from .schemas import LiveCreate, LiveUpdate, LoginRequest, SetCreate, SetUpdate, SettingsWrite

log = logging.getLogger("livestats.api")

TOO_MANY = "Muitas tentativas. Tente novamente em instantes."

# Login: unica rota admin sem sessao previa e sem CSRF (ela cria a sessao).
login_router = APIRouter(prefix="/api/admin")
# Todas as demais exigem sessao valida (checada ANTES do corpo e de qualquer outra validacao).
router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_session)])

_COOKIE_FLAGS = "Path=/; HttpOnly; Secure; SameSite=Strict"


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


# ---------------------------------------------------------------------------- conjuntos e lives (D40)
MAX_SETS = 50
MAX_LIVES = 200
_ID_MAX = 2_147_483_647

_SET_COLUMNS = "id, name, goal, color, font, text_color, position"
_LIVE_COLUMNS = "id, set_id, url, platform, ref, label, goal, color, font, text_color, position"
_STATS_COLUMNS = "live_id, status, title, viewers, likes, updated_at, error_code"


def _limit_error(message: str) -> ValidationFailed:
    return ValidationFailed([{"loc": ["body"], "msg": message, "type": "limit"}])


def _stats_object(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in ("status", "title", "viewers", "likes", "updated_at", "error_code")}


def _live_admin(row: sqlite3.Row, stats: sqlite3.Row | None) -> dict[str, Any]:
    title = stats["title"] if stats is not None else None
    return {
        **{key: row[key] for key in row.keys()},
        "display_name": history.display_name(row["platform"], row["ref"], row["label"], title),  # D44: calculado, nunca gravado
        "stats": _stats_object(stats),
    }


def read_sets(conn: sqlite3.Connection, only: int | None = None) -> list[dict[str, Any]]:
    where, args = ("WHERE id = ?", (only,)) if only is not None else ("", ())
    set_rows = conn.execute(f"SELECT {_SET_COLUMNS} FROM sets {where} ORDER BY position, id", args).fetchall()  # noqa: S608
    live_rows = conn.execute(f"SELECT {_LIVE_COLUMNS} FROM lives ORDER BY position, id").fetchall()  # noqa: S608
    stats = {row["live_id"]: row for row in conn.execute(f"SELECT {_STATS_COLUMNS} FROM live_stats").fetchall()}  # noqa: S608
    by_set: dict[int, list[dict[str, Any]]] = {}
    for row in live_rows:
        by_set.setdefault(row["set_id"], []).append(_live_admin(row, stats.get(row["id"])))
    return [
        {**{key: row[key] for key in row.keys()}, "lives": by_set.get(row["id"], [])}
        for row in set_rows
    ]


def _one_set(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    found = read_sets(conn, set_id)
    if not found:
        raise HTTPException(status_code=404, detail="Conjunto não encontrado")
    return found[0]


def _one_live(conn: sqlite3.Connection, live_id: int) -> dict[str, Any]:
    row = conn.execute(f"SELECT {_LIVE_COLUMNS} FROM lives WHERE id = ?", (live_id,)).fetchone()  # noqa: S608
    if row is None:
        raise HTTPException(status_code=404, detail="Live não encontrada")
    stats = conn.execute(f"SELECT {_STATS_COLUMNS} FROM live_stats WHERE live_id = ?", (live_id,)).fetchone()  # noqa: S608
    return _live_admin(row, stats)


@router.get("/sets")
def get_sets(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        sets = read_sets(conn)
    return {"sets": sets, "limits": {"max_sets": MAX_SETS, "max_lives": MAX_LIVES}}


@router.get("/sets/{set_id}")
def get_set(set_id: int = Path(ge=1, le=_ID_MAX), state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return _one_set(conn, set_id)


def _range_param(value: str | None) -> int:
    if value is None or value not in {str(r) for r in history.RANGES}:  # comparação exata: "030" ou "5.0" não valem
        raise ValidationFailed([{"loc": ["query", "range"], "msg": "Use 5, 30, 60 ou 120", "type": "enum"}])
    return int(value)


@router.get("/sets/{set_id}/history")
def get_set_history(
    set_id: int = Path(ge=1, le=_ID_MAX),
    range: str | None = Query(default=None, max_length=8),  # noqa: A002 (nome do parametro na URL)
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    range_min = _range_param(range)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        if conn.execute("SELECT 1 FROM sets WHERE id = ?", (set_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Conjunto não encontrado")
        return history.set_history(conn, set_id, range_min, state.clock.now())


@router.get("/subscribers/history")
def get_subscribers_history(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return history.subscribers_history(conn, state.clock.now())


@router.get("/health/detail")
def get_health_detail(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return history.health_detail(conn, state.clock.now())


@router.post("/sets", status_code=201)
def post_set(
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(SetCreate, parse_json(raw))
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            if conn.execute("SELECT count(*) FROM sets").fetchone()[0] >= MAX_SETS:
                raise _limit_error(f"Limite de {MAX_SETS} conjuntos atingido")
            cur = conn.execute(
                "INSERT INTO sets (name, goal, color, font, text_color, position, created_at) VALUES "
                "(?, ?, COALESCE(?, '#FF0033'), COALESCE(?, 'Roboto'), COALESCE(?, '#FFFFFF'), "
                "(SELECT COALESCE(MAX(position), -1) + 1 FROM sets), ?)",
                (body.name, body.goal, body.color, body.font, body.text_color, iso_seconds(state.clock.now())),
            )
            history.record_event(conn, state.clock.now(), "info", "set_created", f"Conjunto criado: {body.name}")
        created = _one_set(conn, cur.lastrowid)
    return {"ok": True, "set": created}


def _updates(body: BaseModel, columns: tuple[str, ...]) -> tuple[list[str], list[Any]]:
    """Colunas enviadas no corpo (``model_fields_set``) -> fragmentos ``col = ?`` e valores."""
    sent = [name for name in columns if name in body.model_fields_set]
    return [f"{name} = ?" for name in sent], [getattr(body, name) for name in sent]


@router.put("/sets/{set_id}")
def put_set(
    set_id: int = Path(ge=1, le=_ID_MAX),
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(SetUpdate, parse_json(raw))
    sets, values = _updates(body, ("name", "goal", "color", "font", "text_color"))
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            cur = conn.execute(f"UPDATE sets SET {', '.join(sets)} WHERE id = ?", (*values, set_id))  # noqa: S608 (colunas fixas)
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Conjunto não encontrado")
        updated = _one_set(conn, set_id)
    return {"ok": True, "set": updated}


@router.delete("/sets/{set_id}")
def delete_set(
    set_id: int = Path(ge=1, le=_ID_MAX),
    session: SessionData = Depends(require_csrf),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            found = conn.execute("SELECT name FROM sets WHERE id = ?", (set_id,)).fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="Conjunto não encontrado")
            conn.execute("DELETE FROM sets WHERE id = ?", (set_id,))  # as lives, leituras e amostras saem em CASCADE
            history.record_event(conn, state.clock.now(), "info", "set_deleted", f"Conjunto apagado: {found['name']}")
    return {"ok": True}


@router.post("/sets/{set_id}/lives", status_code=201)
def post_live(
    set_id: int = Path(ge=1, le=_ID_MAX),
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(LiveCreate, parse_json(raw))
    platform, ref = detect(body.url)  # type: ignore[misc]  # o validador do modelo ja garantiu que detecta
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            parent = conn.execute("SELECT color, font, text_color FROM sets WHERE id = ?", (set_id,)).fetchone()
            if parent is None:
                raise HTTPException(status_code=404, detail="Conjunto não encontrado")
            if conn.execute("SELECT count(*) FROM lives").fetchone()[0] >= MAX_LIVES:
                raise _limit_error(f"Limite de {MAX_LIVES} lives atingido")
            # Sem fonte/cor do texto informadas, a live nasce com as do conjunto; sem cor, pega o proximo tom livre da
            # paleta da plataforma (D44). A posicao e 1..n dentro do conjunto.
            used = [r[0] for r in conn.execute("SELECT color FROM lives WHERE set_id = ?", (set_id,))]
            color = body.color or history.next_color(platform, used)
            cur = conn.execute(
                "INSERT INTO lives (set_id, url, platform, ref, label, goal, color, font, text_color, position, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, (SELECT COALESCE(MAX(position), 0) + 1 FROM lives WHERE set_id = ?), ?)",
                (
                    set_id, body.url, platform, ref, body.label or "", body.goal,
                    color, body.font or parent["font"], body.text_color or parent["text_color"],
                    set_id, iso_seconds(state.clock.now()),
                ),
            )
            name = history.display_name(platform, ref, body.label or "", None)  # type: ignore[arg-type]
            history.record_event(
                conn, state.clock.now(), "info", "live_created", f"Live adicionada: {name}", live_id=cur.lastrowid
            )
        created = _one_live(conn, cur.lastrowid)
    return {"ok": True, "live": created}


@router.put("/lives/{live_id}")
def put_live(
    live_id: int = Path(ge=1, le=_ID_MAX),
    session: SessionData = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(LiveUpdate, parse_json(raw))
    columns, values = _updates(body, ("url", "label", "goal", "color", "font", "text_color"))
    new_target: tuple[str, str] | None = None
    if "url" in body.model_fields_set:
        new_target = detect(body.url)  # type: ignore[assignment]
        columns += ["platform = ?", "ref = ?"]
        values += list(new_target)  # type: ignore[arg-type]
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            current = conn.execute("SELECT platform, ref FROM lives WHERE id = ?", (live_id,)).fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="Live não encontrada")
            conn.execute(f"UPDATE lives SET {', '.join(columns)} WHERE id = ?", (*values, live_id))  # noqa: S608 (colunas fixas)
            if new_target is not None and new_target != (current["platform"], current["ref"]):
                conn.execute("DELETE FROM live_stats WHERE live_id = ?", (live_id,))  # leitura da URL antiga
        updated = _one_live(conn, live_id)
    return {"ok": True, "live": updated}


@router.delete("/lives/{live_id}")
def delete_live(
    live_id: int = Path(ge=1, le=_ID_MAX),
    session: SessionData = Depends(require_csrf),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            found = conn.execute("SELECT set_id, platform, ref, label FROM lives WHERE id = ?", (live_id,)).fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="Live não encontrada")
            conn.execute("DELETE FROM lives WHERE id = ?", (live_id,))
            db.compact_live_positions(conn, found["set_id"])  # D44: 1..n sem buracos
            name = history.display_name(found["platform"], found["ref"], found["label"], None)
            history.record_event(conn, state.clock.now(), "info", "live_deleted", f"Live apagada: {name}", live_id=live_id)
    return {"ok": True}


# ---------------------------------------------------------------------------- configuracoes (D35) e canal detectado (D38)
def read_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    stored = {row["key"]: row["value"] for row in conn.execute("SELECT key, value FROM settings")}
    channel = conn.execute("SELECT channel_id, channel_name, detected_at FROM channel_info WHERE id = 1").fetchone()
    return {
        "channel": (
            {"id": channel["channel_id"], "name": channel["channel_name"], "detected_at": channel["detected_at"]}
            if channel
            else None
        ),
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
    changed = [key for key in ("subs_font", "subs_text_color") if getattr(body, key) is not None]
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
        try:
            with db.transaction(conn, immediate=True):
                history.record_event(conn, state.clock.now(), "info", "cookies_sent", f"Cookies enviados ({len(cookies)})")
        except sqlite3.Error as exc:  # o evento e informativo: os cookies ja foram salvos
            log.warning("nao foi possivel gravar o evento de cookies (%s)", type(exc).__name__)
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
            "SELECT id, cookie_count, created_at, source, renewed_from FROM cookie_sets ORDER BY id DESC LIMIT 1"
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
            "source": cookie_row["source"] if cookie_row else None,
            "created_at": cookie_row["created_at"] if cookie_row else None,
            "renewed_from": cookie_row["renewed_from"] if cookie_row else None,
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
