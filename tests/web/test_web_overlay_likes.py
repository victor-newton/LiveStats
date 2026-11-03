"""WEB-LIKE-06..14 (D34): overlay de likes com meta e barra colorida (/overlay/live.html?slot=N)."""

from __future__ import annotations

import json

import pytest
from api_double import CSP_OVERLAY
from playwright.sync_api import Page, Route, expect
from web_helpers import freeze_clock, wait_until

CURVA = "cubic-bezier(0.25, 0.46, 0.45, 0.94)"
PATH = "/overlay/live.html"


def n_polls(api, slot: int = 1) -> int:
    return len(api.requests("GET", f"/api/public/lives/{slot}"))


def transforms(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => e.style.transform)")


def transitions(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => e.style.transition)")


def esperado(digitos: str) -> list[str]:
    # O Chromium serializa "-0em" como "0em"; os demais ficam como "-<d>em".
    return [f"translateY({'-' if d != '0' else ''}{d}em)" for d in digitos if d.isdigit()]


def texto(page: Page) -> str:
    return page.evaluate("document.getElementById('contagem').getAttribute('aria-label')")


def separadores(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('#contagem .separador')].map((e) => e.textContent)")


def largura_pct(page: Page) -> str:
    return page.evaluate("document.getElementById('barra-preenchimento').style.width")


def configura(api, slot: int = 1, likes: int = 1000, goal=10000, color="#00AA11") -> None:
    api.set_stats(slot, api.live_stats(slot, likes=likes))
    api.goals[slot - 1] = goal
    api.colors[slot - 1] = color


# ---------------------------------------------------------------------------
# WEB-LIKE-06 formato likes/meta, separador de milhar e dígitos
# ---------------------------------------------------------------------------

def test_web_like_06_mostra_likes_barra_meta_com_separador_de_milhar(page: Page, api, base_url):
    configura(api, likes=1000, goal=10000)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator("#overlay-likes")).to_be_visible()
    wait_until(lambda: transforms(page) == esperado("1.000/10.000"), "dígitos de 1.000/10.000")
    assert texto(page) == "1.000/10.000"
    assert separadores(page) == [".", "/", "."]
    # mesma estrutura do contador de inscritos: 9 dígitos, cada um com 10 spans
    expect(page.locator(".digito-wrap")).to_have_count(9)
    spans = page.locator(".digito-inner").first.locator("span")
    assert spans.all_inner_texts() == [str(d) for d in range(10)]
    # lido como imagem com nome acessível; o carrossel em si fica oculto para leitores de tela
    assert page.get_attribute("#contagem", "role") == "img"
    assert page.get_attribute(".digito-wrap", "aria-hidden") == "true"


@pytest.mark.parametrize(
    "likes, goal, esperado_texto",
    [
        (0, 50, "0/50"),
        (999, 1000, "999/1.000"),
        (1234567, 2000000, "1.234.567/2.000.000"),
        (56, 1_000_000_000, "56/1.000.000.000"),
    ],
)
def test_web_like_06b_formatos_de_milhar(page: Page, api, base_url, likes, goal, esperado_texto):
    configura(api, likes=likes, goal=goal)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: transforms(page) == esperado(esperado_texto), f"dígitos de {esperado_texto}")
    assert texto(page) == esperado_texto


def test_web_like_06c_cada_slot_le_a_propria_live(page: Page, api, base_url):
    configura(api, slot=2, likes=222, goal=300, color="#0000FF")
    configura(api, slot=3, likes=333, goal=400, color="#FF00FF")
    page.goto(f"{base_url}{PATH}?slot=3")
    wait_until(lambda: texto(page) == "333/400", "slot 3")
    assert n_polls(api, 3) >= 1 and n_polls(api, 2) == 0 and n_polls(api, 1) == 0
    assert page.evaluate("getComputedStyle(document.getElementById('overlay-likes')).getPropertyValue('--cor-barra')") == "#FF00FF"


def test_web_like_06d_sem_meta_mostra_so_os_likes_e_nenhuma_barra(page: Page, api, base_url):
    configura(api, likes=1234, goal=None)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: transforms(page) == esperado("1.234"), "dígitos de 1.234")
    assert texto(page) == "1.234"
    assert separadores(page) == ["."]
    expect(page.locator("#barra")).to_be_hidden()
    expect(page.locator("#barra-preenchimento")).to_be_hidden()
    assert page.locator("#barra").bounding_box() is None


# ---------------------------------------------------------------------------
# WEB-LIKE-07 barra: largura e cor
# ---------------------------------------------------------------------------

