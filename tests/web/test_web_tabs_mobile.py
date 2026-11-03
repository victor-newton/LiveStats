"""WEB-TAB-01..06 e WEB-MOB-01..07 (C13/D36): painel em abas (Inscritos, Lives, Saúde), hash, teclado, endpoints por
aba, salvar por cartão e layout de celular (375x812) e de desktop (1440x900). Chromium real contra o dublê validado."""

from __future__ import annotations

import re

import pytest
from playwright.sync_api import Page, expect
from web_helpers import do_login, open_tab, wait_until

TABS = ["inscritos", "lives", "saude"]
ROTULOS = {"inscritos": "Inscritos", "lives": "Lives", "saude": "Saúde"}
CHANNEL = "UC" + "x1Y2z3" * 3 + "ab_-"

PUBLICOS = (
    [f"/live{n}.json" for n in range(1, 5)]
    + [f"/api/public/lives/{n}" for n in range(1, 5)]
    + [f"/overlay/live.html?slot={n}" for n in range(1, 5)]
    + ["/inscritos.json", "/api/public/subscribers", "/contador", "/health"]
)


def visiveis(page: Page) -> list[str]:
    """Abas cujo painel está visível."""
    return page.evaluate("['inscritos', 'lives', 'saude'].filter((t) => !document.getElementById(t).hidden)")


def selecionada(page: Page) -> list[str]:
    return page.evaluate(
        "[...document.querySelectorAll('[role=tab]')].filter((t) => t.getAttribute('aria-selected') === 'true').map((t) => t.dataset.tab)"
    )


# --------------------------------------------------------------------------------------------- WEB-TAB
def test_web_tab_01_roles_aria_e_uma_aba_visivel_por_vez(page: Page, api, base_url):
    do_login(page, base_url, api)
    lista = page.get_by_role("tablist", name="Seções do painel")
    expect(lista).to_be_visible()
    abas = lista.get_by_role("tab")
    expect(abas).to_have_count(3)
    assert abas.all_inner_texts() == ["Inscritos", "Lives", "Saúde"]
    for tab in TABS:
        botao = page.locator(f"#tab-{tab}")
        assert botao.get_attribute("role") == "tab"
        assert botao.get_attribute("aria-controls") == tab
        painel = page.locator(f"#{tab}")
        assert painel.get_attribute("role") == "tabpanel"
        assert painel.get_attribute("aria-labelledby") == f"tab-{tab}"
    # padrão: Inscritos selecionada e visível; só uma aba por vez; as outras fora do foco do Tab
    assert selecionada(page) == ["inscritos"] and visiveis(page) == ["inscritos"]
    assert page.evaluate("[...document.querySelectorAll('[role=tab]')].map((t) => t.tabIndex)") == [0, -1, -1]
    # não existe mais a seção global de endpoints nem a barra de rolagem por âncoras
    assert page.locator("#endpoints, #epList, .nav-pill, .sections-nav").count() == 0
    # login e logout ficam fora das abas
    assert page.locator("[role=tabpanel] #loginView, [role=tabpanel] #logoutBtn").count() == 0


def test_web_tab_02_clique_troca_de_aba_e_atualiza_o_hash(page: Page, api, base_url):
    do_login(page, base_url, api)
    for tab in ("lives", "saude", "inscritos", "saude"):
        page.get_by_role("tab", name=ROTULOS[tab]).click()
        assert selecionada(page) == [tab] and visiveis(page) == [tab]
        assert page.evaluate("location.hash") == f"#{tab}"
        expect(page.locator(f"#{tab}")).to_be_visible()
        for outra in TABS:
            if outra != tab:
                expect(page.locator(f"#{outra}")).to_be_hidden()
    # o botão Voltar do navegador volta pela história das abas
    page.go_back()
    wait_until(lambda: selecionada(page) == ["inscritos"], "voltou para Inscritos")
    assert visiveis(page) == ["inscritos"]


