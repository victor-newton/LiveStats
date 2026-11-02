"""Estado compartilhado, dependencias de autenticacao/CSRF e leitura segura do corpo."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from .auth import SESSION_COOKIE, AdminAuth, SessionData, is_unsafe_method
from .clock import Clock
from .config import Settings
from .cookies import CookieSealer
from .schemas import sanitize_errors

MAX_BODY_BYTES = 256 * 1024  # D13: 256 KiB (o Nginx tambem limita)

M = TypeVar("M", bound=BaseModel)


@dataclass
class AppState:
    settings: Settings
    clock: Clock
    auth: AdminAuth
    sealer: CookieSealer


class ValidationFailed(Exception):
    """422 com erros ja saneados (``loc``, ``msg``, ``type``)."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("validacao falhou")
        self.errors = errors


def get_state(request: Request) -> AppState:
    return request.app.state.ls


def client_ip(request: Request) -> str:
    """``X-Real-IP`` (definido pelo Nginx) quando for um IP valido; senao o endereco da conexao."""
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        try:
            return str(ipaddress.ip_address(real))
        except ValueError:
            pass
    return request.client.host if request.client else "unknown"


def require_session(request: Request, state: AppState = Depends(get_state)) -> SessionData:
    session = state.auth.load_session(request.cookies.get(SESSION_COOKIE))
    if session is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return session


def require_csrf(request: Request, session: SessionData = Depends(require_session)) -> SessionData:
    if is_unsafe_method(request.method) and not AdminAuth.csrf_matches(session, request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="CSRF token inválido ou ausente")
    return session


async def read_body(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Corpo da requisição grande demais")
    body = await request.body()
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Corpo da requisição grande demais")
    return body


def _reject_constant(_name: str) -> None:
    raise ValueError("constante JSON nao permitida")


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw, parse_constant=_reject_constant)
    except (ValueError, RecursionError):
        raise HTTPException(status_code=400, detail="JSON inválido no body da requisição") from None


def validate_body(model: type[M], payload: Any) -> M:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        errors = [{**e, "loc": ("body", *e["loc"])} for e in exc.errors(include_input=False, include_url=False, include_context=False)]
        raise ValidationFailed(sanitize_errors(errors)) from None
