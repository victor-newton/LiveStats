"""WEB-LIKE-01..05 (D34): painel com meta de likes e cor da barra; lista de endpoints públicos com "Copiar"."""

from __future__ import annotations

from api_double import json_reply
from playwright.sync_api import BrowserContext, Page, expect
from web_helpers import do_login, open_tab

URLS = [
    "https://youtube.com/watch?v=aaaaaaaaaaa",
    "https://youtu.be/bbbbbbbbbbb",
    "",
    "ddddddddddd",
]


def goal_of(page: Page, n: int):
    return page.get_by_label(f"Meta de likes do slot {n}")


def color_of(page: Page, n: int):
    return page.get_by_label(f"Cor da barra do slot {n}")


# ---------------------------------------------------------------------------
# WEB-LIKE-01 campos do cartão
# ---------------------------------------------------------------------------

def test_web_like_01_cada_slot_tem_meta_numerica_e_seletor_de_cor(page: Page, api, base_url):
    do_login(page, base_url, api, tab="lives")
    expect(page.locator(".slot-card")).to_have_count(4)
    for n in range(1, 5):
        goal, color = goal_of(page, n), color_of(page, n)
        expect(goal).to_have_attribute("type", "number")
        expect(goal).to_have_attribute("min", "1")
        expect(goal).to_have_attribute("max", "1000000000")
        expect(goal).to_have_attribute("step", "1")
        expect(goal).to_have_attribute("placeholder", "Sem meta")
        expect(goal).to_have_value("")  # vazio = sem meta
        expect(color).to_have_attribute("type", "color")
        expect(color).to_have_value("#ff0033")  # padrão
        # o rótulo visível é curto; o complemento "do slot N" é só para leitores de tela
        expect(page.locator(".slot-card").nth(n - 1).locator(".slot-goal-label")).to_contain_text("Meta de likes")
        expect(page.locator(".slot-card").nth(n - 1).locator(".slot-color-label")).to_contain_text("Cor da barra")


def test_web_like_01b_valores_do_servidor_aparecem_nos_campos(page: Page, api, base_url):
    api.slots = list(URLS)
    api.goals = [10000, None, 1, 1_000_000_000]
    api.colors = ["#00AA11", "#FF0033", "#123ABC", "#000000"]
    do_login(page, base_url, api, tab="lives")
    for n, (goal, color) in enumerate(zip(["10000", "", "1", "1000000000"], ["#00aa11", "#ff0033", "#123abc", "#000000"]), 1):
        expect(goal_of(page, n)).to_have_value(goal)
        expect(color_of(page, n)).to_have_value(color)


# ---------------------------------------------------------------------------
# WEB-LIKE-02 salvar meta e cor junto com a URL
# ---------------------------------------------------------------------------

def test_web_like_02_salvar_envia_url_meta_e_cor_e_mostra_o_normalizado(page: Page, api, base_url):
    do_login(page, base_url, api, tab="lives")
    page.get_by_label("Slot 1", exact=True).fill(URLS[0])
    goal_of(page, 1).fill("10000")
    color_of(page, 1).fill("#00aa11")
    page.get_by_label("Slot 2", exact=True).fill(URLS[1])
    color_of(page, 2).fill("#1234ab")  # cor sem meta
    goal_of(page, 4).fill("250")  # meta sem URL

    # D36: cada cartão tem o seu Salvar (PUT completo: este cartão + o que já estava gravado nos outros)
    for n in (1, 2, 4):
        page.get_by_role("button", name=f"Salvar slot {n}", exact=True).click()
        expect(page.locator(f"#saveMsg{n}")).to_be_visible()

    puts = api.requests("PUT", "/api/admin/slots")
    assert len(puts) == 3 and all(p.status == 200 for p in puts)
    base = {"font": "Roboto", "text_color": "#ffffff"}
    vazio = {"url": "", "goal": None, "color": "#FF0033", "font": "Roboto", "text_color": "#FFFFFF"}
    assert puts[0].body["slots"][0] == {"url": URLS[0], "goal": 10000, "color": "#00aa11", **base}
    assert puts[0].body["slots"][1:] == [vazio, vazio, vazio]  # rascunhos de outros cartões não vão junto
    assert puts[1].body["slots"][1] == {"url": URLS[1], "goal": None, "color": "#1234ab", **base}
    assert puts[1].body["slots"][0] == {"url": URLS[0], "goal": 10000, "color": "#00AA11", "font": "Roboto", "text_color": "#FFFFFF"}
    assert puts[2].body["slots"][3] == {"url": "", "goal": 250, "color": "#ff0033", **base}
    # o dublê (como a api) guarda a cor em maiúsculas; o painel mostra o que foi gravado
    assert api.goals == [10000, None, None, 250]
    assert api.colors == ["#00AA11", "#1234AB", "#FF0033", "#FF0033"]
    expect(goal_of(page, 1)).to_have_value("10000")
    expect(color_of(page, 1)).to_have_value("#00aa11")
    expect(goal_of(page, 4)).to_have_value("250")


