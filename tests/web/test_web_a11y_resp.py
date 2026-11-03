"""WEB-A11Y-01 (acessibilidade) e WEB-RESP-01 (layout em 375 px)."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from web_helpers import do_login, open_tab

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
    for tab in ("inscritos", "lives", "saude"):  # D36: todos os campos visíveis de cada aba têm nome
        open_tab(page, tab)
        assert page.evaluate(SEM_NOME) == [], tab
    open_tab(page, "lives")
    for n in range(1, 5):
        assert page.locator(f"label[for='inp{n}']").text_content() == f"Slot {n}"
    open_tab(page, "inscritos")
    expect(page.get_by_label("JSON de cookies do EditThisCookie")).to_be_visible()
    # há títulos de seção e regiões com nome: 3 painéis de aba + todos os cartões apontam para um título
    assert page.get_by_role("heading", level=2).count() >= 3
    assert page.locator("#panelView [role=tabpanel][aria-labelledby]").count() == 3
    sem_titulo = page.evaluate(
        """() => [...document.querySelectorAll('#panelView .card')]
            .filter((c) => { const t = document.getElementById(c.getAttribute('aria-labelledby') || ''); return !t || !t.textContent.trim(); })
            .map((c) => c.className)"""
    )
    assert sem_titulo == []
    # mensagens de status são anunciadas
    for sel, role in (("#saveMsg1", "status"), ("#saveErr1", "alert"), ("#saveMsg4", "status"), ("#cookiesMsg", "status"), ("#authAlert", "alert")):
        assert page.get_attribute(sel, "role") == role

    # preencher o slot 1 e salvar usando só o teclado
    open_tab(page, "lives")
    page.get_by_label("Slot 1", exact=True).focus()
    foco = page.evaluate(FOCO)
    assert foco["style"] == "solid" and foco["width"] != "0px"
    page.keyboard.type("dQw4w9WgXcQ")
    paradas = []
    for _ in range(12):  # cada cartão: meta, cor da barra, fonte, cor do texto e Salvar
        page.keyboard.press("Tab")
        paradas.append(page.evaluate("document.activeElement.id"))
        if paradas[-1] == "saveBtn1":
            break
    assert paradas == ["goal1", "color1", "font-1", "textcolor-1", "saveBtn1"], paradas  # tudo alcançável pelo teclado
    foco = page.evaluate(FOCO)
    assert foco["style"] == "solid" and foco["width"] != "0px"
    page.keyboard.press("Enter")
    expect(page.locator("#saveMsg1")).to_be_visible()
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
    for tab in ("inscritos", "lives", "saude"):  # D36: a regra vale para cada aba
        open_tab(page, tab)
        assert page.evaluate(sem_rolagem), f"aba {tab} com rolagem horizontal"
        fora = page.evaluate(
            """() => [...document.querySelectorAll('#panelView *')]
                .filter((e) => e.getBoundingClientRect().right > document.documentElement.clientWidth + 1)
                .map((e) => e.className || e.tagName)"""
        )
        assert fora == [], (tab, fora)  # nada ultrapassa a janela
    open_tab(page, "lives")
    colunas = page.evaluate("getComputedStyle(document.getElementById('slotsGrid')).gridTemplateColumns")
    assert len(colunas.split()) == 1, colunas  # 1 coluna no celular
    cards = page.locator(".slot-card")
    larguras = [cards.nth(i).bounding_box()["width"] for i in range(4)]
    assert all(w <= 375 for w in larguras), larguras


def test_web_resp_01b_largura_larga_usa_duas_colunas(page: Page, api, base_url):
    do_login(page, base_url, api, tab="lives")
    colunas = page.evaluate("getComputedStyle(document.getElementById('slotsGrid')).gridTemplateColumns")
    assert len(colunas.split()) == 2, colunas
