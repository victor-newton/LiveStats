"""WEB-OV-01..07: overlay do contador de inscritos (/contador; D36 removeu /overlay/inscritos)."""

from __future__ import annotations

import io
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from api_double import STATIC
from PIL import Image, ImageChops
from playwright.sync_api import Page, expect
from web_helpers import Watch, freeze_clock, wait_until

PATHS = ["/contador"]
CURVA = "cubic-bezier(0.25, 0.46, 0.45, 0.94)"
LEGACY = "/__legacy__/contador.html"


def leitura(valor: str) -> dict[str, str]:
    return {"inscritos": valor, "raw": valor, "atualizado_em": "2026-11-02T14:32:10.654321Z"}


def transforms(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => e.style.transform)")


def transitions(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => e.style.transition)")


def esperado(digitos: str) -> list[str]:
    # O Chromium serializa "-0em" como "0em"; os demais ficam como "-<d>em".
    return [f"translateY({'-' if d != '0' else ''}{d}em)" for d in digitos]


def n_polls(api) -> int:
    return len(api.requests("GET", "/api/public/subscribers"))


# ---------------------------------------------------------------------------
# WEB-OV-01 estrutura
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", PATHS)
def test_web_ov_01_estrutura_sem_separador(page: Page, api, base_url, path):
    api.subscribers = leitura("460600")
    page.goto(f"{base_url}{path}")
    expect(page.locator("#contador")).to_have_count(1)
    expect(page.locator("#contador > .digito-wrap")).to_have_count(6)
    expect(page.locator(".separador")).to_have_count(0)
    inners = page.locator(".digito-inner")
    expect(inners).to_have_count(6)
    for i in range(6):
        spans = inners.nth(i).locator("span")
        expect(spans).to_have_count(10)
        assert spans.all_inner_texts() == [str(d) for d in range(10)]
    wait_until(lambda: transforms(page) == esperado("460600"), "transform final de cada dígito")


@pytest.mark.parametrize("path", PATHS)
@pytest.mark.parametrize("valor, sep", [("460.600", "."), ("460,600", ",")])
def test_web_ov_01_estrutura_com_separador(page: Page, api, base_url, path, valor, sep):
    api.subscribers = leitura(valor)
    page.goto(f"{base_url}{path}")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    expect(page.locator(".separador")).to_have_count(1)
    expect(page.locator(".separador")).to_have_text(sep)
    filhos = page.evaluate("[...document.getElementById('contador').children].map((e) => e.className)")
    assert filhos == ["digito-wrap"] * 3 + ["separador"] + ["digito-wrap"] * 3
    wait_until(lambda: transforms(page) == esperado("460600"), "transform final de cada dígito")


# ---------------------------------------------------------------------------
# WEB-OV-02 estilos computados (valores do contrato E igualdade com o original)
# ---------------------------------------------------------------------------

PROPS = {
    "body": ["background-color", "display", "align-items", "justify-content", "min-height", "overflow-x", "overflow-y"],
    "#contador": [
        "font-family", "font-size", "font-weight", "color", "letter-spacing", "display", "column-gap",
        "row-gap", "align-items", "filter",
    ],
    ".digito-wrap": ["display", "flex-direction", "height", "overflow-x", "overflow-y", "position"],
    ".digito-inner": ["display", "flex-direction", "transition-property", "transition-duration"],
    ".digito-inner span": ["height", "line-height", "display", "text-align"],
    ".separador": ["display", "padding-left", "padding-right"],
}

COLLECT = """(props) => {
    const out = {};
    for (const [sel, names] of Object.entries(props)) {
        const el = document.querySelector(sel);
        const cs = getComputedStyle(el);
        for (const n of names) out[sel + ' | ' + n] = cs.getPropertyValue(n);
    }
    return out;
}"""


def abre_e_espera(page: Page, url: str, valor: str) -> None:
    digitos = re.sub(r"\D", "", valor)
    page.goto(url)
    expect(page.locator(".digito-wrap")).to_have_count(len(digitos))
    wait_until(lambda: transforms(page) == esperado(digitos), "fim da animação inicial")
    page.wait_for_timeout(700)  # termina a transição CSS de 0,5 s


