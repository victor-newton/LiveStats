"""Painel mestre (D52): ``/api/admin/master/*``. So o mestre; qualquer outro usuario recebe 404 (como se a rota nao existisse).

O dependency do roteador (``require_master``) roda ANTES do CSRF e do corpo: usuario comum nunca descobre que a rota existe.
A senha provisoria so aparece na resposta de criacao e de reset; nunca em log nem em auditoria.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from . import audit, db
from . import counter as counters
from .auth import Ctx, generate_temp_password
from .clock import iso_seconds
from .deps import AppState, ValidationFailed, get_state, parse_json, read_body, require_csrf, require_master, validate_body
from .routes_admin import read_sets
from .schemas import ConfirmBody, UserCreate, UserUpdate
from .users import USER_COLUMNS, purge_owner_data

log = logging.getLogger("livestats.api")

router = APIRouter(prefix="/api/admin/master", dependencies=[Depends(require_master)])

_ID_MAX = 2_147_483_647
_ACTIVITY_MAX = 500
# Estimativa de bytes por linha (so uma ordem de grandeza para o painel; nao e medicao).
_BYTES_SET, _BYTES_LIVE, _BYTES_SAMPLE, _BYTES_COOKIE = 160, 400, 24, 400


def _iso(epoch: int | None) -> str | None:
    return iso_seconds(epoch) if epoch is not None else None


# ---------------------------------------------------------------------------- uso por usuario
def _reads_per_min(pairs: int, state: AppState) -> float:
    return round(pairs * 60 / state.settings.live_interval, 1)


def _usage(conn: sqlite3.Connection, user: sqlite3.Row, state: AppState) -> dict[str, Any]:
    uid = user["id"]
    sets = conn.execute("SELECT count(*) FROM sets WHERE owner_id = ?", (uid,)).fetchone()[0]
    lives = conn.execute("SELECT count(*) FROM lives l JOIN sets s ON s.id = l.set_id WHERE s.owner_id = ?", (uid,)).fetchone()[0]
    pairs = conn.execute(
        "SELECT count(*) FROM (SELECT DISTINCT l.platform, l.ref FROM lives l JOIN sets s ON s.id = l.set_id WHERE s.owner_id = ?)",
        (uid,),
    ).fetchone()[0]
    live_samples = conn.execute(
        "SELECT count(*) FROM live_samples WHERE live_id IN (SELECT l.id FROM lives l JOIN sets s ON s.id = l.set_id WHERE s.owner_id = ?)",
        (uid,),
    ).fetchone()[0]
    counter = counters.counter_of_owner(conn, uid)
    sub_samples = cookie_cost = 0
    counter_state: str | None = None
    loads = 0
    if counter is not None:
        cid = counter["id"]
        sub_samples = conn.execute("SELECT count(*) FROM subscriber_samples WHERE counter_id = ?", (cid,)).fetchone()[0]
        cookie_cost = conn.execute("SELECT COALESCE(SUM(cookie_count), 0) FROM cookie_sets WHERE counter_id = ?", (cid,)).fetchone()[0] * _BYTES_COOKIE
        status = conn.execute("SELECT state, loads_last_hour FROM counter_status WHERE counter_id = ?", (cid,)).fetchone()
        if not user["has_counter"]:
            counter_state = "paused"
        elif status is not None:
            counter_state = status["state"]
        loads = status["loads_last_hour"] if status is not None else 0
    samples = live_samples + sub_samples
    return {
        "sets": sets,
        "lives": lives,
        "platform_pairs": pairs,
        "reads_per_min": _reads_per_min(pairs, state),
        "samples": samples,
        "counter_state": counter_state,
        "counter_loads_per_hour": loads,
        "approx_bytes": sets * _BYTES_SET + lives * _BYTES_LIVE + samples * _BYTES_SAMPLE + cookie_cost,
    }


def _user_item(conn: sqlite3.Connection, row: sqlite3.Row, must_change: bool, state: AppState) -> dict[str, Any]:
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "role": row["role"],
        "status": row["status"],
        "has_counter": bool(row["has_counter"]),
        "max_sets": row["max_sets"],
        "max_lives": row["max_lives"],
        "must_change_password": must_change,
        "created_at": _iso(row["created_at"]),
        "last_login_at": _iso(row["last_login_at"]),
        "last_seen_at": _iso(row["last_seen_at"]),
        "usage": _usage(conn, row, state),
    }


def _row(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = ?", (user_id,)).fetchone()  # noqa: S608 (constante)
    if row is None:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    return row


def _field_error(field: str, message: str, kind: str = "value_error") -> ValidationFailed:
    return ValidationFailed([{"loc": ["body", field], "msg": message, "type": kind}])


def _must_change(state: AppState, user_id: int) -> bool:
    return state.auth.must_change_map().get(user_id, False)


# ---------------------------------------------------------------------------- usuarios
@router.get("/users")
def list_users(state: AppState = Depends(get_state)) -> dict[str, Any]:
    flags = state.auth.must_change_map()
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        rows = conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY id").fetchall()  # noqa: S608 (constante)
        return {"users": [_user_item(conn, row, flags.get(row["id"], False), state) for row in rows]}


@router.post("/users", status_code=201)
def create_user(
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(UserCreate, parse_json(raw))
    now = int(state.clock.now())
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            if conn.execute("SELECT 1 FROM users WHERE username = ?", (body.username,)).fetchone() is not None:
                raise HTTPException(status_code=409, detail="Já existe um usuário com esse nome")
            cur = conn.execute(
                "INSERT INTO users (username, display_name, role, status, has_counter, max_sets, max_lives, created_at) "
                "VALUES (?, ?, 'user', 'active', ?, ?, ?, ?)",
                (body.username, body.display_name, 1 if body.has_counter else 0, body.max_sets, body.max_lives, now),
            )
            new_id = int(cur.lastrowid)
            conn.execute("INSERT INTO counters (owner_id, created_at) VALUES (?, ?)", (new_id, now))  # nasce vazio, sem cookies (D51)
            audit.record(conn, now, ctx.user.id, ctx.user.username, "user_created", body.username)
        temp_password = generate_temp_password()
        try:
            state.auth.set_password(new_id, temp_password, must_change=True)
        except BaseException:
            with db.transaction(conn, immediate=True):  # sem credencial a conta nao pode existir
                conn.execute("DELETE FROM users WHERE id = ?", (new_id,))
            raise
        created = _user_item(conn, _row(conn, new_id), True, state)
    return {"user": created, "temp_password": temp_password}


@router.put("/users/{user_id}")
def update_user(
    user_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    body = validate_body(UserUpdate, parse_json(raw))
    if user_id == ctx.user.id and body.status == "suspended":
        raise _field_error("status", "O mestre não pode suspender a si mesmo")
    sent = [n for n in ("display_name", "status", "max_sets", "max_lives", "has_counter") if n in body.model_fields_set]
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        with db.transaction(conn, immediate=True):
            before = _row(conn, user_id)
            values = [(1 if body.has_counter else 0) if n == "has_counter" else getattr(body, n) for n in sent]
            conn.execute(f"UPDATE users SET {', '.join(f'{n} = ?' for n in sent)} WHERE id = ?", (*values, user_id))  # noqa: S608 (colunas fixas)
            now = state.clock.now()
            if "status" in sent and body.status != before["status"]:
                action = "user_suspended" if body.status == "suspended" else "user_reactivated"
                audit.record(conn, now, ctx.user.id, ctx.user.username, action, before["username"])
            others = [n for n in sent if n != "status"]
            if others:
                audit.record(conn, now, ctx.user.id, ctx.user.username, "user_updated", before["username"], ", ".join(others))
        if "status" in sent and body.status == "suspended":
            state.auth.drop_sessions(user_id)  # alem da checagem a cada requisicao: a sessao some do banco
        updated = _user_item(conn, _row(conn, user_id), _must_change(state, user_id), state)
    return {"ok": True, "user": updated}


@router.post("/users/{user_id}/reset-password")
def reset_password(
    user_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        target = _row(conn, user_id)
        if user_id == ctx.user.id:
            raise ValidationFailed([{"loc": ["path", "user_id"], "msg": "Para trocar a própria senha use a tela Conta", "type": "value_error"}])
        temp_password = generate_temp_password()
        state.auth.set_password(user_id, temp_password, must_change=True)
        state.auth.drop_sessions(user_id)
        audit.record_now(conn, state.clock.now(), ctx.user.id, ctx.user.username, "password_reset", target["username"])
    return {"temp_password": temp_password}


def _confirm(raw: bytes, username: str) -> None:
    body = validate_body(ConfirmBody, parse_json(raw))
    if body.confirm != username:
        raise _field_error("confirm", "Digite o nome de usuário para confirmar", "confirm_mismatch")


@router.post("/users/{user_id}/purge")
def purge_user(
    user_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Apaga tudo que o usuario criou (conjuntos, lives, leituras, amostras, cookies e canal do contador) e mantem a conta."""
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        target = _row(conn, user_id)
        _confirm(raw, target["username"])
        with db.transaction(conn, immediate=True):
            purge_owner_data(conn, user_id)  # a mesma limpeza do autoatendimento (POST /api/admin/account/clear-data)
            audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "user_purged", target["username"])
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int = Path(ge=1, le=_ID_MAX),
    ctx: Ctx = Depends(require_csrf),
    raw: bytes = Depends(read_body),
    state: AppState = Depends(get_state),
) -> dict[str, Any]:
    """Apaga a conta e tudo dele (CASCADE: conjuntos, lives, leituras, amostras, contador e cookies) e as credenciais/sessoes."""
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        target = _row(conn, user_id)
        if user_id == ctx.user.id:
            raise ValidationFailed([{"loc": ["path", "user_id"], "msg": "O mestre não pode apagar a si mesmo", "type": "value_error"}])
        _confirm(raw, target["username"])
        with db.transaction(conn, immediate=True):
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            audit.record(conn, state.clock.now(), ctx.user.id, ctx.user.username, "user_deleted", target["username"])
    state.auth.delete_credentials(user_id)
    return {"ok": True}