def test_web_tab_03_teclado_setas_home_end_com_foco_e_tabindex(page: Page, api, base_url):
    do_login(page, base_url, api)
    page.locator("#tab-inscritos").focus()
    page.keyboard.press("ArrowRight")
    assert selecionada(page) == ["lives"] and page.evaluate("document.activeElement.id") == "tab-lives"
    assert visiveis(page) == ["lives"] and page.evaluate("location.hash") == "#lives"
    page.keyboard.press("ArrowRight")
    assert selecionada(page) == ["saude"] and page.evaluate("document.activeElement.id") == "tab-saude"
    page.keyboard.press("ArrowRight")  # dá a volta
    assert selecionada(page) == ["inscritos"]
    page.keyboard.press("ArrowLeft")  # dá a volta para trás
    assert selecionada(page) == ["saude"] and visiveis(page) == ["saude"]
    page.keyboard.press("Home")
    assert selecionada(page) == ["inscritos"]
    page.keyboard.press("End")
    assert selecionada(page) == ["saude"] and page.evaluate("location.hash") == "#saude"
    # só a aba selecionada entra na ordem do Tab
    assert page.evaluate("[...document.querySelectorAll('[role=tab]')].map((t) => t.tabIndex)") == [-1, -1, 0]
    # anel de foco visível
    foco = page.evaluate("(() => { const c = getComputedStyle(document.activeElement); return [c.outlineStyle, c.outlineWidth]; })()")
    assert foco[0] == "solid" and foco[1] != "0px"
    # outras teclas não mexem
    page.keyboard.press("a")
    assert selecionada(page) == ["saude"]


@pytest.mark.parametrize("tab", TABS)
def test_web_tab_04_o_hash_abre_a_aba_certa_mesmo_antes_do_login(page: Page, api, base_url, tab):
    page.goto(f"{base_url}/#{tab}")
    page.get_by_label("Usuário").fill(api.username)
    page.get_by_label("Senha").fill(api.password)
    page.get_by_role("button", name="Entrar").click()
    page.locator("#panelView").wait_for(state="visible")
    assert visiveis(page) == [tab] and selecionada(page) == [tab]
    # mudar o hash à mão (ou por um link) também troca a aba
    outra = TABS[(TABS.index(tab) + 1) % 3]
    page.evaluate("(h) => { location.hash = h; }", outra)
    wait_until(lambda: visiveis(page) == [outra], "hashchange troca a aba")
    assert selecionada(page) == [outra]


def test_web_tab_04b_hash_invalido_ou_ausente_cai_em_inscritos(page: Page, api, base_url):
    for hash_ in ("", "#nada", "#config", "#__proto__"):
        page.goto(f"{base_url}/{hash_}")
        page.get_by_label("Usuário").fill(api.username)
        page.get_by_label("Senha").fill(api.password)
        page.get_by_role("button", name="Entrar").click()
        page.locator("#panelView").wait_for(state="visible")
        assert visiveis(page) == ["inscritos"], hash_
        page.get_by_role("button", name="Sair").click()
        page.locator("#loginView").wait_for(state="visible")


