"""Chromium via Playwright: lancamento, injecao de cookies, carga do Studio e leitura do contador.

Seguranca (ARCHITECTURE.md D16, S3, S4, V7):
- Playwright controla o Chromium por PIPE (`--remote-debugging-pipe`); sem porta TCP de depuracao remota.
- Contexto efemero (`launch` + `new_context`): sem diretorio de perfil persistente.
- `--no-sandbox` e a compensacao do container nao-root sem capabilities (D16); o container e o sandbox.
- Print de debug so se `debug_dir` for informado (DEBUG_SCREENSHOT=1), em /tmp/debug.
- Nada aqui registra nome ou valor de cookie, nem o texto de excecoes que possam citar cookies.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

log = logging.getLogger("worker_inscritos.browser")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)
VIEWPORT = {"width": 1280, "height": 900}

# Mesmos argumentos do scraper original, menos a porta TCP de depuracao remota (V7) e --user-agent (vai no contexto).
CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,900",
    "--disable-blink-features=AutomationControlled",
]
# O Playwright acrescenta --enable-automation por padrao; o original o removia (excludeSwitches).
IGNORE_DEFAULT_ARGS = ["--enable-automation"]

INIT_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"

# As tres estrategias de leitura do `yta-smooth-counter` (docs/legacy-formats.md 2.2), em ordem.
# Cada uma so vale se o texto montado casar /\d{3,}/.
JS_ESTRATEGIA_1 = r"""
() => {
    const counters = document.querySelectorAll('yta-smooth-counter');
    for (const counter of counters) {
        const valores = counter.querySelectorAll('.counter-value.last');
        if (!valores.length) continue;
        let num = '';
        for (const v of valores)
            num += (v.innerText || v.textContent || '').trim();
        if (/\d{3,}/.test(num)) return num;
    }
    return null;
}
"""

JS_ESTRATEGIA_2 = r"""
() => {
    const counters = document.querySelectorAll('yta-smooth-counter');
    for (const counter of counters) {
        const seqs = counter.querySelectorAll('.counter-sequence-inner');
        if (!seqs.length) continue;
        let num = '';
        for (const seq of seqs) {
            const divs = seq.querySelectorAll('div');
            if (!divs.length) continue;
            num += (divs[divs.length - 1].innerText || divs[divs.length - 1].textContent || '').trim();
        }
        if (/\d{3,}/.test(num)) return num;
    }
    return null;
}
"""

JS_ESTRATEGIA_3 = r"""
() => {
    const counters = document.querySelectorAll('yta-smooth-counter');
    for (const counter of counters) {
        const txt = (counter.innerText || counter.textContent || '').trim();
        if (/\d{3,}/.test(txt)) return txt;
    }
    return null;
}
"""

ESTRATEGIAS = (JS_ESTRATEGIA_1, JS_ESTRATEGIA_2, JS_ESTRATEGIA_3)


@dataclass(frozen=True)
class Timings:
    """Esperas do fluxo de carga (valores do scraper original). Injetaveis para quem constroi o worker."""

    after_www: float = 3.0  # depois de abrir o youtube.com, antes de apagar/inserir cookies
    ready_timeout: float = 30.0  # espera por document.readyState == complete (segue em frente se estourar)
    after_studio: float = 12.0  # depois do Studio carregar
    nav_timeout: float = 60.0  # teto de cada navegacao


def is_login_url(url: str) -> bool:
    """Mesma regra do original: login perdido se a URL contem accounts.google.com ou signin."""
    return "accounts.google.com" in url or "signin" in url.lower()


class BrowserSession:
    def __init__(
        self,
        www_base_url: str,
        studio_url: str,
        timings: Timings | None = None,
        stop: threading.Event | None = None,
        debug_dir: str | None = None,
    ):
        self.www_base_url = www_base_url
        self.studio_url = studio_url
        self.timings = timings or Timings()
        self.stop = stop or threading.Event()
        self.debug_dir = debug_dir
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self._crashed = False

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        self._pw = sync_playwright().start()
        self._launch()

    def _launch(self) -> None:
        assert self._pw is not None
        self._browser = self._pw.chromium.launch(
            headless=True,
            channel="chromium",  # "new headless" (o original usava --headless=new)
            args=CHROMIUM_ARGS,
            ignore_default_args=IGNORE_DEFAULT_ARGS,
        )
        self.context = self._browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        self.context.set_default_timeout(self.timings.nav_timeout * 1000)
        self.context.add_init_script(INIT_SCRIPT)
        self._new_page()

    def _new_page(self) -> None:
        assert self.context is not None
        self._crashed = False
        self.page = self.context.new_page()
        self.page.on("crash", self._on_crash)

    def _on_crash(self, _page: Page) -> None:
        self._crashed = True

    def _ensure_ready(self) -> None:
        """Recria a pagina (ou o navegador inteiro) se tiver caido, antes de uma carga completa."""
        if self._browser is None or not self._browser.is_connected():
            log.warning("[BROWSER] navegador desconectado; reiniciando")
            self._close_browser()
            self._launch()
        elif self.page is None or self.page.is_closed() or self._crashed:
            log.warning("[BROWSER] pagina fechada ou travada; abrindo outra")
            self._new_page()

    def _close_browser(self) -> None:
        for alvo in (self.context, self._browser):
            if alvo is not None:
                try:
                    alvo.close()
                except PlaywrightError as exc:
                    log.debug("[BROWSER] erro ao fechar (%s)", type(exc).__name__)
        self.context = self._browser = self.page = None

    def close(self) -> None:
        self._close_browser()
        if self._pw is not None:
            try:
                self._pw.stop()
            except PlaywrightError as exc:
                log.debug("[BROWSER] erro ao parar o Playwright (%s)", type(exc).__name__)
            self._pw = None

    @property
    def started(self) -> bool:
        return self._pw is not None

    # ------------------------------------------------------------------ carga
    def load_page(self, cookies: list[dict[str, Any]]) -> None:
        """Fluxo do original: youtube.com, espera, apaga cookies, insere, abre o Studio, espera carregar."""
        self._ensure_ready()
        assert self.page is not None and self.context is not None
        t = self.timings
        timeout_ms = t.nav_timeout * 1000
        log.info("[AUTH] Injetando %d cookies...", len(cookies))
        self.page.goto(self.www_base_url, timeout=timeout_ms)
        self.stop.wait(t.after_www)
        self.context.clear_cookies()
        self._add_cookies(cookies)

        log.info("[AUTH] Abrindo YouTube Studio Analytics...")
        self.page.goto(self.studio_url, timeout=timeout_ms)
        try:
            self.page.wait_for_function("document.readyState === 'complete'", timeout=t.ready_timeout * 1000)
        except PlaywrightTimeoutError:
            log.warning("[AUTH] Timeout readyState, continuando...")
        self.stop.wait(t.after_studio)
        self._debug_screenshot()

        url = self.page.url
        if is_login_url(url):
            log.error("[AUTH] *** REDIRECIONADO PARA LOGIN - atualize os cookies no painel admin! ***")
        else:
            log.info("[AUTH] OK - %s", urlsplit(url).netloc)

    def _add_cookies(self, cookies: list[dict[str, Any]]) -> None:
        assert self.context is not None
        if not cookies:
            return
        try:
            self.context.add_cookies(cookies)
            return
        except PlaywrightError as exc:
            # So o TIPO do erro vai ao log: a mensagem pode citar o cookie.
            log.warning("[AUTH] add_cookies em lote falhou (%s); tentando um a um", type(exc).__name__)
        ignorados = 0
        for indice, cookie in enumerate(cookies, start=1):
            try:
                self.context.add_cookies([cookie])
            except PlaywrightError as exc:
                ignorados += 1
                log.warning("[AUTH] cookie #%d ignorado (%s)", indice, type(exc).__name__)
        log.info("[AUTH] %d de %d cookies injetados", len(cookies) - ignorados, len(cookies))

    def _debug_screenshot(self) -> None:
        if not self.debug_dir or self.page is None:
            return
        destino = Path(self.debug_dir)
        destino.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.page.screenshot(path=str(destino / "debug.png"))
        log.info("[DEBUG] screenshot gravado em %s", destino)

    # ------------------------------------------------------------------ leitura
    def current_url(self) -> str:
        assert self.page is not None
        return self.page.url

    def read_counter(self) -> str | None:
        """Texto cru do contador, pela primeira estrategia que casar /\\d{3,}/; None se nenhuma casar."""
        assert self.page is not None
        for estrategia in ESTRATEGIAS:
            bruto = self.page.evaluate(estrategia)
            if bruto:
                return str(bruto)
        return None