def test_web_like_07_barra_tem_largura_likes_sobre_meta_e_a_cor_do_slot(page: Page, api, base_url):
    configura(api, likes=2500, goal=10000, color="#00AA11")
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: largura_pct(page) == "25%", "largura 25%")
    barra = page.locator("#barra")
    expect(barra).to_be_visible()
    # medida real: 25% do trilho
    trilho = barra.bounding_box()
    preench = page.locator("#barra-preenchimento").bounding_box()
    assert trilho and preench
    page.wait_for_timeout(700)  # termina a transição de 0.5 s da largura
    preench = page.locator("#barra-preenchimento").bounding_box()
    assert preench and abs(preench["width"] - trilho["width"] * 0.25) < 1.5
    # cor: variável CSS aplicada por CSSOM e usada como fundo da barra
    assert page.evaluate("document.getElementById('overlay-likes').style.getPropertyValue('--cor-barra')") == "#00AA11"
    assert page.evaluate("getComputedStyle(document.getElementById('barra-preenchimento')).backgroundColor") == "rgb(0, 170, 17)"
    # fina e embaixo
    assert trilho["height"] <= 12
    viewport = page.viewport_size
    assert viewport and abs((trilho["y"] + trilho["height"]) - viewport["height"]) < 1.5


def test_web_like_07b_largura_fracionaria(page: Page, api, base_url):
    configura(api, likes=56, goal=10000)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: largura_pct(page) == "0.56%", "largura 0.56%")


def test_web_like_07c_passou_da_meta_a_barra_fica_em_100_por_cento(page: Page, api, base_url):
    configura(api, likes=12000, goal=10000)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: largura_pct(page) == "100%", "largura 100%")
    assert texto(page) == "12.000/10.000"  # o número continua exato
    page.wait_for_timeout(700)
    trilho = page.locator("#barra").bounding_box()
    preench = page.locator("#barra-preenchimento").bounding_box()
    assert trilho and preench and abs(preench["width"] - trilho["width"]) < 1.5


def test_web_like_07d_exatamente_na_meta_tambem_100(page: Page, api, base_url):
    configura(api, likes=10000, goal=10000)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: largura_pct(page) == "100%", "largura 100%")


# ---------------------------------------------------------------------------
# WEB-LIKE-08 animação igual à do contador e polling de 5 s
# ---------------------------------------------------------------------------

def test_web_like_08_carga_inicial_anima_com_atraso_de_50_mais_60ms_por_digito(page: Page, api, base_url):
    configura(api, likes=460600, goal=None)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    assert transforms(page) == ["translateY(0px)"] * 6
    assert transitions(page) == ["none"] * 6
    anim = f"transform 0.5s {CURVA}"
    page.clock.run_for(49)
    assert transitions(page) == ["none"] * 6
    page.clock.run_for(1)  # t = 50 ms: primeiro dígito
    assert transitions(page) == [anim] + ["none"] * 5
    page.clock.run_for(60)  # t = 110 ms: segundo dígito
    assert transitions(page) == [anim] * 2 + ["none"] * 4
    page.clock.run_for(300)  # t = 410 ms: o último (50 + 5*60 = 350)
    assert transitions(page) == [anim] * 6
    assert transforms(page) == esperado("460600")


def test_web_like_08b_mudanca_de_likes_so_troca_o_transform_com_0_45s(page: Page, api, base_url):
    configura(api, likes=460600, goal=500000)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator(".digito-wrap")).to_have_count(12)
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("460.600/500.000"), "animação inicial")
    page.evaluate("window.__inners = [...document.querySelectorAll('.digito-inner')]")
    antes = transforms(page)

    api.set_stats(1, api.live_stats(1, likes=460601))
    page.clock.run_for(5000)
    wait_until(lambda: transforms(page)[5] != antes[5], "último dígito dos likes mudou")
    depois = transforms(page)
    assert depois[:5] == antes[:5] and depois[6:] == antes[6:]
    assert depois[5] == "translateY(-1em)"
    assert transitions(page)[5] == f"transform 0.45s {CURVA}"
    # os mesmos elementos: nada foi recriado
    assert page.evaluate("[...document.querySelectorAll('.digito-inner')].every((e, i) => e === window.__inners[i])")
    assert texto(page) == "460.601/500.000"


def test_web_like_08c_estrutura_diferente_reconstroi_com_atraso_de_40ms_por_digito(page: Page, api, base_url):
    configura(api, likes=999, goal=None)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator(".digito-wrap")).to_have_count(3)
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("999"), "animação inicial")

    api.set_stats(1, api.live_stats(1, likes=1000))  # 999 -> 1.000 ganha dígito e separador
    page.clock.run_for(5000)
    expect(page.locator(".digito-wrap")).to_have_count(4)
    assert separadores(page) == ["."]
    assert transforms(page) == esperado("1000")
    assert transitions(page)[1:] == ["none"] * 3  # só o dígito 0 (atraso 0) pode já ter disparado
    anim = f"transform 0.5s {CURVA}"
    page.clock.run_for(1)
    assert transitions(page) == [anim] + ["none"] * 3
    page.clock.run_for(39)
    assert transitions(page) == [anim] * 2 + ["none"] * 2
    page.clock.run_for(80)
    assert transitions(page) == [anim] * 4


