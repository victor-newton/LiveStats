"""Aplicacao FastAPI. Entrada: ``uvicorn app.main:app``.

A configuracao e lida do ambiente na SUBIDA (lifespan): sem variavel/segredo valido o processo nao sobe.
``create_app`` aceita a fonte de configuracao e o relogio para poderem ser injetados (testes avancam o
tempo sem dormir); nao existe nenhuma opcao que desligue login, CSRF ou limite de tentativas.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from typing import Mapping

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import db
from .auth import AdminAuth
from .clock import Clock, SystemClock
from .config import load_settings
from .cookies import CookieSealer
from .deps import ApiError, AppState, ValidationFailed
from .routes_admin import login_router
from .routes_admin import router as admin_router
from .routes_master import router as master_router
from .routes_public import router as public_router
from .schemas import sanitize_errors

log = logging.getLogger("livestats.api")


class NoStoreMiddleware:
    """``Cache-Control: no-store`` em todas as respostas (sao todas dinamicas)."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    headers.append((b"cache-control", b"no-store"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app(environ: Mapping[str, str] | None = None, clock: Clock | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        os.umask(0o007)  # D1: arquivos de banco sem acesso de "outros"
        settings = load_settings(environ)
        db.bootstrap_schema(settings.db_path, settings.schema_sql)
        db.bootstrap_schema(settings.admin_db_path, settings.admin_schema_sql)
        clk = clock or SystemClock()
        db.bootstrap_master(settings.db_path, settings.admin_db_path, settings.admin_username, settings.password_hash, int(clk.now()))
        app.state.ls = AppState(
            settings=settings,
            clock=clk,
            auth=AdminAuth(settings, clk),
            sealer=CookieSealer(settings.cookie_public_key),
        )
        log.info("api pronta")
        yield

    app = FastAPI(
        title="LiveStats API",
        version="4.0.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(NoStoreMiddleware)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": sanitize_errors(list(exc.errors()))}, status_code=422)

    @app.exception_handler(ValidationFailed)
    async def _validation_failed(_: Request, exc: ValidationFailed) -> JSONResponse:
        return JSONResponse({"detail": exc.errors}, status_code=422)

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse({"detail": exc.detail, "code": exc.code}, status_code=exc.status_code)

    @app.exception_handler(sqlite3.Error)
    async def _database(_: Request, exc: sqlite3.Error) -> JSONResponse:
        # Registra so o tipo: a mensagem de erro do SQLite nunca leva dados de requisicao.
        log.error("banco de dados indisponivel (%s)", type(exc).__name__)
        return JSONResponse({"detail": "Banco de dados indisponível"}, status_code=503)

    app.include_router(public_router)
    app.include_router(login_router)
    app.include_router(master_router)  # antes do admin: a rota pega-tudo do admin e a ultima
    app.include_router(admin_router)
    return app


app = create_app()
