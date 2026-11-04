"""Fallback opcional do Kick: Chromium headless (Playwright), usado SÓ quando o Cloudflare bloqueia o httpx.

Fica isolado aqui para o resto do worker não depender do Playwright: a importação é preguiçosa e, se o
pacote ou o Chromium não existirem, `buscar_json` levanta `LeituraErro("blocked")`. O navegador sobe na
primeira necessidade e é reaproveitado; contexto efêmero, sem perfil nem cookies persistentes.
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from worker_lives.leitores import BROWSER_HEADERS, KICK_API, USER_AGENT, LeituraErro

log = logging.getLogger("worker_lives")

NAV_TIMEOUT_MS = 8_000  # D42: o Kick também entra no limite de 15 s por live
CHROMIUM_ARGS = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]  # o container é o sandbox


class NavegadorKickPlaywright:
    """O Playwright síncrono NÃO é thread-safe e fica preso à thread que o iniciou (D42): todo uso do navegador
    (subir, buscar, fechar) roda numa única thread dedicada, o que também serializa as consultas."""

    def __init__(self) -> None:
        self._thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="kick-browser")
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None

    def _iniciar(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise LeituraErro("blocked", "Kick bloqueou a consulta e o Playwright não está instalado") from None
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True, args=CHROMIUM_ARGS)
        self._context = self._browser.new_context(
            user_agent=USER_AGENT, locale="pt-BR", extra_http_headers={"Accept-Language": BROWSER_HEADERS["Accept-Language"]}
        )
        self._context.set_default_timeout(NAV_TIMEOUT_MS)

    def buscar_json(self, slug: str) -> Any:
        return self._thread.submit(self._buscar_json, slug).result()

    def _buscar_json(self, slug: str) -> Any:
        try:
            if self._context is None:
                self._iniciar()
            page = self._context.new_page()
            try:
                page.goto(f"{KICK_API}/{slug}", timeout=NAV_TIMEOUT_MS)
                texto = page.inner_text("body")
            finally:
                page.close()
            return json.loads(texto)
        except LeituraErro:
            raise
        except ValueError:
            raise LeituraErro("blocked", "Kick bloqueou a consulta (desafio do Cloudflare)") from None
        except Exception as exc:  # erros do Playwright: só o tipo vai ao log
            log.warning("Falha no navegador do Kick (%s)", type(exc).__name__)
            self._fechar()
            raise LeituraErro("blocked", "Kick bloqueou a consulta e o navegador falhou") from None

    def fechar(self) -> None:
        try:
            self._thread.submit(self._fechar).result(timeout=10)
        except Exception:  # noqa: BLE001 - encerramento: melhor esforço
            pass
        self._thread.shutdown(wait=False)

    def _fechar(self) -> None:
        for alvo in (self._context, self._browser):
            try:
                if alvo is not None:
                    alvo.close()
            except Exception:  # noqa: BLE001 - encerramento: melhor esforço
                pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:  # noqa: BLE001
            pass
        self._pw = self._browser = self._context = None
