"""Auxiliares compartilhados pelos testes do front (sem fixtures)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlsplit

from playwright.sync_api import Page

from api_double import ApiDouble

CLOCK_START = datetime(2026, 11, 2, 12, 0, 0, tzinfo=timezone.utc)


@dataclass
class Watch:
    """Observa todas as páginas do contexto: console, requisições e diálogos."""

    origin: str
    console: list[tuple[str, str]] = field(default_factory=list)  # (tipo, texto)
    requests: list[str] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    def attach(self, page: Page) -> None:
        page.on("console", lambda m: self.console.append((m.type, m.text)))
        page.on("request", lambda r: self.requests.append(r.url))
        page.on("pageerror", lambda e: self.page_errors.append(str(e)))
        page.on("requestfailed", lambda r: self.failed.append(f"{r.url} {r.failure}"))
        page.on("dialog", self._dialog)

    def _dialog(self, dialog) -> None:  # type: ignore[no-untyped-def]
        self.dialogs.append(f"{dialog.type}: {dialog.message}")
        dialog.dismiss()

    @property
    def csp_violations(self) -> list[str]:
        return [
            text
            for kind, text in self.console
            if "CSP_VIOLATION" in text or "Content Security Policy" in text or "Refused to" in text
        ]

    @property
    def foreign_requests(self) -> list[str]:
        own = urlsplit(self.origin).netloc
        out = []
        for url in self.requests:
            parts = urlsplit(url)
            if parts.scheme in ("data", "about"):
                continue
            if parts.netloc != own:
                out.append(url)
        return out


def freeze_clock(page: Page) -> None:
    """Relógio falso e parado: os temporizadores da página só disparam em
    `page.clock.run_for(ms)`. Chamar ANTES de abrir a página."""
    page.clock.install(time=CLOCK_START)
    page.clock.pause_at(CLOCK_START + timedelta(seconds=1))


def do_login(page: Page, base_url: str, api: ApiDouble) -> None:
    """Entra pelo formulário, como o usuário, e espera o painel e a 1ª carga
    de dados (slots e status) ter sido atendida pelo servidor."""
    page.goto(f"{base_url}/")
    page.get_by_label("Usuário").fill(api.username)
    page.get_by_label("Senha").fill(api.password)
    page.get_by_role("button", name="Entrar").click()
    page.locator("#panelView").wait_for(state="visible")
    for path in ("/api/admin/slots", "/api/admin/workers/status"):
        wait_until(
            lambda p=path: any(r.status for r in api.requests("GET", p)),
            f"primeira resposta de {path}",
        )


def fetch_status(page: Page, method: str, path: str, body: object | None = None) -> int:
    """Faz uma requisição da própria página (mesma origem, com o cookie de
    sessão do navegador, SEM o cabeçalho CSRF) e devolve o status HTTP."""
    return page.evaluate(
        """async ([method, path, body]) => {
            const init = {method, credentials: 'same-origin'};
            if (body !== null) {
                init.headers = {'Content-Type': 'application/json'};
                init.body = JSON.stringify(body);
            }
            return (await fetch(path, init)).status;
        }""",
        [method, path, body],
    )


_pump: Page | None = None


def set_pump(page: Page | None) -> None:
    """Página usada por `wait_until` para entregar eventos do Playwright."""
    global _pump
    _pump = page


def wait_until(
    condition: Callable[[], object], what: str, timeout: float = 5.0, page: Page | None = None
) -> None:
    """Espera (tempo REAL) até `condition()` ser verdadeira; falha com `what`.

    Passe `page` quando a condição depender de eventos do Playwright (handlers
    de `page.route`, console, requisições): eles só são entregues enquanto a
    thread principal está dentro de uma chamada do Playwright.
    """
    page = page or _pump
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        if page is not None:
            page.wait_for_timeout(20)
        else:
            time.sleep(0.02)
    raise AssertionError(f"Tempo esgotado esperando: {what}")


# D35: o unico svg legitimo e o diagrama estatico da secao Saude. Ele e identificado pela POSICAO no DOM
# (o elemento exato: filho direto de .diagram-wrap, filho direto de section#saude, ambos do index.html);
# a identidade e comparada por referencia, nunca pelo id. Qualquer outro svg (inclusive outro #healthDiagram,
# dentro ou fora do conteiner) conta como injecao. O conteudo do diagrama e checado em WEB-SAU-08.
CONTA_INJETADOS = """() => {
    const permitido = document.querySelector('section#saude > .diagram-wrap > svg#healthDiagram');
    const alvo = document.body.querySelectorAll('img, svg, iframe, object, embed, script, style, a');
    return [...alvo].filter((e) => e !== permitido).length;
}"""


def injected_count(page: Page) -> int:
    return page.evaluate(CONTA_INJETADOS)


def no_html_injected(page: Page) -> None:
    """Nenhum elemento "perigoso" nasceu dentro do corpo da página: o que veio
    do servidor foi tratado como texto."""
    n = injected_count(page)
    assert n == 0, f"{n} elementos injetados no corpo"
    assert page.evaluate("window.__xss") is None
