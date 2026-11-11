"""Rotas de admin (``/api/admin/*``). Somente a porta 8081 do Nginx as encaminha.

Todo caminho sob ``/api/admin/`` passa pela dependencia ``require_session`` do roteador, inclusive a rota
pega-tudo no fim (D30): sem sessao valida -> 401; com sessao e rota inexistente -> 404.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import db
from . import audit
from . import chat as chats
from . import counter as counters
from .auth import ABSOLUTE_TTL, MIN_PASSWORD, SESSION_COOKIE, Ctx, InvalidCredentials, LoginBlocked
from .clock import iso_seconds
from .cookies import CookieBadRequest, CookieInvalid, normalize_cookies, store_cookie_set
from .deps import (
    ApiError,
    AppState,
    ValidationFailed,
    client_ip,
    get_state,
    parse_json,
    read_body,
    require_csrf,
    require_master_only,
    require_session,
    validate_body,
)
from . import history, memory
from .fonts import DEFAULT_FONT, FONTS
from .platforms import detect
from .users import purge_owner_data
from .schemas import ChatWrite, ConfirmBody, CounterWrite, LiveCreate, LiveUpdate, LoginRequest, MeUpdate, PasswordChange, SetCreate, SetUpdate

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
        session_id, session, user = state.auth.login(
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
            "username": user.username,
            "user": user.public(),
            "csrf_token": session.csrf_token,
            "expires_at": iso_seconds(session.expires_at),
        }
    )
    response.headers.append("set-cookie", _session_cookie(session_id, ABSOLUTE_TTL))
    return response


@router.post("/logout")
def logout(ctx: Ctx = Depends(require_csrf), state: AppState = Depends(get_state)) -> JSONResponse:
    state.auth.logout(ctx.session)
    response = JSONResponse({"ok": True})
    response.headers.append("set-cookie", _session_cookie("", 0))
    return response


@router.get("/session")
def get_session(ctx: Ctx = Depends(require_session), state: AppState = Depends(get_state)) -> dict[str, Any]:
    return {
        "authenticated": True,
        "username": ctx.user.username,
        "user": ctx.user.public(),
        "csrf_token": ctx.session.csrf_token,
        "expires_at": iso_seconds(ctx.session.expires_at),
        "idle_expires_at": iso_seconds(ctx.session.idle_expires_at),
        "public_base_url": state.settings.public_base_url,
        "public_admin_url": state.settings.public_admin_url,
    }


def _field_error(field: str, message: str, kind: str = "value_error") -> ValidationFailed:
    return ValidationFailed([{"loc": ["body", field], "msg": message, "type": kind}])


@router.post("/me/password", status_code=204)
def post_my_password(
    request: Request,
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> Response:
    body = validate_body(PasswordChange, parse_json(raw))
    if len(body.new) < MIN_PASSWORD:
        raise _field_error("new", f"A nova senha deve ter ao menos {MIN_PASSWORD} caracteres", "too_short")
    if body.new.strip().lower() == ctx.user.username:
        raise _field_error("new", "A nova senha não pode ser igual ao nome de usuário", "same_as_username")
    if body.new == body.current:
        raise _field_error("new", "A nova senha deve ser diferente da atual", "same_as_current")
    ip = client_ip(request)
    try:
        state.auth.change_password(ctx, body.current, body.new, ip)
    except LoginBlocked as blocked:
        return JSONResponse(  # type: ignore[return-value]
            {"detail": TOO_MANY}, status_code=429, headers={"Retry-After": str(blocked.retry_after)}
        )
    except InvalidCredentials:
        raise _field_error("current", "Senha atual incorreta", "wrong_password") from None
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        audit.record_now(conn, state.clock.now(), ctx.user.id, ctx.user.username, "password_changed", ctx.user.username)
    return Response(status_code=204)


@router.post("/account/clear-data")
def post_clear_my_data(
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Autoatendimento: apaga SO os dados do usuario logado (conjuntos, lives, contador, cookies, amostras) e mantem a conta.

    O corpo ``{"confirm": "<username>"}`` precisa ser o proprio nome de usuario. Quem e o dono vem da sessao, nunca do corpo.
    """
    body = validate_body(ConfirmBody, parse_json(raw))
    if body.confirm != ctx.user.username:
        raise _field_error("confirm", "Digite o nome de usuário para confirmar", "confirm_mismatch")
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            cleared = purge_owner_data(conn, ctx.user.id)
            audit.record(
                conn, state.clock.now(), ctx.user.id, ctx.user.username, "clear_data", ctx.user.username,
                f"{cleared['sets']} conjuntos, {cleared['lives']} lives",
            )
    log.info("dados da conta limpos (%d conjuntos, %d lives)", cleared["sets"], cleared["lives"])
    return {"ok": True, "cleared": cleared}


