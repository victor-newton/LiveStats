"""Chromium do worker-chat (Playwright assincrono), compartilhado pelo YouTube (uma aba por live) e pelo plano B do Kick.

Um unico navegador, subido na primeira necessidade e relancado se cair; cada uso pede o proprio contexto (sem perfil,
sem login, sem cookie persistente: tudo some ao fechar). Imagens, midia e fontes sao bloqueadas no contexto, porque o
worker so le respostas de rede e nunca desenha a pagina. A porta de depuracao do Chrome nao existe (o Playwright fala
com o navegador por pipe). O proprio container e o sandbox (`--no-sandbox`), como no `worker_lives/kick_browser.py`.
"""

from __future__ import annotations

import asyncio
import logging

from playwright.async_api import Browser, BrowserContext, Playwright, Route, async_playwright
from playwright.async_api import Error as BrowserError
from playwright.async_api import TimeoutError as BrowserTimeout

from worker_chat.config import USER_AGENT

__all__ = ["BrowserError", "BrowserTimeout", "ChatBrowser"]

log = logging.getLogger("worker_chat.browser")

CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
ACCEPT_LANGUAGE = "pt-BR,pt;q=0.9,en;q=0.8"
_BLOCKED_TYPES = frozenset({"image", "media", "font"})


async def _skip_heavy(route: Route) -> None:
    if route.request.resource_type in _BLOCKED_TYPES:
        await route.abort()
    else:
        await route.continue_()


class ChatBrowser:
    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._lock = asyncio.Lock()

    async def new_context(self, cookies: list[dict[str, str]] | None = None) -> BrowserContext:
        """Contexto novo e isolado (quem pede fecha). `cookies`: lista no formato do Playwright (`name`, `value`, `domain`/`url`)."""
        async with self._lock:
            if self._browser is None or not self._browser.is_connected():
                await self._launch()
            assert self._browser is not None
            context = await self._browser.new_context(
                user_agent=USER_AGENT, locale="pt-BR", extra_http_headers={"Accept-Language": ACCEPT_LANGUAGE}
            )
        try:
            if cookies:
                await context.add_cookies(cookies)  # type: ignore[arg-type]
            await context.route("**/*", _skip_heavy)
        except BaseException:
            await context.close()
            raise
        return context

    async def _launch(self) -> None:
        await self._shutdown()
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        log.info("Chromium do chat iniciado")

    async def _shutdown(self) -> None:
        browser, pw = self._browser, self._pw
        self._browser = self._pw = None
        try:
            if browser is not None:
                await browser.close()
        except BrowserError as exc:  # navegador ja morto: so registra o tipo
            log.warning("Chromium: falha ao fechar o navegador (%s)", type(exc).__name__)
        try:
            if pw is not None:
                await pw.stop()
        except BrowserError as exc:
            log.warning("Chromium: falha ao encerrar o Playwright (%s)", type(exc).__name__)

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown()