def test_web_tab_05_cada_aba_tem_o_seu_conteudo_e_os_seus_enderecos(page: Page, api, base_url):
    api.public_base_url = "https://live.exemplo.com.br"
    do_login(page, base_url, api)
    base = "https://live.exemplo.com.br"
    esperado = {
        "inscritos": [f"{base}/inscritos.json", f"{base}/api/public/subscribers", f"{base}/contador"],
        "lives": [
            f"{base}{p}"
            for n in range(1, 5)
            for p in (f"/live{n}.json", f"/api/public/lives/{n}", f"/overlay/live.html?slot={n}")
        ],
        "saude": [f"{base}/health"],
    }
    # elementos que só existem na aba do seu assunto
    so_na_aba = {
        "inscritos": ["#subsCurrent", "#subsLast", "#cookieBadge", "#channelInput", "#font-subs", "#textcolor-subs", "#cookiesInput"],
        "lives": ["#inp1", "#inp4", "#goal1", "#color1", "#font-1", "#textcolor-1", "#font-4", "#saveBtn4"],
        "saude": ["#healthDiagram", "#healthCards", "#healthUpdated"],
    }
    todos: list[str] = []
    for tab in TABS:
        open_tab(page, tab)
        urls = page.locator(f"#{tab} .ep-url").all_inner_texts()
        assert urls == esperado[tab], (tab, urls)
        todos += urls
        for sel in so_na_aba[tab]:
            expect(page.locator(f"#{tab} {sel}")).to_have_count(1)
            expect(page.locator(sel)).to_be_visible()
        for outra in TABS:
            if outra != tab:
                for sel in so_na_aba[outra]:
                    expect(page.locator(f"#{outra} {sel}")).to_have_count(1)  # existe lá…
                    expect(page.locator(sel)).to_be_hidden()  # …e não aparece aqui
    # a união é exatamente o conjunto público (16), sem nenhum endereço de admin
    assert sorted(u[len(base):] for u in todos) == sorted(PUBLICOS)
    assert len(todos) == len(set(todos)) == 16
    assert not any("admin" in u or "/overlay/inscritos" in u for u in todos)
    # Lives: o cartão de cada slot é autocontido e os 3 endereços ficam DENTRO dele
    open_tab(page, "lives")
    for n in range(1, 5):
        card = page.locator(".slot-card").nth(n - 1)
        for sel in (".slot-input", ".slot-goal", ".slot-color", ".look-font", ".look-color", ".slot-stats", ".slot-save"):
            expect(card.locator(sel)).to_have_count(1)
        assert card.locator(".ep-url").all_inner_texts() == esperado["lives"][3 * (n - 1): 3 * n]
    # Inscritos: número atual, última leitura, estado/autenticação, canal, cookies, fonte e cor
    open_tab(page, "inscritos")
    for texto in ("Inscritos atuais", "Última leitura"):
        expect(page.locator("#inscritos").get_by_text(texto)).to_be_visible()
    # Saúde: diagrama, cartões e /health
    open_tab(page, "saude")
    expect(page.locator("#saude .hc")).to_have_count(5)
    expect(page.locator("#saude #healthDiagram .node")).to_have_count(6)