@router.put("/me")
def put_me(
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(MeUpdate, parse_json(raw))
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            conn.execute("UPDATE users SET display_name = ? WHERE id = ?", (body.display_name, ctx.user.id))
    return {"ok": True, "user": {**ctx.user.public(), "display_name": body.display_name}}


# ---------------------------------------------------------------------------- conjuntos e lives (D40, D50)
_ID_MAX = 2_147_483_647

_SET_COLUMNS = "s.id, s.name, s.goal, s.color, s.font, s.text_color, s.position, s.owner_id, u.username AS owner_username"
_LIVE_COLUMNS = "l.id, l.set_id, l.url, l.platform, l.ref, l.label, l.goal, l.color, l.font, l.text_color, l.position, l.chat_enabled"
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
        "chat_enabled": bool(row["chat_enabled"]),  # D57
        "display_name": history.display_name(row["platform"], row["ref"], row["label"], title),  # D44: calculado, nunca gravado
        "stats": _stats_object(stats),
    }


def _set_object(row: sqlite3.Row, lives: list[dict[str, Any]], chat_enabled: bool = False) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "goal": row["goal"],
        "color": row["color"],
        "font": row["font"],
        "text_color": row["text_color"],
        "position": row["position"],
        "owner": {"id": row["owner_id"], "username": row["owner_username"]},
        "lives": lives,
        # D57: chave "Chat do conjunto", quantas lives entram no chat e o endereco (relativo) do overlay
        "chat": {
            "enabled": chat_enabled,
            "lives_enabled": sum(1 for live in lives if live["chat_enabled"]),
            "overlay": chats.overlay_path(row["id"]),
        },
    }


def read_sets(conn: sqlite3.Connection, owner: int | None = None, only: int | None = None) -> list[dict[str, Any]]:
    """Conjuntos com as lives. ``owner`` = so os desse dono (``None`` = de todos: so o mestre chega aqui sem filtro)."""
    where, args = [], []
    if owner is not None:
        where.append("s.owner_id = ?")
        args.append(owner)
    if only is not None:
        where.append("s.id = ?")
        args.append(only)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    set_rows = conn.execute(  # noqa: S608 (colunas e filtros fixos)
        f"SELECT {_SET_COLUMNS} FROM sets s JOIN users u ON u.id = s.owner_id {clause} ORDER BY s.owner_id, s.position, s.id", args
    ).fetchall()
    live_rows = conn.execute(  # noqa: S608
        f"SELECT {_LIVE_COLUMNS} FROM lives l JOIN sets s ON s.id = l.set_id {clause} ORDER BY l.position, l.id", args
    ).fetchall()
    stats = {row["live_id"]: row for row in conn.execute(f"SELECT {_STATS_COLUMNS} FROM live_stats").fetchall()}  # noqa: S608
    by_set: dict[int, list[dict[str, Any]]] = {}
    for row in live_rows:
        by_set.setdefault(row["set_id"], []).append(_live_admin(row, stats.get(row["id"])))
    chat_on = {row[0] for row in conn.execute("SELECT set_id FROM set_chat WHERE enabled = 1")}
    return [_set_object(row, by_set.get(row["id"], []), row["id"] in chat_on) for row in set_rows]


