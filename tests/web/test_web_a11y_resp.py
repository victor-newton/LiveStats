"""WEB-A11Y-01 (acessibilidade) e WEB-RESP-01 (layout em 375 px)."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from web_helpers import do_login

SEM_NOME = """() => [...document.querySelectorAll('input, textarea, select, button')]
    .filter((e) => e.offsetParent !== null || e.getClientRects().length)
    .filter((e) => {
        const nome = (e.labels && e.labels.length ? e.labels[0].textContent : '')
            || e.getAttribute('aria-label') || e.getAttribute('aria-labelledby') || e.textContent;
        return !nome || !nome.trim();
    })
    .map((e) => e.outerHTML.slice(0, 80))"""

FOCO = """() => {
    const cs = getComputedStyle(document.activeElement);
    return {tag: document.activeElement.tagName, style: cs.outlineStyle, width: cs.outlineWidth};
}"""


def test_web_a11y_01_login_com_rotulos_foco_visivel_e_teclado(page: Page, api, base_url):
    page.goto(f"{base_url}/")
    expect(page.locator("#loginView")).to_be_visible()
    assert page.get_attribute("html", "lang") == "pt-BR"
    assert page.evaluate(SEM_NOME) == []
    expect(page.get_by_label("Usuário")).to_have_attribute("autocomplete", "username")
    expect(page.get_by_label("Senha")).to_have_attribute("autocomplete", "current-password")

    # navegação só por teclado, com anel de foco visível em cada parada
    page.keyboard.press("Tab")
    foco = page.evaluate(FOCO)
    assert (foco["tag"], foco["style"]) == ("INPUT", "solid") and foco["width"] != "0px"
    assert page.evaluate("document.activeElement.id") == "loginUser"
    page.keyboard.type(api.username)
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "loginPass"
    page.keyboard.type(api.password)
    page.keyboard.press("Tab")
    foco = page.evaluate(FOCO)
    assert page.evaluate("document.activeElement.id") == "loginBtn"
    assert foco["style"] == "solid" and foco["width"] != "0px"
    page.keyboard.press("Enter")
    expect(page.locator("#panelView")).to_be_visible()


def test_web_a11y_01b_painel_com_rotulos_e_operavel_pelo_teclado(page: Page, api, base_url):
    api.set_stats(1, api.live_stats(1))
    do_login(page, base_url, api)
    assert page.evaluate(SEM_NOME) == []
    for n in range(1, 5):
        assert page.locator(f"label[for='inp{n}']").text_content() == f"Slot {n}"
    expect(page.get_by_label("JSON de cookies do EditThisCookie")).to_be_visible()
    # há títulos de seção e regiões com nome
    assert page.get_by_role("heading", level=2).count() >= 3
    # D35: Lives, Saúde, Configurações, Inscritos, Cookies e Endpoints (antes eram 3 seções)
    assert page.locator("#panelView section[aria-labelledby]").count() == 6
    # mensagens de status são anunciadas
    for sel, role in (("#saveMsg", "status"), ("#saveErr", "alert"), ("#cookiesMsg", "status"), ("#authAlert", "alert")):
        assert page.get_attribute(sel, "role") == role

    # preencher o slot 1 e salvar usando só o teclado
    page.get_by_label("Slot 1", exact=True).focus()
    foco = page.evaluate(FOCO)
    assert foco["style"] == "solid" and foco["width"] != "0px"
    page.keyboard.type("dQw4w9WgXcQ")
    paradas = []
    for _ in range(12):  # D34: cada slot ganhou meta e cor (3 paradas por slot)
        page.keyboard.press("Tab")
        paradas.append(page.evaluate("document.activeElement.id"))
        if paradas[-1] == "saveBtn":
            break
    assert paradas[-1] == "saveBtn", paradas
    assert paradas[:3] == ["goal1", "color1", "inp2"], paradas  # meta e cor são alcançáveis pelo teclado
    foco = page.evaluate(FOCO)
    assert foco["style"] == "solid" and foco["width"] != "0px"
    page.keyboard.press("Enter")
    expect(page.locator("#saveMsg")).to_be_visible()
    assert api.requests("PUT", "/api/admin/slots")[0].body["slots"][0]["url"] == "dQw4w9WgXcQ"


def test_web_resp_01_largura_375_sem_rolagem_horizontal(page: Page, api, base_url):
    page.set_viewport_size({"width": 375, "height": 812})
    longo = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=" + "x" * 150
    api.public_base_url = "https://um-dominio-bem-comprido.exemplo.com.br/com/um/caminho/enorme/tambem"
    api.slots = [longo, longo, "", ""]
    for n in (1, 2):
        api.set_stats(n, api.live_stats(n, titulo="Um título de vídeo muito comprido " * 8, views=12_345_678))
    api.subscribers = {"inscritos": "460600", "raw": "460600", "atualizado_em": "2026-11-02T14:32:10.654321Z"}
    api.inscritos_worker = {
        **api.worker_state("error", last_error_code="http_error", last_error_message="mensagem " * 20),
        "auth_state": "login_required",
    }

    page.goto(f"{base_url}/")
    sem_rolagem = "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    assert page.evaluate(sem_rolagem), "login com rolagem horizontal"

    do_login(page, base_url, api)
    expect(page.locator(".slot-card")).to_have_count(4)
    assert page.evaluate(sem_rolagem), "painel com rolagem horizontal"
    colunas = page.evaluate("getComputedStyle(document.getElementById('slotsGrid')).gridTemplateColumns")
    assert len(colunas.split()) == 1, colunas  # 1 coluna abaixo de 600 px
    cards = page.locator(".slot-card")
    larguras = [cards.nth(i).bounding_box()["width"] for i in range(4)]
    assert all(w <= 375 for w in larguras), larguras
    # nada ultrapassa a janela
    fora = page.evaluate(
        """() => [...document.querySelectorAll('#panelView *')]
            .filter((e) => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
            .map((e) => e.className || e.tagName)"""
    )
    assert fora == []


def test_web_resp_01b_largura_larga_usa_duas_colunas(page: Page, api, base_url):
    do_login(page, base_url, api)
    colunas = page.evaluate("getComputedStyle(document.getElementById('slotsGrid')).gridTemplateColumns")
    assert len(colunas.split()) == 2, colunas