def test_web_like_08d_polling_a_cada_5s_em_api_public_lives(page: Page, api, base_url):
    configura(api, likes=10, goal=100)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: n_polls(api) == 1, "1ª consulta")
    page.clock.run_for(4999)
    page.wait_for_timeout(300)
    assert n_polls(api) == 1
    page.clock.run_for(1)
    wait_until(lambda: n_polls(api) == 2, "2ª consulta em 5 s")
    page.clock.run_for(5000)
    wait_until(lambda: n_polls(api) == 3, "3ª consulta")
    assert all(r.path == "/api/public/lives/1" for r in api.log if r.path.startswith("/api/"))


def test_web_like_08e_meta_ou_cor_mudando_no_painel_chega_ao_overlay(page: Page, api, base_url):
    configura(api, likes=500, goal=1000, color="#00AA11")
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: largura_pct(page) == "50%", "50%")
    api.goals[0] = 2000
    api.colors[0] = "#FF8800"
    page.clock.run_for(5000)
    wait_until(lambda: largura_pct(page) == "25%", "25% com a nova meta")
    assert texto(page) == "500/2.000"
    assert page.evaluate("document.getElementById('overlay-likes').style.getPropertyValue('--cor-barra')") == "#FF8800"
    api.goals[0] = None  # meta removida: some a barra e o texto fica só com os likes
    page.clock.run_for(5000)
    wait_until(lambda: texto(page) == "500", "só likes")
    expect(page.locator("#barra")).to_be_hidden()


# ---------------------------------------------------------------------------
# WEB-LIKE-09 slot inválido ou ausente: nada exibido
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "query",
    ["", "?", "?slot=", "?slot=0", "?slot=5", "?slot=-1", "?slot=abc", "?slot=1abc", "?slot=01", "?slot=1.0",
     "?slot=%201", "?slot=1&slot=2", "?SLOT=1", "?slot[]=1", "?slot=%3Cimg%20src%3Dx%3E", "?slot=99999999999999999999"],
)
def test_web_like_09_slot_invalido_nao_exibe_nada_nem_consulta_a_api(page: Page, api, base_url, query):
    configura(api, slot=1, likes=1000, goal=10000)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}{query}")
    page.clock.run_for(12000)  # dois ciclos de polling, se houvesse
    page.wait_for_timeout(200)
    expect(page.locator("#overlay-likes")).to_be_hidden()
    assert page.locator(".digito-wrap").count() == 0
    assert page.evaluate("document.body.innerText.trim()") == ""
    assert [r for r in api.log if r.path.startswith("/api/")] == []
    # nada de HTML injetado a partir do parâmetro
    assert page.evaluate("document.body.querySelectorAll('img, svg, iframe, script:not([src])').length") == 0


# ---------------------------------------------------------------------------
# WEB-LIKE-10 erros: mantém o último valor
# ---------------------------------------------------------------------------

def test_web_like_10_erro_de_rede_mantem_o_ultimo_valor(page: Page, api, base_url):
    configura(api, likes=1000, goal=10000)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator(".digito-wrap")).to_have_count(9)  # a 1ª resposta já chegou
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado("1.000/10.000"), "valor inicial")
    antes = n_polls(api)

    page.route("**/api/public/lives/1*", lambda route: route.abort("connectionrefused"))
    api.set_stats(1, api.live_stats(1, likes=2000))
    page.clock.run_for(5000)
    page.wait_for_timeout(300)
    assert texto(page) == "1.000/10.000" and transforms(page) == esperado("1.000/10.000")
    assert largura_pct(page) == "10%"
    expect(page.locator("#overlay-likes")).to_be_visible()
    assert n_polls(api) == antes  # a requisição nem chegou ao servidor

    # a rede volta: o overlay se atualiza sozinho
    page.unroute("**/api/public/lives/1*")
    page.clock.run_for(5000)
    wait_until(lambda: texto(page) == "2.000/10.000", "recuperou")