def test_web_ov_02_estilos_computados_iguais_ao_original(page: Page, api, base_url, legacy_base_url):
    api.subscribers = leitura("460.600")
    abre_e_espera(page, f"{base_url}/contador", "460.600")
    novo = page.evaluate(COLLECT, PROPS)

    original = page.context.new_page()
    original.goto(f"{legacy_base_url}{LEGACY}")
    expect(original.locator(".digito-wrap")).to_have_count(6)
    wait_until(lambda: transforms(original) == esperado("460600"), "original animou")
    antigo = original.evaluate(COLLECT, PROPS)
    original.close()

    assert novo == antigo

    # e os valores do contrato (legacy-formats.md §3), explicitamente
    assert novo["body | background-color"] == "rgba(0, 0, 0, 0)"
    assert novo["#contador | font-size"] == "80px"
    assert novo["#contador | font-weight"] == "700"
    assert novo["#contador | color"] == "rgb(255, 255, 255)"
    assert novo["#contador | letter-spacing"] == "-2px"
    assert novo["#contador | display"] == "flex"
    assert novo["#contador | align-items"] == "center"
    assert novo["#contador | filter"] == "drop-shadow(rgba(0, 0, 0, 0.5) 0px 2px 8px)"
    assert "YouTube Sans" in novo["#contador | font-family"] and "Roboto" in novo["#contador | font-family"]
    assert novo[".digito-wrap | height"] == "80px"  # 1em de 80px
    assert novo[".digito-wrap | overflow-x"] == "hidden" and novo[".digito-wrap | overflow-y"] == "hidden"
    assert novo[".digito-inner span | height"] == "80px"
    assert novo[".separador | padding-left"] == "1px" and novo[".separador | padding-right"] == "1px"
    assert novo["body | min-height"] == "900px"  # 100vh na viewport do teste


# ---------------------------------------------------------------------------
# WEB-OV-03 comparação visual (diferença de pixels = 0)
# ---------------------------------------------------------------------------

def captura(page: Page, url: str, valor: str) -> Image.Image:
    abre_e_espera(page, url, valor)
    # fundo magenta aplicado pelo CSSOM (não é bloqueado pela CSP)
    page.evaluate("document.documentElement.style.background = '#ff00ff'")
    png = page.screenshot()
    return Image.open(io.BytesIO(png)).convert("RGB")


@pytest.mark.parametrize("valor", ["460600", "460.600", "1.234.567"])
def test_web_ov_03_visual_identico_ao_original(page: Page, api, base_url, legacy_base_url, valor):
    api.subscribers = leitura(valor)
    novo = captura(page, f"{base_url}/contador", valor)
    original_page = page.context.new_page()
    antigo = captura(original_page, f"{legacy_base_url}{LEGACY}", valor)
    original_page.close()

    assert novo.size == antigo.size
    # a captura não é vazia: tem o magenta de fundo E os dígitos brancos
    cores = dict((c, n) for n, c in novo.getcolors(maxcolors=1_000_000))
    assert cores.get((255, 0, 255), 0) > novo.size[0] * novo.size[1] * 0.8
    assert sum(n for c, n in cores.items() if min(c) > 240) > 500
    diff = ImageChops.difference(novo, antigo)
    assert diff.getbbox() is None, f"imagens diferem na região {diff.getbbox()}"


# ---------------------------------------------------------------------------
# WEB-OV-04 animação
# ---------------------------------------------------------------------------