def test_web_like_02b_apagar_a_meta_envia_null(page: Page, api, base_url):
    api.slots = list(URLS)
    api.goals = [5000, None, None, None]
    do_login(page, base_url, api, tab="lives")
    expect(goal_of(page, 1)).to_have_value("5000")
    goal_of(page, 1).fill("")
    page.get_by_role("button", name="Salvar slot 1", exact=True).click()
    expect(page.locator("#saveMsg1")).to_be_visible()
    assert api.requests("PUT", "/api/admin/slots")[0].body["slots"][0]["goal"] is None
    assert api.goals[0] is None


def test_web_like_04_campos_editados_nao_sao_sobrescritos_pelo_polling(page: Page, api, base_url):
    from web_helpers import freeze_clock, wait_until

    api.slots = list(URLS)
    api.goals = [5000, None, None, None]
    freeze_clock(page)
    do_login(page, base_url, api, tab="lives")
    antes = len(api.requests("GET", "/api/admin/slots"))
    goal_of(page, 1).fill("777")
    color_of(page, 2).fill("#abcdef")
    api.goals = [9999, 1, None, None]  # mudou no servidor
    api.colors = ["#111111", "#222222", "#333333", "#444444"]
    page.clock.run_for(15000)
    wait_until(lambda: len(api.requests("GET", "/api/admin/slots")) == antes + 1, "polling do painel")
    expect(goal_of(page, 1)).to_have_value("777")  # em edição: preservado
    expect(color_of(page, 2)).to_have_value("#abcdef")
    expect(goal_of(page, 2)).to_have_value("1")  # não editado: segue o servidor
    expect(color_of(page, 3)).to_have_value("#333333")


# ---------------------------------------------------------------------------
# WEB-LIKE-03 meta inválida
# ---------------------------------------------------------------------------

def test_web_like_03_meta_invalida_nao_e_enviada_e_o_erro_aponta_o_slot(page: Page, api, base_url):
    do_login(page, base_url, api, tab="lives")
    for ruim in ("0", "1.5", "1000000001", "-3"):
        goal_of(page, 3).fill(ruim)
        page.get_by_role("button", name="Salvar slot 3", exact=True).click()
        expect(page.locator("#saveErr3")).to_contain_text("Slot 3")
        expect(page.locator("#saveErr3")).to_contain_text("meta de likes")
        expect(page.locator("#saveMsg3")).to_be_hidden()
    assert api.requests("PUT", "/api/admin/slots") == []  # nada foi ao servidor
    # corrigindo, salva e o erro some
    goal_of(page, 3).fill("42")
    page.get_by_role("button", name="Salvar slot 3", exact=True).click()
    expect(page.locator("#saveMsg3")).to_be_visible()
    expect(page.locator("#saveErr3")).to_be_hidden()
    assert api.goals[2] == 42


def test_web_like_03b_422_do_servidor_em_meta_ou_cor_mostra_o_campo(page: Page, api, base_url):
    do_login(page, base_url, api, tab="lives")
    detail = [
        {"loc": ["body", "slots", 1, "color"], "msg": "Cor invalida: use #RRGGBB", "type": "value_error"},
        {"loc": ["body", "slots", 2, "goal"], "msg": "Input should be greater than or equal to 1", "type": "greater_than_equal"},
    ]
    api.override("PUT", "/api/admin/slots", json_reply(422, {"detail": detail}))
    page.get_by_role("button", name="Salvar slot 2", exact=True).click()
    erro = page.locator("#saveErr2")
    expect(erro).to_contain_text("Slot 2 (cor): Cor invalida")
    expect(erro).to_contain_text("Slot 3 (meta de likes)")
    expect(page.locator("#saveMsg2")).to_be_hidden()


# ---------------------------------------------------------------------------
# WEB-LIKE-05 endpoints públicos: cada aba lista os SEUS, a união é o conjunto público completo (D36)
# ---------------------------------------------------------------------------