def test_web_like_10b_http_503_ou_json_estranho_mantem_o_ultimo_valor(page: Page, api, base_url):
    from api_double import error

    configura(api, likes=1000, goal=10000)
    freeze_clock(page)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator(".digito-wrap")).to_have_count(9)  # a 1ª resposta já chegou
    page.clock.run_for(1000)
    wait_until(lambda: texto(page) == "1.000/10.000", "valor inicial")

    api.override("GET", "/api/public/lives/1", error(503, "Dados ainda não disponíveis"))
    page.clock.run_for(5000)
    wait_until(lambda: any(r.status == 503 for r in api.requests("GET", "/api/public/lives/1")), "503 servido")
    assert texto(page) == "1.000/10.000"
    expect(page.locator("#overlay-likes")).to_be_visible()

    def lixo(route: Route) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"likes": "muitos", "goal": 5}))

    page.route("**/api/public/lives/1*", lixo)
    page.clock.run_for(5000)
    page.wait_for_timeout(300)
    assert texto(page) == "1.000/10.000"


def test_web_like_10c_antes_do_primeiro_dado_nada_aparece(page: Page, api, base_url):
    # sem estatísticas do worker (503): a página fica vazia e transparente, sem erro
    api.goals[0] = 5000
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: n_polls(api) >= 1, "consulta")
    page.wait_for_timeout(200)
    expect(page.locator("#overlay-likes")).to_be_hidden()
    assert page.locator(".digito-wrap").count() == 0


# ---------------------------------------------------------------------------
# WEB-LIKE-11 transparência e aparência
# ---------------------------------------------------------------------------

def test_web_like_11_fundo_transparente_e_texto_branco(page: Page, api, base_url):
    configura(api, likes=1234, goal=5000)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: texto(page) == "1.234/5.000", "valor")
    for seletor in ("html", "body", "#overlay-likes"):
        assert page.evaluate(f"getComputedStyle(document.querySelector('{seletor}')).backgroundColor") == "rgba(0, 0, 0, 0)", seletor
    assert page.evaluate("getComputedStyle(document.getElementById('contagem')).color") == "rgb(255, 255, 255)"
    # mesmos números do contador de inscritos
    assert page.evaluate("getComputedStyle(document.getElementById('contagem')).fontSize") == "80px"
    assert page.evaluate("getComputedStyle(document.getElementById('contagem')).fontWeight") == "700"
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")


def test_web_like_11b_cabe_em_fonte_de_obs_1920x1080_e_pequena(page: Page, api, base_url):
    configura(api, likes=999999, goal=1_000_000_000)
    for largura, altura in ((1920, 1080), (1280, 720)):
        page.set_viewport_size({"width": largura, "height": altura})
        page.goto(f"{base_url}{PATH}?slot=1")
        wait_until(lambda: texto(page) == "999.999/1.000.000.000", "valor")
        box = page.locator("#contagem").bounding_box()
        assert box and box["y"] >= 0 and box["y"] + box["height"] <= altura + 1


# ---------------------------------------------------------------------------
# WEB-LIKE-12 CSP e terceiros
# ---------------------------------------------------------------------------

def test_web_like_12_cabecalhos_csp_do_overlay_e_nenhuma_requisicao_externa(page: Page, api, base_url, watch):
    configura(api, likes=1000, goal=10000)
    resposta = page.goto(f"{base_url}{PATH}?slot=1")
    assert resposta is not None
    assert resposta.headers["content-security-policy"] == CSP_OVERLAY
    assert "x-frame-options" not in resposta.headers  # embutível no OBS (D11)
    wait_until(lambda: texto(page) == "1.000/10.000", "valor")
    page.wait_for_timeout(200)
    caminhos = {page.evaluate("(u) => new URL(u).pathname", u) for u in watch.requests if not u.startswith("data:")}
    assert caminhos == {PATH, "/overlay/live.css", "/overlay/live.js", "/overlay/carousel.js", "/api/public/lives/1"}
    # a fixture `page` falha o teste se houver violação de CSP ou requisição a terceiros


def test_web_like_12b_cor_estranha_vinda_da_rede_nao_vira_estilo_nem_requisicao(page: Page, api, base_url, watch):
    corpo = {**api.live_stats(1, likes=10), "goal": 100, "color": "red;background:url(http://evil.example/x.png)"}
    page.route("**/api/public/lives/1*", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(corpo)))
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: texto(page) == "10/100", "valor")
    # a cor inválida é descartada: vale o padrão, e nada foi pedido a evil.example
    assert page.evaluate("document.getElementById('overlay-likes').style.getPropertyValue('--cor-barra')") == "#FF0033"
    assert page.evaluate("getComputedStyle(document.getElementById('barra-preenchimento')).backgroundColor") == "rgb(255, 0, 51)"
    assert not [u for u in watch.requests if "evil.example" in u]
    assert page.evaluate("document.getElementById('overlay-likes').getAttribute('style')") == "--cor-barra: #FF0033;"
    assert page.evaluate("document.body.querySelectorAll('img, svg, iframe, script:not([src]), style').length") == 0
