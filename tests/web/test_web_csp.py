"""WEB-CSP-01/02 e WEB-NET-01: nada inline, nada de terceiros, CSP do contrato."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from api_double import CSP, CSP_OVERLAY, STATIC
from playwright.sync_api import Page, expect
from web_helpers import Watch, do_login

# ---------------------------------------------------------------------------
# Varredura estática
# ---------------------------------------------------------------------------

JS_FORBIDDEN = {
    "innerHTML": r"\binnerHTML\b",
    "outerHTML": r"\bouterHTML\b",
    "insertAdjacentHTML": r"\binsertAdjacentHTML\b",
    "document.write": r"\bdocument\s*\.\s*write(ln)?\b",
    "eval": r"\beval\s*\(",
    "new Function": r"\bnew\s+Function\b",
    "Function(": r"(?<![.\w])Function\s*\(",
    "setTimeout/setInterval com texto": r"\bset(Timeout|Interval)\s*\(\s*[\"'`]",
    "createContextualFragment": r"\bcreateContextualFragment\b",
    "srcdoc": r"\bsrcdoc\b",
    "setAttribute de style/on*": r"setAttribute\s*\(\s*[\"'](style|on[a-z]+)[\"']",
    "atribuição on*": r"\.\s*on[a-z]+\s*=(?!=)",
    "javascript:": r"javascript\s*:",
}
HTML_FORBIDDEN = {
    "script inline": r"<script(?![^>]*\bsrc\s*=)[^>]*>",
    "atributo on*": r"<[a-zA-Z][^>]*\son[a-zA-Z]+\s*=",
    "atributo style": r"<[a-zA-Z][^>]*\sstyle\s*=",
    "elemento style": r"<style\b",
    "javascript:": r"javascript\s*:",
}
EXTERNAL_REF = re.compile(r"""(?:src|href|action|poster)\s*=\s*["']?\s*(?:https?:)?//""", re.I)
CSS_EXTERNAL = re.compile(r"""(?:@import\s+["']?|url\(\s*["']?)\s*(?:https?:)?//""", re.I)


def scan_html(text: str) -> list[str]:
    problems = [name for name, rx in HTML_FORBIDDEN.items() if re.search(rx, text, re.I)]
    if EXTERNAL_REF.search(text):
        problems.append("recurso externo (src/href/action)")
    return problems


def scan_js(text: str) -> list[str]:
    problems = [name for name, rx in JS_FORBIDDEN.items() if re.search(rx, text)]
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    if first != "// @ts-check":
        problems.append("falta // @ts-check na primeira linha")
    return problems


def scan_css(text: str) -> list[str]:
    return ["recurso externo (url/@import)"] if CSS_EXTERNAL.search(text) else []


def static_files() -> list[Path]:
    files = sorted(p for p in STATIC.rglob("*") if p.is_file())
    assert files, "web/static está vazio"
    return files


def test_web_csp_01_varredura_estatica_de_web_static():
    seen = {".html": 0, ".js": 0, ".css": 0}
    problems: dict[str, list[str]] = {}
    for f in static_files():
        text = f.read_text(encoding="utf-8") if f.suffix in seen else ""
        found: list[str] = []
        if f.suffix == ".html":
            found = scan_html(text)
        elif f.suffix == ".js":
            found = scan_js(text)
        elif f.suffix == ".css":
            found = scan_css(text)
        if f.suffix in seen:
            seen[f.suffix] += 1
        if found:
            problems[str(f.relative_to(STATIC))] = found
    assert problems == {}
    # há o que varrer: o painel, o overlay e todos os módulos
    assert seen[".html"] >= 2 and seen[".css"] >= 2 and seen[".js"] >= 7, seen


@pytest.mark.parametrize(
    "amostra, esperado",
    [
        ("<script>alert(1)</script>", "script inline"),
        ('<script type="module">x()</script>', "script inline"),
        ('<div onclick="x()">', "atributo on*"),
        ('<p style="color:red">', "atributo style"),
        ("<style>a{}</style>", "elemento style"),
        ('<script src="https://cdn.example/x.js"></script>', "recurso externo (src/href/action)"),
        ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=X">', "recurso externo (src/href/action)"),
        ('<link href="//fonts.googleapis.com/x">', "recurso externo (src/href/action)"),
    ],
)
def test_web_csp_01b_o_detector_de_html_realmente_detecta(amostra, esperado):
    assert esperado in scan_html(amostra)