def test_web_tab_06_cada_cartao_salva_so_o_seu_assunto(page: Page, api, base_url):
    api.slots = ["dQw4w9WgXcQ", "", "", ""]
    do_login(page, base_url, api)
    expect(page.locator("#font-subs option")).not_to_have_count(0)
    # Inscritos: três botões, três assuntos, cada um com a sua mensagem
    page.get_by_label("ID do canal do YouTube").fill(CHANNEL)
    page.get_by_role("button", name="Salvar canal").click()
    expect(page.locator("#channelOk")).to_be_visible()
    expect(page.locator("#lookOk")).to_be_hidden()
    assert [r.body for r in api.requests("PUT", "/api/admin/settings")] == [{"channel_id": CHANNEL}]
    page.get_by_label("Cor do texto do contador", exact=True).fill("#112233")
    page.get_by_role("button", name="Salvar aparência").click()
    expect(page.locator("#lookOk")).to_be_visible()
    assert api.requests("PUT", "/api/admin/settings")[-1].body == {"subs_font": "Roboto", "subs_text_color": "#112233"}
    page.locator("#cookiesInput").fill('[{"name":"SID","value":"abc","domain":".youtube.com"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#cookiesMsg")).to_contain_text("cookies salvos")
    assert len(api.requests("PUT", "/api/admin/settings")) == 2 and len(api.requests("POST", "/api/admin/cookies")) == 1
    assert api.requests("PUT", "/api/admin/slots") == []  # nada de lives foi tocado

    # Lives: o Salvar de um cartão só grava aquele cartão
    open_tab(page, "lives")
    page.get_by_label("Meta de likes do slot 2").fill("1234")
    page.get_by_label("Meta de likes do slot 4").fill("99")
    page.get_by_role("button", name="Salvar slot 2", exact=True).click()
    expect(page.locator("#saveMsg2")).to_be_visible()
    expect(page.locator("#saveMsg4")).to_be_hidden()
    assert api.goals == [None, 1234, None, None]  # a meta do slot 4 segue só no navegador
    page.get_by_role("button", name="Salvar slot 4", exact=True).click()
    expect(page.locator("#saveMsg4")).to_be_visible()
    assert api.goals == [None, 1234, None, 99]
    assert len(api.requests("PUT", "/api/admin/settings")) == 2  # lives não mexem em settings


# --------------------------------------------------------------------------------------------- WEB-MOB
def celular(page: Page) -> None:
    page.set_viewport_size({"width": 375, "height": 812})


def interativos_visiveis(page: Page) -> list[dict]:
    return page.evaluate(
        """() => [...document.querySelectorAll('button, input, select, textarea, [role=tab]')]
            .filter((e) => e.getClientRects().length && !e.closest('[hidden]') && getComputedStyle(e).visibility !== 'hidden')
            .map((e) => { const r = e.getBoundingClientRect();
                return {id: e.id || e.className || e.tagName, w: Math.round(r.width * 10) / 10, h: Math.round(r.height * 10) / 10, tipo: e.type || e.tagName}; })"""
    )


def test_web_mob_01_barra_de_abas_fixa_embaixo_com_safe_area(page: Page, api, base_url):
    celular(page)
    api.set_stats(1, api.live_stats(1))
    do_login(page, base_url, api)
    # a meta viewport existe e cobre a área segura dos celulares com notch
    meta = page.get_attribute("meta[name=viewport]", "content") or ""
    assert "width=device-width" in meta and "initial-scale=1" in meta and "viewport-fit=cover" in meta
    barra = page.locator(".tabbar")
    for tab in TABS:
        open_tab(page, tab)
        info = page.evaluate(
            """() => { const e = document.querySelector('.tabbar'), r = e.getBoundingClientRect(), c = getComputedStyle(e);
            return [c.position, Math.round(r.left), Math.round(r.right), Math.round(r.bottom), window.innerWidth, window.innerHeight, c.paddingBottom]; }"""
        )
        assert info[0] == "fixed", info
        assert info[1] == 0 and info[2] == info[4] == 375, info  # largura toda
        assert info[3] == info[5] == 812, info  # colada embaixo
        # rolando a página até o fim a barra continua no lugar
        page.evaluate("window.scrollTo({top: document.documentElement.scrollHeight, behavior: 'instant'})")
        assert page.evaluate("Math.round(document.querySelector('.tabbar').getBoundingClientRect().bottom)") == 812
        expect(barra).to_be_visible()
        # o conteúdo final não fica escondido atrás da barra
        ultimo = page.evaluate(
            "Math.max(...[...document.querySelectorAll(`#${CSS.escape(document.querySelector('[role=tab][aria-selected=true]').dataset.tab)} *`)].map((e) => e.getBoundingClientRect().bottom))"
        )
        topo_barra = page.evaluate("document.querySelector('.tabbar').getBoundingClientRect().top")
        assert ultimo <= topo_barra + 1, (tab, ultimo, topo_barra)
        page.evaluate("window.scrollTo({top: 0, behavior: 'instant'})")
    # o recuo da área segura está no CSS (o Chromium de teste não tem notch para exercitar o valor real)
    css = page.evaluate("fetch('/admin-assets/css/main.css').then((r) => r.text())")
    assert re.search(r"padding[^;]*env\(safe-area-inset-bottom", css)
    assert page.evaluate("getComputedStyle(document.querySelector('.tabbar')).paddingBottom") != "0px"


def test_web_mob_02_coluna_unica_e_sem_rolagem_horizontal_a_375(page: Page, api, base_url):
    celular(page)
    api.slots = ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=" + "x" * 150, "", "", ""]
    api.public_base_url = "https://um-dominio-bem-comprido.exemplo.com.br/com/um/caminho/enorme"
    api.lives_worker = api.worker_state("error", last_error_code="http_error", last_error_message="mensagem " * 20)
    do_login(page, base_url, api)
    for tab in TABS:
        open_tab(page, tab)
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), tab
        fora = page.evaluate(
            """() => [...document.querySelectorAll('#panelView *')]
                .filter((e) => e.getClientRects().length && e.getBoundingClientRect().right > 375.5)
                .map((e) => (e.id || e.className || e.tagName).toString())"""
        )
        assert fora == [], (tab, fora)
    # cartões em uma coluna só: todos começam na mesma borda esquerda e cabem na largura
    for tab in TABS:
        open_tab(page, tab)
        lefts = page.evaluate(f"[...document.querySelectorAll('#{tab} .card')].map((e) => Math.round(e.getBoundingClientRect().left))")
        assert len(set(lefts)) == 1, (tab, lefts)
    open_tab(page, "saude")
    assert len(page.evaluate("getComputedStyle(document.getElementById('healthCards')).gridTemplateColumns").split()) == 1