def test_web_ov_04_mesma_estrutura_so_troca_o_transform_do_digito(page: Page, api, base_url):
    api.subscribers = leitura("460600")
    freeze_clock(page)
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("460600"), "animação inicial")
    page.evaluate("window.__inners = [...document.querySelectorAll('.digito-inner')]")
    antes = transforms(page)

    api.subscribers = leitura("460601")
    page.clock.run_for(5000)
    wait_until(lambda: transforms(page)[5] != antes[5], "último dígito mudou")

    depois = transforms(page)
    assert depois[:5] == antes[:5]
    assert depois[5] == "translateY(-1em)"
    assert transitions(page)[5] == f"transform 0.45s {CURVA}"
    # os mesmos elementos: nada foi recriado
    assert page.evaluate("[...document.querySelectorAll('.digito-inner')].every((e, i) => e === window.__inners[i])")
    expect(page.locator(".digito-wrap")).to_have_count(6)


def test_web_ov_04b_estrutura_diferente_reconstroi_com_atraso_de_40ms_por_digito(page: Page, api, base_url):
    api.subscribers = leitura("99999")
    freeze_clock(page)
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(5)
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("99999"), "animação inicial")
    page.evaluate("window.__inners = [...document.querySelectorAll('.digito-inner')]")

    api.subscribers = leitura("100000")
    page.clock.run_for(5000)
    expect(page.locator(".digito-wrap")).to_have_count(6)  # reconstruiu
    assert page.evaluate("[...document.querySelectorAll('.digito-inner')].some((e, i) => e === window.__inners[i])") is False
    # recém-construído: tudo parado no valor final, sem transição, até os atrasos vencerem.
    # O dígito 0 tem atraso 0 e a resposta do poll pode chegar ainda DENTRO do run_for(5000)
    # (corrida entre a rede e o relógio falso, que depende da latência do servidor): o
    # setTimeout(0) dele pode ou não já ter disparado. Os demais (40 ms em diante) nunca.
    assert transforms(page) == esperado("100000")
    assert transitions(page)[0] in ("none", f"transform 0.5s {CURVA}")
    assert transitions(page)[1:] == ["none"] * 5

    anim = f"transform 0.5s {CURVA}"
    page.clock.run_for(1)  # t = 1 ms: dígito 0 (atraso 0)
    assert transitions(page) == [anim] + ["none"] * 5
    page.clock.run_for(39)  # t = 40 ms: dígito 1
    assert transitions(page) == [anim] * 2 + ["none"] * 4
    page.clock.run_for(39)  # t = 79 ms: ainda falta o dígito 2 (80 ms)
    assert transitions(page) == [anim] * 2 + ["none"] * 4
    page.clock.run_for(1)  # t = 80 ms
    assert transitions(page) == [anim] * 3 + ["none"] * 3
    page.clock.run_for(120)  # t = 200 ms: último dígito (5 * 40)
    assert transitions(page) == [anim] * 6
    assert transforms(page) == esperado("100000")


def test_web_ov_04c_carga_inicial_anima_com_atraso_de_50_mais_60ms_por_digito(page: Page, api, base_url):
    api.subscribers = leitura("460600")
    freeze_clock(page)
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    assert transforms(page) == ["translateY(0px)"] * 6  # parados no zero (o Chromium serializa 0 como 0px)
    assert transitions(page) == ["none"] * 6
    anim = f"transform 0.5s {CURVA}"
    page.clock.run_for(49)
    assert transitions(page) == ["none"] * 6
    page.clock.run_for(1)  # 50 ms: dígito 0
    assert transitions(page) == [anim] + ["none"] * 5
    assert transforms(page)[0] == "translateY(-4em)"
    page.clock.run_for(59)  # 109 ms
    assert transitions(page) == [anim] + ["none"] * 5
    page.clock.run_for(1)  # 110 ms: dígito 1 (50 + 60)
    assert transitions(page) == [anim] * 2 + ["none"] * 4
    page.clock.run_for(500)  # 610 ms: 50 + 5*60 = 350 já passou
    assert transitions(page) == [anim] * 6
    assert transforms(page) == esperado("460600")


# ---------------------------------------------------------------------------
# WEB-OV-05 polling
# ---------------------------------------------------------------------------