def expected_endpoints(base: str) -> dict[str, list[str]]:
    return {
        "inscritos": [f"{base}/inscritos.json", f"{base}/api/public/subscribers", f"{base}/contador"],
        "lives": [
            u
            for n in range(1, 5)
            for u in (f"{base}/live{n}.json", f"{base}/api/public/lives/{n}", f"{base}/overlay/live.html?slot={n}")
        ],
        "saude": [f"{base}/health"],
    }


def listed(page: Page) -> dict[str, list[str]]:
    """Endereços de cada aba, na ordem do DOM (lidos de dentro do painel de cada aba)."""
    return page.evaluate(
        """() => Object.fromEntries(['inscritos', 'lives', 'saude'].map((tab) => [tab,
            [...document.querySelectorAll(`#${tab} .ep .ep-url`)].map((e) => e.textContent)]))"""
    )


def test_web_like_05_cada_aba_lista_os_seus_enderecos_a_partir_do_public_base_url(page: Page, api, base_url):
    api.public_base_url = "https://live.exemplo.com.br/"  # a barra final é descartada
    do_login(page, base_url, api)
    got = listed(page)
    assert got == expected_endpoints("https://live.exemplo.com.br")
    todos = [u for urls in got.values() for u in urls]
    assert len(set(todos)) == len(todos) == 16  # sem repetidos; a união são os 16 endereços públicos
    # nenhum endereço de admin
    for url in todos:
        assert "/api/admin" not in url and ":8081" not in url and "/admin" not in url, url
    assert "/overlay/inscritos" not in "".join(todos)  # D36: só /contador
    # cada linha tem rótulo e botão Copiar, e ficam na aba do seu assunto
    for tab, n in (("inscritos", 3), ("lives", 12), ("saude", 1)):
        open_tab(page, tab)
        rows = page.locator(f"#{tab} .ep")
        expect(rows).to_have_count(n)
        expect(page.locator(f"#{tab} .ep-copy")).to_have_count(n)
        for i in range(n):
            expect(rows.nth(i).locator(".ep-label")).not_to_be_empty()
            expect(rows.nth(i).locator(".ep-copy")).to_have_text("Copiar")
    # o overlay de cada slot aparece com o rótulo do slot, dentro do cartão do próprio slot
    open_tab(page, "lives")
    card3 = page.locator(".slot-card").nth(2)
    expect(card3.locator(".ep").filter(has_text="Likes da live 3")).to_contain_text("https://live.exemplo.com.br/overlay/live.html?slot=3")
    assert card3.locator(".ep-url").all_inner_texts() == expected_endpoints("https://live.exemplo.com.br")["lives"][6:9]


def test_web_like_05b_sem_base_publica_usa_a_origem_atual(page: Page, api, base_url):
    api.public_base_url = None
    do_login(page, base_url, api)
    origem = page.evaluate("window.location.origin")
    got = listed(page)
    assert got == expected_endpoints(origem)
    assert all("/api/admin" not in url for urls in got.values() for url in urls)


def test_web_like_05c_copiar_coloca_o_endereco_completo_na_area_de_transferencia(
    page: Page, context: BrowserContext, api, base_url
):
    api.public_base_url = "https://live.exemplo.com.br"
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    do_login(page, base_url, api, tab="lives")
    alvo = "https://live.exemplo.com.br/overlay/live.html?slot=2"
    linha = page.locator(".slot-card").nth(1).locator(".ep").filter(has_text=alvo)
    expect(linha).to_have_count(1)
    linha.get_by_role("button", name="Copiar").click()
    expect(linha.locator(".ep-copy")).to_have_text("Copiado")
    assert page.evaluate("navigator.clipboard.readText()") == alvo
    # o botão da aba Inscritos e o da aba Saúde copiam o seu próprio endereço
    open_tab(page, "inscritos")
    page.locator("#epSubs .ep").filter(has_text="https://live.exemplo.com.br/contador").get_by_role("button", name="Copiar").click()
    assert page.evaluate("navigator.clipboard.readText()") == "https://live.exemplo.com.br/contador"
    open_tab(page, "saude")
    page.locator("#epHealth .ep").filter(has_text="https://live.exemplo.com.br/health").get_by_role("button", name="Copiar").click()
    assert page.evaluate("navigator.clipboard.readText()") == "https://live.exemplo.com.br/health"