def test_web_mob_03_alvos_de_toque_de_44px_e_teclado_numerico_na_meta(page: Page, api, base_url):
    celular(page)
    do_login(page, base_url, api)
    for tab in TABS:
        open_tab(page, tab)
        pequenos = [i for i in interativos_visiveis(page) if i["h"] < 43.9 or i["w"] < 43.9]
        assert pequenos == [], (tab, pequenos)
    # a tela de login também
    page.get_by_role("button", name="Sair").click()
    page.locator("#loginView").wait_for(state="visible")
    pequenos = [i for i in interativos_visiveis(page) if i["h"] < 43.9]
    assert pequenos == [], pequenos
    # a meta chama o teclado numérico; a URL, o de endereços; o ID do canal não corrige nem capitaliza
    do_login(page, base_url, api, tab="lives")
    for n in range(1, 5):
        meta = page.locator(f"#goal{n}")
        assert meta.get_attribute("inputmode") == "numeric" and meta.get_attribute("type") == "number"
        assert page.locator(f"#inp{n}").get_attribute("inputmode") == "url"
    open_tab(page, "inscritos")
    assert page.locator("#channelInput").get_attribute("autocapitalize") == "off"


def test_web_mob_04_tipografia_legivel_no_celular(page: Page, api, base_url):
    celular(page)
    api.set_stats(1, api.live_stats(1, titulo="Titulo"))
    do_login(page, base_url, api)
    assert page.evaluate("parseFloat(getComputedStyle(document.body).fontSize)") >= 14
    for tab, seletores in (
        ("inscritos", [".hint", ".stat-small", ".worker", ".ep-label", ".ep-url", ".msg, .steps", ".field label"]),
        ("lives", [".hint", ".slot-label", ".ep-label", ".ep-url", ".stat-empty", ".slot-goal-label"]),
        ("saude", [".hint", ".hc-rows dd", ".hc-rows dt", ".legend li", ".ep-url"]),
    ):
        open_tab(page, tab)
        for sel in seletores:
            tamanhos = page.evaluate(
                "(s) => [...document.querySelectorAll(s)].filter((e) => e.getClientRects().length).map((e) => parseFloat(getComputedStyle(e).fontSize))",
                f"#{tab} {sel}",
            )
            assert tamanhos, (tab, sel)
            assert min(tamanhos) >= 12.0, (tab, sel, tamanhos)  # rótulos em caixa alta: mínimo 12 px
            if sel in (".hint", ".ep-label", ".ep-url", ".hc-rows dd", ".worker", ".stat-small", ".msg, .steps"):
                assert min(tamanhos) >= 14.0, (tab, sel, tamanhos)  # texto corrido: mínimo 14 px
    # nenhum texto visível menor que 12 px em toda a tela
    menor = page.evaluate(
        """() => Math.min(...[...document.querySelectorAll('#panelView *')]
            .filter((e) => e.getClientRects().length && [...e.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && !e.closest('svg'))
            .map((e) => parseFloat(getComputedStyle(e).fontSize)))"""
    )
    assert menor >= 12.0, menor
    # campos com 16 px: o iOS não dá zoom ao focar
    for tab in ("inscritos", "lives"):
        open_tab(page, tab)
        campos = page.evaluate(
            "[...document.querySelectorAll('#panelView input:not([type=color]), #panelView select, #panelView textarea')].filter((e) => e.getClientRects().length).map((e) => parseFloat(getComputedStyle(e).fontSize))"
        )
        assert campos and min(campos) >= 16, (tab, campos)