@router.get("/users/{user_id}/data")
def user_data(user_id: int = Path(ge=1, le=_ID_MAX), state: AppState = Depends(get_state)) -> dict[str, Any]:
    """Leitura dos dados do usuario (a edicao usa as rotas normais de conjunto/live com o id; o mestre alcanca qualquer dono)."""
    must = _must_change(state, user_id)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        target = _row(conn, user_id)
        counter = counters.counter_of_owner(conn, user_id)
        return {
            "user": _user_item(conn, target, must, state),
            "sets": read_sets(conn, owner=user_id),
            "counter": counters.read_counter(conn, counter, state.settings) if counter is not None else None,
        }


# ---------------------------------------------------------------------------- uso geral e atividade
@router.get("/usage")
def get_usage(state: AppState = Depends(get_state)) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        users = conn.execute("SELECT count(*) FROM users").fetchone()[0]
        sets = conn.execute("SELECT count(*) FROM sets").fetchone()[0]
        lives = conn.execute("SELECT count(*) FROM lives").fetchone()[0]
        pairs = conn.execute("SELECT count(*) FROM (SELECT DISTINCT platform, ref FROM lives)").fetchone()[0]
        # contadores ativos no pool de usuarios: conta ativa, contador habilitado e cookies enviados (o do mestre tem pagina propria)
        active = conn.execute(
            "SELECT count(*) FROM users u JOIN counters c ON c.owner_id = u.id "
            "WHERE u.role = 'user' AND u.status = 'active' AND u.has_counter = 1 "
            "AND EXISTS (SELECT 1 FROM cookie_sets k WHERE k.counter_id = c.id)"
        ).fetchone()[0]
    path = state.settings.db_path
    db_bytes = sum(os.path.getsize(p) for p in (path, path + "-wal") if os.path.exists(p))
    return {
        "users": users,
        "sets": sets,
        "lives": lives,
        "platform_pairs": pairs,
        "reads_per_min": _reads_per_min(pairs, state),
        "db_bytes": db_bytes,
        "counters_active": active,
        "user_pool": {"size": state.settings.user_pool_size, "busy": None},  # busy so existiria com o worker informando
        "limits": {"max_sets_total": state.settings.max_sets_total, "max_lives_total": state.settings.max_lives_total},
    }


@router.get("/activity")
def get_activity(
    limit: int = Query(default=100, ge=1, le=_ACTIVITY_MAX), state: AppState = Depends(get_state)
) -> dict[str, Any]:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        return {"events": audit.list_events(conn, limit)}