def test_web_ov_05_polling_a_cada_5000ms_com_parametro_t(page: Page, api, base_url):
    api.subscribers = leitura("460600")
    freeze_clock(page)
    page.goto(f"{base_url}/contador")
    wait_until(lambda: n_polls(api) == 1, "requisição da carga")
    agora = page.evaluate("Date.now()")
    primeiro = api.requests("GET", "/api/public/subscribers")[0]
    assert primeiro.query == f"t={agora}"  # ?t=<Date.now()> (relógio falso, parado)

    page.clock.run_for(4999)
    page.wait_for_timeout(300)
    assert n_polls(api) == 1
    page.clock.run_for(1)
    wait_until(lambda: n_polls(api) == 2, "segunda requisição aos 5000 ms")
    page.clock.run_for(5000)
    wait_until(lambda: n_polls(api) == 3, "terceira requisição aos 10000 ms")
    t = [int(r.query.removeprefix("t=")) for r in api.requests("GET", "/api/public/subscribers")]
    assert t[1] - t[0] == 5000 and t[2] - t[1] == 5000
    assert all(r.path == "/api/public/subscribers" for r in api.requests("GET", "/api/public/subscribers"))
    assert not any(r.path.endswith("inscritos.json") for r in api.log)


def test_web_ov_05b_resposta_nao_ok_e_valor_igual_sao_ignorados_sem_erro(page: Page, api, base_url, watch: Watch):
    api.subscribers = leitura("460600")
    freeze_clock(page)
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("460600"), "animação inicial")
    page.evaluate("window.__inners = [...document.querySelectorAll('.digito-inner')]")
    antes = (transforms(page), transitions(page))

    api.subscribers_status = 503  # 503 previsto no contrato
    page.clock.run_for(5000)
    wait_until(lambda: n_polls(api) == 2, "poll com 503")
    assert api.requests("GET", "/api/public/subscribers")[-1].status == 503

    api.subscribers_status = 200  # mesmo valor de antes
    page.clock.run_for(5000)
    wait_until(lambda: n_polls(api) == 3, "poll com valor igual")
    page.wait_for_timeout(300)

    assert (transforms(page), transitions(page)) == antes
    assert page.evaluate("[...document.querySelectorAll('.digito-inner')].every((e, i) => e === window.__inners[i])")
    assert [t for t, _ in watch.console if t == "warning"] == []  # nenhum console.warn
    # o único ruído aceitável é o log do próprio Chromium para o HTTP 503
    erros = [txt for t, txt in watch.console if t == "error"]
    assert all(e.startswith("Failed to load resource") for e in erros), erros
    assert watch.page_errors == []


def test_web_ov_05c_valor_vazio_rede_ou_json_quebrado_so_geram_warn(page: Page, api, base_url, watch: Watch):
    """Defesa: respostas FORA do contrato (o dublê não as produz), via interceptação do navegador."""
    api.subscribers = leitura("460600")
    freeze_clock(page)
    corpo: dict[str, object] = {"modo": "ok", "n": 0}

    def handler(route):
        corpo["n"] = int(corpo["n"]) + 1
        if corpo["modo"] == "vazio":
            route.fulfill(status=200, content_type="application/json",
                          body='{"inscritos":"","raw":"","atualizado_em":"2026-11-02T14:32:10.654321Z"}')
        elif corpo["modo"] == "rede":
            route.abort()
        elif corpo["modo"] == "quebrado":
            route.fulfill(status=200, content_type="application/json", body="isto nao e json")
        else:
            route.fallback()

    page.route("**/api/public/subscribers*", handler)
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("460600"), "animação inicial")
    avisos_antes = len([1 for t, _ in watch.console if t == "warning"])

    def avisos_agora() -> int:
        return len([1 for t, _ in watch.console if t == "warning"])

    corpo["modo"] = "vazio"
    chamadas = int(corpo["n"])
    page.clock.run_for(5000)
    wait_until(lambda: int(corpo["n"]) == chamadas + 1, "poll com valor vazio", page=page)
    page.wait_for_timeout(300)
    assert avisos_agora() == avisos_antes  # vazio: ignorado em silêncio
    assert transforms(page) == esperado("460600")

    for modo in ("rede", "quebrado"):
        corpo["modo"] = modo
        n = avisos_agora()
        chamadas = int(corpo["n"])
        page.clock.run_for(5000)
        wait_until(lambda: int(corpo["n"]) == chamadas + 1, f"poll ({modo})", page=page)
        wait_until(lambda: avisos_agora() == n + 1, f"console.warn ({modo}): {watch.console}", page=page)
    avisos = [txt for t, txt in watch.console if t == "warning"]
    assert all("Erro ao buscar inscritos" in a for a in avisos[avisos_antes:])
    assert transforms(page) == esperado("460600")  # o contador continua mostrando o último valor
    assert watch.page_errors == []