@pytest.mark.parametrize(
    "amostra, esperado",
    [
        ("el.innerHTML = x", "innerHTML"),
        ("el.outerHTML = x", "outerHTML"),
        ("el.insertAdjacentHTML('beforeend', x)", "insertAdjacentHTML"),
        ("document.write(x)", "document.write"),
        ("eval('1')", "eval"),
        ("new Function('return 1')", "new Function"),
        ("setTimeout('f()', 10)", "setTimeout/setInterval com texto"),
        ("setInterval(`f()`, 10)", "setTimeout/setInterval com texto"),
        ("el.setAttribute('style', 'a:b')", "setAttribute de style/on*"),
        ("el.onclick = f", "atribuição on*"),
        ("x = 1;", "falta // @ts-check na primeira linha"),
    ],
)
def test_web_csp_01c_o_detector_de_js_realmente_detecta(amostra, esperado):
    assert esperado in scan_js(amostra)


def test_web_csp_01d_o_detector_aceita_codigo_legitimo():
    assert scan_js("// @ts-check\nel.style.transform = 'x';\nel.addEventListener('click', f);\nsetTimeout(() => f(), 5);") == []
    assert scan_html('<script type="module" src="/overlay/contador.js"></script>') == []
    assert scan_css("@font-face{src:url('../fonts/a.woff2')}") == []


# ---------------------------------------------------------------------------
# Execução com a CSP do contrato
# ---------------------------------------------------------------------------

def parse_csp(value: str) -> dict[str, frozenset[str]]:
    out = {}
    for part in value.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = frozenset(tokens[1:])
    return out


def test_web_csp_02_paginas_funcionam_sob_a_csp_do_contrato(page: Page, api, base_url, watch: Watch):
    api.set_stats(1, api.live_stats(1))
    api.subscribers = {"inscritos": "460600", "raw": "460600", "atualizado_em": "2026-11-02T14:32:10.654321Z"}

    # painel (o hook do conftest falha o teste se houver QUALQUER violação)
    resp = page.goto(f"{base_url}/")
    assert resp is not None
    assert parse_csp(resp.headers["content-security-policy"]) == parse_csp(CSP)
    assert resp.headers["x-content-type-options"] == "nosniff"
    do_login(page, base_url, api)
    expect(page.locator(".slot-card")).to_have_count(4)
    expect(page.locator(".stat-val").first).to_have_text("1.2K")
    # CSSOM não é bloqueado pela CSP; o painel usa classes e `hidden`
    expect(page.locator("#panelView")).to_be_visible()

    # overlay (frame-ancestors * e sem X-Frame-Options)
    for path in ("/contador", "/overlay/inscritos"):
        resp = page.goto(f"{base_url}{path}")
        assert resp is not None
        assert parse_csp(resp.headers["content-security-policy"]) == parse_csp(CSP_OVERLAY)
        assert "x-frame-options" not in resp.headers
        expect(page.locator(".digito-wrap")).to_have_count(6)
    assert watch.csp_violations == []


def test_web_csp_02b_o_detector_de_violacao_funciona(page: Page, base_url, watch: Watch):
    """Prova que a vigilância de CSP não é vazia: um script inline é barrado e visto."""
    page.goto(f"{base_url}/")
    page.evaluate(
        "() => { const s = document.createElement('script'); s.textContent = 'window.__inline = 1'; document.head.appendChild(s); }"
    )
    page.wait_for_timeout(200)
    assert page.evaluate("window.__inline") is None  # a CSP impediu a execução
    assert watch.csp_violations, "a violação de CSP deveria ter sido registrada"
    watch.console.clear()  # violação provocada de propósito; não deve reprovar o teste


# ---------------------------------------------------------------------------
# Rede: só a própria origem, fontes locais
# ---------------------------------------------------------------------------

def test_web_net_01_so_requisicoes_para_a_propria_origem(page: Page, api, base_url, watch: Watch):
    api.set_stats(1, api.live_stats(1))
    api.subscribers = {"inscritos": "460600", "raw": "460600", "atualizado_em": "2026-11-02T14:32:10.654321Z"}
    do_login(page, base_url, api)
    page.evaluate("() => document.fonts.ready")
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)

    assert watch.requests, "nenhuma requisição observada"
    assert watch.foreign_requests == []
    assert not any("googleapis" in u or "gstatic" in u for u in watch.requests)


def test_web_net_01b_as_fontes_do_painel_vem_do_proprio_servidor(page: Page, api, base_url, watch: Watch):
    do_login(page, base_url, api)
    page.evaluate("() => document.fonts.ready")
    fontes = [u for u in watch.requests if u.endswith(".woff2")]
    assert any("bebas-neue" in u for u in fontes), fontes
    assert any("dm-sans" in u for u in fontes), fontes
    assert all(u.startswith(base_url + "/admin-assets/fonts/") for u in fontes)
    assert page.evaluate("document.fonts.check('16px \"DM Sans\"')") is True
    assert page.evaluate("document.fonts.check('16px \"Bebas Neue\"')") is True
    # as fontes realmente carregaram (nenhuma ficou com erro)
    estados = page.evaluate("[...document.fonts].map((f) => f.status)")
    assert estados and "error" not in estados
