"""WEB-LIKE-01..05 (D34): painel com meta de likes e cor da barra; lista de endpoints públicos com "Copiar"."""

from __future__ import annotations

from api_double import json_reply
from playwright.sync_api import BrowserContext, Page, expect
from web_helpers import do_login

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
    do_login(page, base_url, api)
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
    do_login(page, base_url, api)
    for n, (goal, color) in enumerate(zip(["10000", "", "1", "1000000000"], ["#00aa11", "#ff0033", "#123abc", "#000000"]), 1):
        expect(goal_of(page, n)).to_have_value(goal)
        expect(color_of(page, n)).to_have_value(color)


# ---------------------------------------------------------------------------
# WEB-LIKE-02 salvar meta e cor junto com a URL
# ---------------------------------------------------------------------------

def test_web_like_02_salvar_envia_url_meta_e_cor_e_mostra_o_normalizado(page: Page, api, base_url):
    do_login(page, base_url, api)
    page.get_by_label("Slot 1", exact=True).fill(URLS[0])
    goal_of(page, 1).fill("10000")
    color_of(page, 1).fill("#00aa11")
    page.get_by_label("Slot 2", exact=True).fill(URLS[1])
    color_of(page, 2).fill("#1234ab")  # cor sem meta
    goal_of(page, 4).fill("250")  # meta sem URL

    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()

    puts = api.requests("PUT", "/api/admin/slots")
    assert len(puts) == 1 and puts[0].status == 200
    assert puts[0].body == {
        "slots": [
            {"url": URLS[0], "goal": 10000, "color": "#00aa11"},
            {"url": URLS[1], "goal": None, "color": "#1234ab"},
            {"url": "", "goal": None, "color": "#ff0033"},
            {"url": "", "goal": 250, "color": "#ff0033"},
        ]
    }
    # o dublê (como a api) guarda a cor em maiúsculas; o painel mostra o que foi gravado
    assert api.goals == [10000, None, None, 250]
    assert api.colors == ["#00AA11", "#1234AB", "#FF0033", "#FF0033"]
    expect(goal_of(page, 1)).to_have_value("10000")
    expect(color_of(page, 1)).to_have_value("#00aa11")
    expect(goal_of(page, 4)).to_have_value("250")


def test_web_like_02b_apagar_a_meta_envia_null(page: Page, api, base_url):
    api.slots = list(URLS)
    api.goals = [5000, None, None, None]
    do_login(page, base_url, api)
    expect(goal_of(page, 1)).to_have_value("5000")
    goal_of(page, 1).fill("")
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()
    assert api.requests("PUT", "/api/admin/slots")[0].body["slots"][0]["goal"] is None
    assert api.goals[0] is None


def test_web_like_04_campos_editados_nao_sao_sobrescritos_pelo_polling(page: Page, api, base_url):
    from web_helpers import freeze_clock, wait_until

    api.slots = list(URLS)
    api.goals = [5000, None, None, None]
    freeze_clock(page)
    do_login(page, base_url, api)
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
    do_login(page, base_url, api)
    for ruim in ("0", "1.5", "1000000001", "-3"):
        goal_of(page, 3).fill(ruim)
        page.get_by_role("button", name="Salvar slots").click()
        expect(page.locator("#saveErr")).to_contain_text("Slot 3")
        expect(page.locator("#saveErr")).to_contain_text("meta de likes")
        expect(page.locator("#saveMsg")).to_be_hidden()
    assert api.requests("PUT", "/api/admin/slots") == []  # nada foi ao servidor
    # corrigindo, salva e o erro some
    goal_of(page, 3).fill("42")
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()
    expect(page.locator("#saveErr")).to_be_hidden()
    assert api.goals[2] == 42


def test_web_like_03b_422_do_servidor_em_meta_ou_cor_mostra_o_campo(page: Page, api, base_url):
    do_login(page, base_url, api)
    detail = [
        {"loc": ["body", "slots", 1, "color"], "msg": "Cor invalida: use #RRGGBB", "type": "value_error"},
        {"loc": ["body", "slots", 2, "goal"], "msg": "Input should be greater than or equal to 1", "type": "greater_than_equal"},
    ]
    api.override("PUT", "/api/admin/slots", json_reply(422, {"detail": detail}))
    page.get_by_role("button", name="Salvar slots").click()
    erro = page.locator("#saveErr")
    expect(erro).to_contain_text("Slot 2 (cor): Cor invalida")
    expect(erro).to_contain_text("Slot 3 (meta de likes)")
    expect(page.locator("#saveMsg")).to_be_hidden()