# ---------------------------------------------------------------------------
# WEB-OV-06 HTML do overlay
# ---------------------------------------------------------------------------

def test_web_ov_06_html_sem_inline_e_assets_do_mesmo_dominio_em_overlay():
    html = (STATIC / "overlay" / "contador.html").read_text(encoding="utf-8")
    assert "<title>Inscritos</title>" in html
    assert 'lang="pt-BR"' in html
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)  # nenhum script inline
    assert not re.search(r"<style|\sstyle\s*=|\son[a-z]+\s*=", html, re.I)
    assert re.findall(r'<script[^>]*src="([^"]+)"', html) == ["/overlay/contador.js"]
    assert re.findall(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"', html) == ["/overlay/contador.css"]
    assert (STATIC / "overlay" / "contador.css").is_file() and (STATIC / "overlay" / "contador.js").is_file()
    css = (STATIC / "overlay" / "contador.css").read_text(encoding="utf-8")
    assert re.search(r"body\s*\{[^}]*background:\s*transparent", css)


@pytest.mark.parametrize("path", PATHS)
def test_web_ov_06b_pagina_servida_e_transparente_e_embutivel(page: Page, api, base_url, path):
    api.subscribers = leitura("460600")
    resp = page.goto(f"{base_url}{path}")
    assert resp is not None and resp.status == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "frame-ancestors *" in resp.headers["content-security-policy"]
    assert "x-frame-options" not in resp.headers
    assert page.title() == "Inscritos"
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgba(0, 0, 0, 0)"
    assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == "rgba(0, 0, 0, 0)"
    srcs = page.evaluate("[...document.querySelectorAll('script[src], link[rel=stylesheet]')].map((e) => e.src || e.href)")
    assert sorted(srcs) == [f"{base_url}/overlay/contador.css", f"{base_url}/overlay/contador.js"]
    expect(page.locator(".digito-wrap")).to_have_count(6)

    # embutível: uma página de OUTRA origem (outro host/porta, servidor real)
    # carrega o overlay num iframe. frame-ancestors * só casa pais http(s).
    html = f'<iframe id="f" src="{base_url}{path}" width="900" height="200"></iframe>'.encode()

    class Hospedeiro(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    servidor = ThreadingHTTPServer(("127.0.0.1", 0), Hospedeiro)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    host = page.context.new_page()
    try:
        host.goto(f"http://localhost:{servidor.server_address[1]}/")
        expect(host.frame_locator("#f").locator(".digito-wrap")).to_have_count(6)
    finally:
        host.close()
        servidor.shutdown()
        servidor.server_close()


def test_web_ov_07_overlay_inscritos_foi_removido_so_o_contador_existe(page: Page, api, base_url):
    """D36: um só overlay de inscritos. /overlay/inscritos devolve 404 (como qualquer rota inexistente),
    /contador segue servindo o overlay."""
    for path in ("/overlay/inscritos", "/overlay/inscritos/"):
        resp = page.goto(f"{base_url}{path}")
        assert resp is not None and resp.status == 404, (path, resp and resp.status)
        assert page.locator(".digito-wrap").count() == 0
    api.subscribers = leitura("460.600")
    resp = page.goto(f"{base_url}/contador")
    assert resp is not None and resp.status == 200
    expect(page.locator(".digito-wrap")).to_have_count(6)
