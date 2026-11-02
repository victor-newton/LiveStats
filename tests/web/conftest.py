"""Fixtures da suíte do front (T6): Chromium real + dublê do contrato HTTP.

A suíte roda por padrão contra o servidor-dublê (`api_double.py`), que serve
`web/static` com a CSP do contrato e responde a API validando cada resposta no
`docs/openapi.yaml` (D26).

Contra a pilha real (T8), defina `WEB_BASE_URL` (ex.: `http://127.0.0.1:8081`):
o navegador carrega as páginas dessa origem e as chamadas à API continuam sendo
atendidas pelo dublê (via `context.route`), de modo que os mesmos testes
exercitam o Nginx real (CSP, cabeçalhos, mapeamento de URLs) com dados
controlados. Os estados que dependem do banco real ficam a cargo da T8.
"""

from __future__ import annotations

import os
import re
from typing import Iterator
from urllib.parse import urlsplit

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, Route, sync_playwright

from api_double import ApiDouble, StackServer
from web_helpers import Watch

API_PATH = re.compile(r"^/(api/|live\d+\.json$|inscritos\.json$|health$)")


@pytest.fixture(scope="session")
def stack() -> Iterator[StackServer]:
    server = StackServer(ApiDouble())
    server.start()
    yield server
    server.stop()


@pytest.fixture(scope="session")
def playwright_driver() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="session")
def browser(playwright_driver: Playwright) -> Iterator[Browser]:
    b = playwright_driver.chromium.launch()
    yield b
    b.close()


@pytest.fixture
def api(stack: StackServer) -> Iterator[ApiDouble]:
    """Estado do dublê, limpo a cada teste. Falha o teste se o dublê emitiu algo
    fora do contrato."""
    stack.api.reset()
    yield stack.api
    assert not stack.api.violations, "Violações do openapi.yaml: " + " | ".join(stack.api.violations)


@pytest.fixture
def base_url(stack: StackServer) -> str:
    return os.environ.get("WEB_BASE_URL", "").rstrip("/") or stack.base_url


@pytest.fixture
def legacy_base_url(stack: StackServer) -> str:
    """Origem que serve o contador ORIGINAL (golden file) para comparação."""
    return stack.base_url


CSP_REPORTER = (
    "document.addEventListener('securitypolicyviolation', (e) => {"
    "console.error('CSP_VIOLATION', e.violatedDirective, e.blockedURI);}, true);"
)


@pytest.fixture
def context(browser: Browser, stack: StackServer, base_url: str, api: ApiDouble) -> Iterator[BrowserContext]:
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, locale="pt-BR")
    ctx.add_init_script(CSP_REPORTER)
    if base_url != stack.base_url:
        _proxy_api_to_double(ctx, stack.base_url)
    yield ctx
    ctx.close()


def _proxy_api_to_double(ctx: BrowserContext, double_origin: str) -> None:
    """Modo WEB_BASE_URL: páginas vêm da pilha real; a API vem do dublê."""
    jar: dict[str, str] = {}

    def handler(route: Route) -> None:
        req = route.request
        parts = urlsplit(req.url)
        headers = {k: v for k, v in req.headers.items() if k.lower() not in ("cookie", "host")}
        if jar:
            headers["cookie"] = "; ".join(f"{k}={v}" for k, v in jar.items())
        resp = route.fetch(url=double_origin + parts.path + (f"?{parts.query}" if parts.query else ""), headers=headers)
        for header in resp.headers_array():
            if header["name"].lower() == "set-cookie":
                name, _, rest = header["value"].partition("=")
                value = rest.split(";", 1)[0]
                if "max-age=0" in header["value"].lower():
                    jar.pop(name, None)
                else:
                    jar[name] = value
        headers_out = {k: v for k, v in resp.headers.items() if k.lower() != "set-cookie"}
        route.fulfill(response=resp, headers=headers_out)

    ctx.route(lambda url: bool(API_PATH.match(urlsplit(url).path)), handler)


@pytest.fixture
def watch(base_url: str) -> Watch:
    return Watch(origin=base_url)


@pytest.fixture
def page(context: BrowserContext, watch: Watch) -> Iterator[Page]:
    p = context.new_page()
    watch.attach(p)
    yield p
    assert not watch.csp_violations, f"Violações de CSP: {watch.csp_violations}"
    assert not watch.foreign_requests, f"Requisições a terceiros: {watch.foreign_requests}"
    assert not watch.page_errors, f"Erros de JavaScript: {watch.page_errors}"