# ---------------------------------------------------------------------------
# WEB-LIKE-05 endpoints públicos: lista completa, agrupada, com "Copiar"
# ---------------------------------------------------------------------------

def expected_endpoints(base: str) -> list[tuple[str, str]]:
    lives = [(f"{base}/live{n}.json") for n in range(1, 5)] + [f"{base}/api/public/lives/{n}" for n in range(1, 5)]
    return (
        [("JSON por live", u) for u in lives]
        + [("JSON de inscritos", f"{base}/inscritos.json"), ("JSON de inscritos", f"{base}/api/public/subscribers")]
        + [("Overlays", f"{base}/contador"), ("Overlays", f"{base}/overlay/inscritos")]
        + [("Overlays", f"{base}/overlay/live.html?slot={n}") for n in range(1, 5)]
        + [("Saúde", f"{base}/health")]
    )


def listed(page: Page) -> list[tuple[str, str]]:
    return page.evaluate(
        """() => [...document.querySelectorAll('#epList .ep-group')].flatMap((g) => {
            const title = g.querySelector('.ep-group-title').textContent;
            return [...g.querySelectorAll('.ep')].map((r) => [title, r.querySelector('.ep-url').textContent]);
        })"""
    )


def test_web_like_05_lista_exatamente_os_enderecos_publicos_a_partir_do_public_base_url(page: Page, api, base_url):
    api.public_base_url = "https://live.exemplo.com.br/"  # a barra final é descartada
    do_login(page, base_url, api)
    got = [tuple(item) for item in listed(page)]
    assert got == expected_endpoints("https://live.exemplo.com.br")
    assert len({url for _, url in got}) == len(got) == 17  # sem repetidos
    # nenhum endereço de admin
    for _, url in got:
        assert "/api/admin" not in url and ":8081" not in url and "/admin" not in url, url
    # cada linha tem rótulo curto e botão Copiar
    rows = page.locator("#epList .ep")
    expect(rows).to_have_count(17)
    expect(page.locator("#epList .ep-copy")).to_have_count(17)
    for i in range(17):
        expect(rows.nth(i).locator(".ep-label")).not_to_be_empty()
        expect(rows.nth(i).locator(".ep-copy")).to_have_text("Copiar")
    titulos = page.evaluate("[...document.querySelectorAll('#epList .ep-group-title')].map((e) => e.textContent)")
    assert titulos == ["JSON por live", "JSON de inscritos", "Overlays", "Saúde"]
    # o endereço do overlay de cada slot aparece com o rótulo do slot
    expect(rows.filter(has_text="Likes da live 3")).to_contain_text("https://live.exemplo.com.br/overlay/live.html?slot=3")


def test_web_like_05b_sem_base_publica_usa_a_origem_atual(page: Page, api, base_url):
    api.public_base_url = None
    do_login(page, base_url, api)
    origem = page.evaluate("window.location.origin")
    got = [tuple(item) for item in listed(page)]
    assert got == expected_endpoints(origem)
    assert all("/api/admin" not in url for _, url in got)


def test_web_like_05c_copiar_coloca_o_endereco_completo_na_area_de_transferencia(
    page: Page, context: BrowserContext, api, base_url
):
    api.public_base_url = "https://live.exemplo.com.br"
    context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base_url)
    do_login(page, base_url, api)
    alvo = "https://live.exemplo.com.br/overlay/live.html?slot=2"
    linha = page.locator("#epList .ep").filter(has_text=alvo)
    expect(linha).to_have_count(1)
    linha.get_by_role("button", name="Copiar").click()
    expect(linha.locator(".ep-copy")).to_have_text("Copiado")
    assert page.evaluate("navigator.clipboard.readText()") == alvo
    # outro botão copia o seu próprio endereço
    page.locator("#epList .ep").filter(has_text="https://live.exemplo.com.br/health").get_by_role("button", name="Copiar").click()
    assert page.evaluate("navigator.clipboard.readText()") == "https://live.exemplo.com.br/health"