def _scope(ctx: Ctx) -> int | None:
    """Dono que o usuario enxerga: o proprio; o mestre acessa qualquer id (``None`` = sem filtro)."""
    return None if ctx.is_master else ctx.user.id


def _one_set(conn: sqlite3.Connection, ctx: Ctx, set_id: int) -> dict[str, Any]:
    found = read_sets(conn, _scope(ctx), set_id)
    if not found:
        raise HTTPException(status_code=404, detail="Conjunto não encontrado")  # id de outro usuario = 404, nunca 403 (D50)
    return found[0]


def _one_live(conn: sqlite3.Connection, ctx: Ctx, live_id: int) -> dict[str, Any]:
    owner = _scope(ctx)
    row = conn.execute(  # noqa: S608 (constantes)
        f"SELECT {_LIVE_COLUMNS} FROM lives l JOIN sets s ON s.id = l.set_id WHERE l.id = ? AND (? IS NULL OR s.owner_id = ?)",
        (live_id, owner, owner),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Live não encontrada")
    stats = conn.execute(f"SELECT {_STATS_COLUMNS} FROM live_stats WHERE live_id = ?", (live_id,)).fetchone()  # noqa: S608
    return _live_admin(row, stats)


def _effective_limits(ctx: Ctx, state: AppState) -> dict[str, int]:
    """Limites mostrados no painel: os do usuario; o mestre nao tem limite proprio (vale so o teto geral)."""
    if ctx.is_master:
        return {"max_sets": state.settings.max_sets_total, "max_lives": state.settings.max_lives_total}
    return {"max_sets": ctx.user.max_sets, "max_lives": ctx.user.max_lives}


@router.get("/sets")
def get_sets(
    ctx: Ctx = Depends(require_session),
    owner: str | None = Query(default=None, max_length=12),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    scope: int | None = ctx.user.id
    if ctx.is_master and owner is not None:  # so o mestre usa ?owner=<id>|all; para os demais o parametro e ignorado
        if owner == "all":
            scope = None
        elif owner.isascii() and owner.isdigit() and 1 <= int(owner) <= _ID_MAX:
            scope = int(owner)
        else:
            raise ValidationFailed([{"loc": ["query", "owner"], "msg": "Use o id de um usuário ou all", "type": "enum"}])
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        sets = read_sets(conn, scope)
    return {"sets": sets, "limits": _effective_limits(ctx, state)}


@router.get("/sets/{set_id}")
def get_set(
    set_id: int = Path(ge=1, le=_ID_MAX), ctx: Ctx = Depends(require_session), state: AppState = Depends(get_state)
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return _one_set(conn, ctx, set_id)


def _range_param(value: str | None) -> int:
    if value is None or value not in {str(r) for r in history.RANGES}:  # comparação exata: "030" ou "5.0" não valem
        raise ValidationFailed([{"loc": ["query", "range"], "msg": "Use 5, 30, 60 ou 120", "type": "enum"}])
    return int(value)


@router.get("/sets/{set_id}/history")
def get_set_history(
    set_id: int = Path(ge=1, le=_ID_MAX),
    range: str | None = Query(default=None, max_length=8),  # noqa: A002 (nome do parametro na URL)
    ctx: Ctx = Depends(require_session),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    range_min = _range_param(range)
    owner = _scope(ctx)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        if conn.execute(
            "SELECT 1 FROM sets WHERE id = ? AND (? IS NULL OR owner_id = ?)", (set_id, owner, owner)
        ).fetchone() is None:
            raise HTTPException(status_code=404, detail="Conjunto não encontrado")
        return history.set_history(conn, set_id, range_min, state.clock.now())


@router.get("/subscribers/history")
def get_subscribers_history(ctx: Ctx = Depends(require_session), state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = _my_counter(conn, ctx)
        return history.subscribers_history(conn, row["id"], state.clock.now())


@router.get("/health/detail", dependencies=[Depends(require_master_only)])
def get_health_detail(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        now = state.clock.now()
        return {**history.health_detail(conn, now), "memory": memory.memory_block(conn, now)}


@router.post("/sets", status_code=201)
def post_set(
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(SetCreate, parse_json(raw))
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            own = conn.execute("SELECT count(*) FROM sets WHERE owner_id = ?", (ctx.user.id,)).fetchone()[0]
            if not ctx.is_master and own >= ctx.user.max_sets:
                raise _limit_error(f"Limite de {ctx.user.max_sets} conjuntos atingido")
            if conn.execute("SELECT count(*) FROM sets").fetchone()[0] >= state.settings.max_sets_total:
                raise _limit_error(f"Limite geral de {state.settings.max_sets_total} conjuntos do sistema atingido")
            cur = conn.execute(
                "INSERT INTO sets (owner_id, name, goal, color, font, text_color, position, created_at) VALUES "
                "(?, ?, ?, COALESCE(?, '#FF0033'), COALESCE(?, 'Roboto'), COALESCE(?, '#FFFFFF'), "
                "(SELECT COALESCE(MAX(position), -1) + 1 FROM sets WHERE owner_id = ?), ?)",
                (ctx.user.id, body.name, body.goal, body.color, body.font, body.text_color, ctx.user.id, iso_seconds(state.clock.now())),
            )
            history.record_event(conn, state.clock.now(), "info", "set_created", f"Conjunto criado: {body.name}")
            audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "set_created", body.name, f"id {cur.lastrowid}")
        created = _one_set(conn, ctx, cur.lastrowid)
    return {"ok": True, "set": created}


def _updates(body: BaseModel, columns: tuple[str, ...]) -> tuple[list[str], list[Any]]:
    """Colunas enviadas no corpo (``model_fields_set``) -> fragmentos ``col = ?`` e valores."""
    sent = [name for name in columns if name in body.model_fields_set]
    return [f"{name} = ?" for name in sent], [getattr(body, name) for name in sent]


@router.put("/sets/{set_id}")
def put_set(
    set_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(SetUpdate, parse_json(raw))
    sets, values = _updates(body, ("name", "goal", "color", "font", "text_color"))
    owner = _scope(ctx)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            cur = conn.execute(  # noqa: S608 (colunas fixas)
                f"UPDATE sets SET {', '.join(sets)} WHERE id = ? AND (? IS NULL OR owner_id = ?)", (*values, set_id, owner, owner)
            )
            if cur.rowcount == 0:
                raise HTTPException(status_code=404, detail="Conjunto não encontrado")
        updated = _one_set(conn, ctx, set_id)
    return {"ok": True, "set": updated}


@router.delete("/sets/{set_id}")
def delete_set(
    set_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    owner = _scope(ctx)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            found = conn.execute(
                "SELECT name FROM sets WHERE id = ? AND (? IS NULL OR owner_id = ?)", (set_id, owner, owner)
            ).fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="Conjunto não encontrado")
            conn.execute("DELETE FROM sets WHERE id = ?", (set_id,))  # as lives, leituras e amostras saem em CASCADE
            history.record_event(conn, state.clock.now(), "info", "set_deleted", f"Conjunto apagado: {found['name']}")
            audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "set_deleted", found["name"], f"id {set_id}")
    return {"ok": True}


def _chat_response(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    return {**chats.read_config(conn, set_id), "public": {"overlay": chats.overlay_path(set_id)}}


def _own_set_name(conn: sqlite3.Connection, ctx: Ctx, set_id: int) -> str:
    owner = _scope(ctx)
    found = conn.execute("SELECT name FROM sets WHERE id = ? AND (? IS NULL OR owner_id = ?)", (set_id, owner, owner)).fetchone()
    if found is None:
        raise HTTPException(status_code=404, detail="Conjunto não encontrado")  # id de outro usuario = 404, nunca 403 (D50)
    return found["name"]


@router.get("/sets/{set_id}/chat")
def get_set_chat(
    set_id: int = Path(ge=1, le=_ID_MAX), ctx: Ctx = Depends(require_session), state: AppState = Depends(get_state)
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        _own_set_name(conn, ctx, set_id)
        return _chat_response(conn, set_id)


@router.put("/sets/{set_id}/chat")
def put_set_chat(
    set_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(ChatWrite, parse_json(raw))
    changes = {name: getattr(body, name) for name in body.model_fields_set}
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            name = _own_set_name(conn, ctx, set_id)
            chats.write_config(conn, set_id, changes, state.clock.now())
            audit.record(  # so os nomes dos campos mudados, nunca a lista de ignorados
                conn, state.clock.now(), ctx.user.id, ctx.user.username, "set_chat_updated", name,
                f"conjunto {set_id}: {', '.join(sorted(changes))}",
            )
        return _chat_response(conn, set_id)


@router.post("/sets/{set_id}/lives", status_code=201)
def post_live(
    set_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(LiveCreate, parse_json(raw))
    platform, ref = detect(body.url)  # type: ignore[misc]  # o validador do modelo ja garantiu que detecta
    owner = _scope(ctx)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            parent = conn.execute(
                "SELECT color, font, text_color, owner_id FROM sets WHERE id = ? AND (? IS NULL OR owner_id = ?)",
                (set_id, owner, owner),
            ).fetchone()
            if parent is None:
                raise HTTPException(status_code=404, detail="Conjunto não encontrado")
            if not ctx.is_master:
                own = conn.execute(
                    "SELECT count(*) FROM lives l JOIN sets s ON s.id = l.set_id WHERE s.owner_id = ?", (ctx.user.id,)
                ).fetchone()[0]
                if own >= ctx.user.max_lives:
                    raise _limit_error(f"Limite de {ctx.user.max_lives} lives atingido")
            if conn.execute("SELECT count(*) FROM lives").fetchone()[0] >= state.settings.max_lives_total:
                raise _limit_error(f"Limite geral de {state.settings.max_lives_total} lives do sistema atingido")
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
            audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "live_created", name, f"id {cur.lastrowid}, conjunto {set_id}")
        created = _one_live(conn, ctx, cur.lastrowid)
    return {"ok": True, "live": created}


@router.put("/lives/{live_id}")
def put_live(
    live_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(LiveUpdate, parse_json(raw))
    columns, values = _updates(body, ("url", "label", "goal", "color", "font", "text_color", "chat_enabled"))
    new_target: tuple[str, str] | None = None
    if "url" in body.model_fields_set:
        new_target = detect(body.url)  # type: ignore[assignment]
        columns += ["platform = ?", "ref = ?"]
        values += list(new_target)  # type: ignore[arg-type]
    owner = _scope(ctx)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            current = conn.execute(
                "SELECT l.platform, l.ref FROM lives l JOIN sets s ON s.id = l.set_id "
                "WHERE l.id = ? AND (? IS NULL OR s.owner_id = ?)",
                (live_id, owner, owner),
            ).fetchone()
            if current is None:
                raise HTTPException(status_code=404, detail="Live não encontrada")
            conn.execute(f"UPDATE lives SET {', '.join(columns)} WHERE id = ?", (*values, live_id))  # noqa: S608 (colunas fixas)
            if new_target is not None and new_target != (current["platform"], current["ref"]):
                conn.execute("DELETE FROM live_stats WHERE live_id = ?", (live_id,))  # leitura da URL antiga
        updated = _one_live(conn, ctx, live_id)
    return {"ok": True, "live": updated}


@router.delete("/lives/{live_id}")
def delete_live(
    live_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    owner = _scope(ctx)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            found = conn.execute(
                "SELECT l.set_id, l.platform, l.ref, l.label FROM lives l JOIN sets s ON s.id = l.set_id "
                "WHERE l.id = ? AND (? IS NULL OR s.owner_id = ?)",
                (live_id, owner, owner),
            ).fetchone()
            if found is None:
                raise HTTPException(status_code=404, detail="Live não encontrada")
            conn.execute("DELETE FROM lives WHERE id = ?", (live_id,))
            db.compact_live_positions(conn, found["set_id"])  # D44: 1..n sem buracos
            name = history.display_name(found["platform"], found["ref"], found["label"], None)
            history.record_event(conn, state.clock.now(), "info", "live_deleted", f"Live apagada: {name}", live_id=live_id)
            audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "live_deleted", name, f"id {live_id}")
    return {"ok": True}


# ---------------------------------------------------------------------------- contador do dono (D51) e canal detectado (D38)
def _my_counter(conn: sqlite3.Connection, ctx: Ctx) -> sqlite3.Row:
    """O contador do usuario logado (cada usuario so enxerga o proprio). ``has_counter = 0`` -> 403 ``counter_disabled``."""
    if not ctx.user.has_counter:
        raise ApiError(403, "O contador de inscritos não está habilitado para esta conta", "counter_disabled")
    row = counters.counter_of_owner(conn, ctx.user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="Contador não encontrado")
    return row


@router.get("/counter")
def get_counter(ctx: Ctx = Depends(require_session), state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return counters.read_counter(conn, _my_counter(conn, ctx), state.settings)


@router.put("/counter")
def put_counter(
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(CounterWrite, parse_json(raw))
    columns, values = _updates(body, ("font", "text_color"))
    if "channel_id" in body.model_fields_set:  # D54: None volta a deteccao automatica
        columns.append("channel_override = ?")
        values.append(body.channel_id)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = _my_counter(conn, ctx)
        with db.transaction(conn, immediate=True):
            conn.execute(f"UPDATE counters SET {', '.join(columns)} WHERE id = ?", (*values, row["id"]))  # noqa: S608 (colunas fixas)
            if "channel_id" in body.model_fields_set and body.channel_id != row["channel_override"]:
                audit.record(
                    conn,
                    state.clock.now(),
                    ctx.user.id,
                    ctx.user.username,
                    "counter_channel_set" if body.channel_id else "counter_channel_cleared",
                    ctx.user.username,
                    body.channel_id or "",
                )
        result = counters.read_counter(conn, _my_counter(conn, ctx), state.settings)
    log.info("contador salvo (%s)", ",".join(sorted(body.model_fields_set)))
    return {"ok": True, **result}


@router.get("/fonts")
def get_fonts() -> dict[str, Any]:
    return {"fonts": list(FONTS), "default": DEFAULT_FONT}


# ---------------------------------------------------------------------------- cookies (do contador do usuario logado)
@router.post("/cookies")
def post_cookies(
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        counter_id = _my_counter(conn, ctx)["id"]  # 403 counter_disabled antes de qualquer processamento do corpo
    payload = parse_json(raw)
    try:
        cookies = normalize_cookies(payload)
    except CookieBadRequest as bad:
        raise HTTPException(status_code=400, detail=bad.detail) from None
    except CookieInvalid as invalid:
        raise ValidationFailed(invalid.errors) from None
    ciphertext = state.sealer.seal(cookies)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        store_cookie_set(conn, counter_id, ciphertext, state.sealer.key_id, len(cookies), ctx.user.username, state.clock.now())
        try:
            with db.transaction(conn, immediate=True):
                history.record_event(conn, state.clock.now(), "info", "cookies_sent", f"Cookies enviados ({len(cookies)})")
                audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "cookies_sent", ctx.user.username, f"{len(cookies)} cookies")
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


@router.get("/workers/status", dependencies=[Depends(require_master_only)])
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
            "SELECT id, cookie_count, created_at, source, renewed_from FROM cookie_sets WHERE counter_id = 1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        reading = conn.execute("SELECT value, raw, read_at FROM subscriber_reading WHERE counter_id = 1").fetchone()

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