def no_svg(page: Page) -> dict:
    return page.evaluate(
        """() => { const root = document.getElementById('healthDiagram'), box = root.getBoundingClientRect(), vb = root.viewBox.baseVal;
        const nodes = Object.fromEntries([...root.querySelectorAll('[data-node]')].map((g) => {
            const r = g.querySelector('rect').getBoundingClientRect(), t = g.querySelector('text').getBoundingClientRect();
            return [g.dataset.node, {x: r.left, y: r.top, w: r.width, h: r.height, tw: t.width, th: t.height, fs: parseFloat(getComputedStyle(g.querySelector('text')).fontSize)}]; }));
        return {layout: root.dataset.layout, vbw: vb.width, vbh: vb.height, boxw: box.width, boxh: box.height, left: box.left, right: box.right, scale: box.width / vb.width, nodes}; }"""
    )


def test_web_mob_05_diagrama_de_saude_empilhado_na_vertical_sem_miniaturizar(page: Page, api, base_url):
    api.cookies = {"configured": True, "count": 12, "uploaded_at": api.worker_state()["last_heartbeat_at"], "applied": True, "source": "admin", "created_at": api.worker_state()["last_heartbeat_at"], "renewed_from": None}
    celular(page)
    do_login(page, base_url, api, tab="saude")
    expect(page.locator("#healthDiagram [data-node]")).to_have_count(6)
    d = no_svg(page)
    assert d["layout"] == "vertical" and d["vbh"] > d["vbw"], d
    assert d["left"] >= 0 and d["right"] <= 375, d
    assert d["scale"] >= 0.85, d  # em tamanho (quase) real, não encolhido para caber
    n = d["nodes"]
    # empilhado: YouTube em cima, os workers lado a lado, depois Banco, API e Web, cada um abaixo do anterior
    assert n["youtube"]["y"] + n["youtube"]["h"] <= n["lives"]["y"] and n["youtube"]["y"] + n["youtube"]["h"] <= n["inscritos"]["y"]
    assert abs(n["lives"]["y"] - n["inscritos"]["y"]) < 1 and n["lives"]["x"] + n["lives"]["w"] <= n["inscritos"]["x"]
    assert n["lives"]["y"] + n["lives"]["h"] <= n["db"]["y"]
    assert n["db"]["y"] + n["db"]["h"] <= n["api"]["y"] and n["api"]["y"] + n["api"]["h"] <= n["web"]["y"]
    for nome, caixa in n.items():
        assert caixa["tw"] <= caixa["w"] - 8, (nome, caixa)  # o rótulo cabe no nó
        assert caixa["fs"] * d["scale"] >= 13.5, (nome, caixa, d["scale"])  # texto legível na tela
        assert caixa["x"] >= d["left"] - 0.5 and caixa["x"] + caixa["w"] <= d["right"] + 0.5, (nome, caixa)
    # as seis ligações existem e ligam os nós de cima para baixo; o estado continua valendo no layout vertical
    assert page.locator("#healthDiagram [data-link]").count() == 6
    for nome in ("youtube-lives", "youtube-inscritos", "lives-db", "inscritos-db", "db-api", "api-web"):
        assert page.evaluate("(n) => document.querySelector(`[data-link='${n}']`).dataset.state", nome) == "ok", nome
    # girar o aparelho / alargar a janela redesenha na horizontal, e voltar redesenha na vertical, sem perder o estado
    page.set_viewport_size({"width": 1440, "height": 900})
    wait_until(lambda: no_svg(page)["layout"] == "horizontal", "layout horizontal no desktop")
    h = no_svg(page)
    assert h["vbw"] == 920 and h["nodes"]["youtube"]["x"] < h["nodes"]["web"]["x"]
    assert page.locator("#healthDiagram [data-link]").count() == 6 and page.locator("#healthDiagram [data-node]").count() == 6
    assert page.evaluate("document.querySelector('[data-link=\"db-api\"]').dataset.state") == "ok"
    celular(page)
    wait_until(lambda: no_svg(page)["layout"] == "vertical", "volta ao layout vertical")
    assert page.locator("#healthDiagram [data-node]").count() == 6


