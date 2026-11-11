"""Endpoints publicos (somente leitura). Sem autenticacao e SEM CORS: o CORS dos GET publicos de dados e
emitido pelo Nginx (D10); a api nunca envia ``Access-Control-*``.

v2 (D41): ``/api/public/lives/{id}`` e ``/api/public/sets/{id}`` (mais os atalhos ``/lives/{id}.json`` e
``/conjuntos/{id}.json``), ``/api/public/subscribers`` e ``/inscritos.json``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response

from . import chat as chats
from . import db
from .clock import timestamp_publico
from .deps import AppState, get_state
from .formato_publico import dumps, subscribers_object

router = APIRouter()

NOT_READY_LIVE = "Dados ainda não disponíveis"
NOT_READY_SUBSCRIBERS = "Dados ainda não disponíveis. Scraper inicializando..."
LIVE_NOT_FOUND = "Live não encontrada"
SET_NOT_FOUND = "Conjunto não encontrado"
COUNTER_NOT_FOUND = "Contador não encontrado"
_ID_MAX = 2_147_483_647


def _json(body: bytes) -> Response:
    return Response(content=body, media_type="application/json", headers={"Cache-Control": "no-store"})


def _valid_id(value: int) -> bool:
    return 1 <= value <= _ID_MAX


def _progress(likes: int | None, goal: int | None) -> float | None:
    """likes/meta limitado a 1; ``None`` sem meta ou sem likes."""
    if goal is None or likes is None:
        return None
    return min(likes / goal, 1.0)


def _live_public(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "set_id": row["set_id"],
        "platform": row["platform"],
        "label": row["label"],
        "title": row["title"],
        "status": row["status"],
        "viewers": row["viewers"],
        "likes": row["likes"],
        "goal": row["goal"],
        "progress": _progress(row["likes"], row["goal"]),
        "color": row["color"],
        "font": row["font"],
        "text_color": row["text_color"],
        "updated_at": row["updated_at"],
    }


# LEFT JOIN: a live existe antes da primeira leitura do worker (status/viewers/likes nulos).
_LIVE_SELECT = (
    "SELECT l.id, l.set_id, l.platform, l.label, l.goal, l.color, l.font, l.text_color, "
    "COALESCE(s.title, '') AS title, s.status, s.viewers, s.likes, s.updated_at "
    "FROM lives l LEFT JOIN live_stats s ON s.live_id = l.id"
)


MASTER_COUNTER_ID = 1  # /inscritos.json e /api/public/subscribers sao o contador do mestre


def _subscribers_object_or_error(state: AppState, counter_id: int = MASTER_COUNTER_ID) -> tuple[dict, sqlite3.Row]:
    """(objeto de ``/inscritos.json``, linha do contador). 404 se o contador nao existe; 503 se ainda nao ha leitura."""
    if not _valid_id(counter_id):
        raise HTTPException(status_code=404, detail=COUNTER_NOT_FOUND)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        counter = conn.execute("SELECT id, font, text_color FROM counters WHERE id = ?", (counter_id,)).fetchone()
        if counter is None:
            raise HTTPException(status_code=404, detail=COUNTER_NOT_FOUND)
        row = conn.execute("SELECT value, raw, read_at FROM subscriber_reading WHERE counter_id = ?", (counter_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail=NOT_READY_SUBSCRIBERS)
    return subscribers_object(row), counter


def _subscribers_body(state: AppState, counter_id: int = MASTER_COUNTER_ID) -> bytes:
    return dumps(_subscribers_object_or_error(state, counter_id)[0])


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> Response:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = conn.execute("SELECT value, read_at FROM subscriber_reading WHERE counter_id = 1").fetchone()
    with db.connection(db.open_admin, state.settings.admin_db_path) as admin_conn:
        admin_conn.execute("SELECT key FROM schema_meta LIMIT 1").fetchall()
    body = {
        "status": "ok",
        "server_time": timestamp_publico(state.clock.now()),
        "data_file_exists": row is not None,
        "inscritos": row["value"] if row else None,
        "last_update": row["read_at"] if row else None,
    }
    return _json(dumps(body))


def _live_body(state: AppState, live_id: int) -> bytes:
    if not _valid_id(live_id):
        raise HTTPException(status_code=404, detail=LIVE_NOT_FOUND)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = conn.execute(f"{_LIVE_SELECT} WHERE l.id = ?", (live_id,)).fetchone()  # noqa: S608 (constante)
    if row is None:
        raise HTTPException(status_code=404, detail=LIVE_NOT_FOUND)
    if row["status"] is None:  # o worker ainda nao leu esta live
        raise HTTPException(status_code=503, detail=NOT_READY_LIVE)
    return dumps(_live_public(row))


def _set_body(state: AppState, set_id: int) -> bytes:
    if not _valid_id(set_id):
        raise HTTPException(status_code=404, detail=SET_NOT_FOUND)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        parent = conn.execute(
            "SELECT id, name, goal, color, font, text_color FROM sets WHERE id = ?", (set_id,)
        ).fetchone()
        if parent is None:
            raise HTTPException(status_code=404, detail=SET_NOT_FOUND)
        rows = conn.execute(f"{_LIVE_SELECT} WHERE l.set_id = ? ORDER BY l.position, l.id", (set_id,)).fetchall()  # noqa: S608
    viewers = [row["viewers"] for row in rows if row["viewers"] is not None]
    likes = [row["likes"] for row in rows if row["likes"] is not None]
    likes_total = sum(likes) if likes else None  # so as lives que tem likes (YouTube) entram; nenhuma = null
    stamps = [row["updated_at"] for row in rows if row["updated_at"] is not None]
    body = {
        "id": parent["id"],
        "name": parent["name"],
        "viewers_total": sum(viewers),
        "likes_total": likes_total,
        "goal": parent["goal"],
        "progress": _progress(likes_total, parent["goal"]),
        "color": parent["color"],
        "font": parent["font"],
        "text_color": parent["text_color"],
        "updated_at": max(stamps) if stamps else None,
        "lives": [
            {
                key: row[key]
                for key in ("id", "platform", "label", "status", "viewers", "likes", "color", "font", "text_color")
            }
            for row in rows
        ],
    }
    return dumps(body)


@router.get("/api/public/lives/{live_id:int}")
def public_live(live_id: int, state: AppState = Depends(get_state)) -> Response:
    return _json(_live_body(state, live_id))


@router.get("/api/public/sets/{set_id:int}")
def public_set(set_id: int, state: AppState = Depends(get_state)) -> Response:
    return _json(_set_body(state, set_id))


@router.get("/api/public/chat/{set_id:int}")
def public_chat(set_id: int, state: AppState = Depends(get_state)) -> Response:
    """D57: so a aparencia do overlay de chat (nada do dono, filtros ou lives); 404 se o conjunto nao existe."""
    if not _valid_id(set_id):
        raise HTTPException(status_code=404, detail=SET_NOT_FOUND)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        if conn.execute("SELECT 1 FROM sets WHERE id = ?", (set_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail=SET_NOT_FOUND)
        return _json(dumps(chats.public_appearance(conn, set_id)))


def _subscribers_extended(state: AppState, counter_id: int) -> Response:
    obj, counter = _subscribers_object_or_error(state, counter_id)
    return _json(dumps({**obj, "font": counter["font"], "text_color": counter["text_color"]}))


@router.get("/api/public/subscribers")
def public_subscribers(state: AppState = Depends(get_state)) -> Response:
    """Formato estendido: o de ``/inscritos.json`` acrescido de ``font`` e ``text_color`` do contador (D35). Contador 1 (mestre)."""
    return _subscribers_extended(state, MASTER_COUNTER_ID)


@router.get("/api/public/subscribers/{counter_id:int}")
def public_subscribers_of(counter_id: int, state: AppState = Depends(get_state)) -> Response:
    """D51: o mesmo formato estendido, do contador ``counter_id`` (404 se nao existe; 503 sem leitura)."""
    return _subscribers_extended(state, counter_id)


# Atalhos de URL curta (o Nginx so deixa passar ids com digitos).
@router.get("/lives/{live_id:int}.json")
def shortcut_live(live_id: int, state: AppState = Depends(get_state)) -> Response:
    return _json(_live_body(state, live_id))


@router.get("/conjuntos/{set_id:int}.json")
def shortcut_set(set_id: int, state: AppState = Depends(get_state)) -> Response:
    return _json(_set_body(state, set_id))


@router.get("/inscritos.json")
def inscritos_json(state: AppState = Depends(get_state)) -> Response:
    return _json(_subscribers_body(state))


@router.get("/inscritos/{counter_id:int}.json")
def shortcut_subscribers(counter_id: int, state: AppState = Depends(get_state)) -> Response:
    return _json(_subscribers_body(state, counter_id))
