"""Estado compartilhado, dependencias de autenticacao/CSRF e leitura segura do corpo."""

from __future__ import annotations

import ipaddress
import json
from dataclasses import dataclass
from typing import Any, TypeVar

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from .auth import SESSION_COOKIE, AdminAuth, Ctx, ip_allowed, is_unsafe_method
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


class ApiError(Exception):
    """Erro com ``code`` estavel para o painel: ``{"detail": ..., "code": ...}`` (ex.: ``password_change_required``)."""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


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


# Rotas liberadas enquanto a troca de senha e obrigatoria (D48): so estas tres.
_ALLOWED_WHILE_MUST_CHANGE = frozenset({"/api/admin/session", "/api/admin/logout", "/api/admin/me/password"})


def require_session(request: Request, state: AppState = Depends(get_state)) -> Ctx:
    ctx = state.auth.resolve(request.cookies.get(SESSION_COOKIE))
    if ctx is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    if ctx.user.must_change and request.url.path not in _ALLOWED_WHILE_MUST_CHANGE:
        raise ApiError(403, "Troque a senha provisória para continuar", "password_change_required")
    return ctx


def require_csrf(request: Request, ctx: Ctx = Depends(require_session)) -> Ctx:
    if is_unsafe_method(request.method) and not AdminAuth.csrf_matches(ctx.session, request.headers.get("x-csrf-token")):
        raise HTTPException(status_code=403, detail="CSRF token inválido ou ausente")
    return ctx


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not Found")


def require_master(
    request: Request, ctx: Ctx = Depends(require_session), state: AppState = Depends(get_state)
) -> Ctx:
    """Rotas do mestre (D52): usuario comum (ou mestre fora de ``MASTER_ALLOW_CIDRS``) recebe 404, como se a rota nao existisse."""
    if not ctx.is_master or not ip_allowed(state.settings.master_allow_cidrs, client_ip(request)):
        raise _not_found()
    return ctx


def require_master_only(ctx: Ctx = Depends(require_session)) -> Ctx:
    """Saude e eventos (D52): so o mestre (404 para os demais); a lista de IPs do mestre nao se aplica aqui."""
    if not ctx.is_master:
        raise _not_found()
    return ctx


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
