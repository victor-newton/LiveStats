"""Endpoints publicos (somente leitura) e aliases legados. Sem autenticacao e SEM CORS: o CORS dos GET
publicos de dados e emitido pelo Nginx (D10); a api nunca envia ``Access-Control-*``."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from . import db
from .clock import legacy_timestamp
from .deps import AppState, get_state
from .legacy import dumps, live_object, subscribers_object

router = APIRouter()

_LIVE_COLUMNS = "slot, active, video_id, title, status, views, likes, views_formatted, likes_formatted, updated_at"
NOT_READY_LIVE = "Dados ainda não disponíveis"
NOT_READY_SUBSCRIBERS = "Dados ainda não disponíveis. Scraper inicializando..."
INVALID_SLOT = "Slot inválido (1-4)"


def _json(body: bytes) -> Response:
    return Response(content=body, media_type="application/json", headers={"Cache-Control": "no-store"})


def _live_object_or_error(state: AppState, slot: int) -> dict:
    if slot < 1 or slot > 4:
        raise HTTPException(status_code=404, detail=INVALID_SLOT)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = conn.execute(f"SELECT {_LIVE_COLUMNS} FROM live_stats WHERE slot = ?", (slot,)).fetchone()  # noqa: S608
    if row is None:
        raise HTTPException(status_code=503, detail=NOT_READY_LIVE)
    return live_object(row)


def _subscribers_body(state: AppState) -> bytes:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = conn.execute("SELECT value, raw, read_at FROM subscriber_reading WHERE id = 1").fetchone()
    if row is None:
        raise HTTPException(status_code=503, detail=NOT_READY_SUBSCRIBERS)
    return dumps(subscribers_object(row))


@router.get("/health")
def health(state: AppState = Depends(get_state)) -> Response:
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = conn.execute("SELECT value, read_at FROM subscriber_reading WHERE id = 1").fetchone()
    with db.connection(db.open_admin, state.settings.admin_db_path) as admin_conn:
        admin_conn.execute("SELECT key FROM schema_meta LIMIT 1").fetchall()
    body = {
        "status": "ok",
        "server_time": legacy_timestamp(state.clock.now()),
        "data_file_exists": row is not None,
        "inscritos": row["value"] if row else None,
        "last_update": row["read_at"] if row else None,
    }
    return _json(dumps(body))


@router.get("/api/public/lives/{slot}")
def public_live(slot: int, state: AppState = Depends(get_state)) -> Response:
    """Formato novo (objeto): o legado acrescido de ``goal`` e ``color`` do slot (D34). Os ``/live{n}.json`` nao mudam."""
    obj = _live_object_or_error(state, slot)
    with db.connection(db.open_shared, state.settings.db_path) as conn:
        row = conn.execute("SELECT goal, color FROM slots WHERE slot = ?", (slot,)).fetchone()
    return _json(dumps({**obj, "goal": row["goal"], "color": row["color"]}))


@router.get("/api/public/subscribers")
def public_subscribers(state: AppState = Depends(get_state)) -> Response:
    return _json(_subscribers_body(state))


@router.get("/live{n}.json")
def legacy_live(n: int, state: AppState = Depends(get_state)) -> Response:
    return _json(dumps([_live_object_or_error(state, n)]))


@router.get("/inscritos.json")
def legacy_subscribers(state: AppState = Depends(get_state)) -> Response:
    return _json(_subscribers_body(state))