def test_web_mob_05b_diagrama_vertical_mostra_a_falha_na_ligacao_certa(page: Page, api, base_url):
    celular(page)
    api.lives_worker = api.worker_state("error", last_error_code="http_error", last_error_message="O YouTube respondeu com erro")
    do_login(page, base_url, api, tab="saude")
    wait_until(lambda: page.evaluate("document.querySelector('[data-link=\"youtube-lives\"]').dataset.state") == "error", "falha desenhada")
    assert page.evaluate("document.getElementById('healthDiagram').dataset.layout") == "vertical"
    assert page.evaluate("document.querySelector('[data-link=\"inscritos-db\"]').dataset.state") == "ok"
    dots = page.evaluate("getComputedStyle(document.querySelector('[data-link=\"youtube-lives\"] .link-dots')).animationName")
    assert dots == "none"


def test_web_mob_06_desktop_1440x900_barra_no_topo_e_grade_em_colunas(page: Page, api, base_url):
    page.set_viewport_size({"width": 1440, "height": 900})
    api.set_stats(1, api.live_stats(1))
    do_login(page, base_url, api)
    info = page.evaluate(
        """() => { const bar = document.querySelector('.tabbar'), c = getComputedStyle(bar), r = bar.getBoundingClientRect(),
        painel = document.getElementById('inscritos').getBoundingClientRect();
        return [c.position, r.top < painel.top, r.bottom <= painel.top + 1, document.documentElement.scrollWidth <= document.documentElement.clientWidth]; }"""
    )
    assert info == ["static", True, True, True], info  # barra normal, acima do conteúdo (não fixa embaixo)
    # Inscritos: cartões lado a lado (2 colunas); Lives: 2 colunas; Saúde: cartões de serviço em várias colunas
    tops = page.evaluate("[...document.querySelectorAll('#inscritos .grid > .card')].slice(0, 2).map((e) => Math.round(e.getBoundingClientRect().top))")
    assert tops[0] == tops[1], tops
    open_tab(page, "lives")
    assert len(page.evaluate("getComputedStyle(document.getElementById('slotsGrid')).gridTemplateColumns").split()) == 2
    assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    open_tab(page, "saude")
    assert page.evaluate("document.getElementById('healthDiagram').dataset.layout") == "horizontal"
    assert len(page.evaluate("getComputedStyle(document.getElementById('healthCards')).gridTemplateColumns").split()) >= 3
    larg = page.evaluate("document.getElementById('healthDiagram').getBoundingClientRect().width")
    assert larg > 600, larg
    # o visual da D35 segue no desktop: fundo quase preto, cartão com borda de 1px e 16 px de raio
    visual = page.evaluate(
        "(() => { const c = getComputedStyle(document.querySelector('.card')); return [getComputedStyle(document.body).backgroundColor, c.borderTopWidth, c.borderTopLeftRadius]; })()"
    )
    assert visual == ["rgb(5, 5, 6)", "1px", "16px"]
    # alvos de toque pequenos são permitidos no desktop: a regra de 44 px é do celular
    assert page.locator("#tab-lives").bounding_box()["height"] < 44


def test_web_mob_07_abas_funcionam_por_toque_no_celular(page: Page, api, base_url):
    celular(page)
    do_login(page, base_url, api)
    for tab in ("saude", "lives", "inscritos"):
        page.get_by_role("tab", name=ROTULOS[tab]).click()
        assert visiveis(page) == [tab]
        assert page.evaluate("location.hash") == f"#{tab}"
    # teclado também funciona na barra de baixo
    page.locator("#tab-inscritos").focus()
    page.keyboard.press("ArrowRight")
    assert selecionada(page) == ["lives"]
