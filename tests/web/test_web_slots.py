"""WEB-SLOT-01..06: painel de lives (4 slots, estatísticas, salvar, endpoints)."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from web_helpers import do_login, freeze_clock, wait_until

URLS = [
    "https://youtube.com/watch?v=aaaaaaaaaaa",
    "https://youtu.be/bbbbbbbbbbb",
    "https://www.youtube.com/live/ccccccccccc",
    "ddddddddddd",
]


def n_get_slots(api) -> int:
    return len(api.requests("GET", "/api/admin/slots"))


def test_web_slot_01_quatro_cartoes_com_valores_do_servidor(page: Page, api, base_url):
    api.slots = list(URLS)
    do_login(page, base_url, api)

    cards = page.locator(".slot-card")
    expect(cards).to_have_count(4)
    for n in range(1, 5):
        campo = page.get_by_label(f"Slot {n}", exact=True)
        expect(campo).to_have_attribute("placeholder", "https://youtube.com/watch?v=...")
        expect(campo).to_have_value(URLS[n - 1])
        expect(cards.nth(n - 1).locator(".slot-label")).to_have_text(f"Slot {n}")
    expect(page.get_by_role("button", name="Salvar slots")).to_be_visible()
    assert n_get_slots(api) >= 1


def test_web_slot_03_estados_do_cartao(page: Page, api, base_url):
    api.slots = [URLS[0], URLS[1], URLS[2], ""]
    api.set_stats(1, api.live_stats(1, status="ao_vivo", views=1234, likes=56, titulo="Live de teste um"))
    api.set_stats(2, api.live_stats(2, status="encerrada", views=2_500_000, likes=1500, titulo="Live dois"))
    api.set_stats(3, api.live_stats(3, status="agendada", views=0, likes=0, titulo="Live três agendada"))
    api.set_stats(
        4,
        api.live_stats(4, status="sem_url", ativo="false", video_id="", titulo="", views=0, likes=0),
    )
    do_login(page, base_url, api)
    cards = page.locator(".slot-card")

    dot = lambda i: cards.nth(i).locator(".slot-status")  # noqa: E731
    expect(dot(0)).to_have_class("slot-status ao_vivo")
    expect(dot(1)).to_have_class("slot-status encerrada")
    expect(dot(2)).to_have_class("slot-status agendada")
    expect(dot(3)).to_have_class("slot-status")  # sem_url: sem classe de estado

    # com views > 0: números formatados e rótulos Views/Likes
    c1 = cards.nth(0)
    expect(c1.locator(".stat-item")).to_have_count(2)
    expect(c1.locator(".stat-item").nth(0).locator(".stat-val")).to_have_text("1.2K")
    expect(c1.locator(".stat-item").nth(0).locator(".stat-label")).to_have_text("Views")
    expect(c1.locator(".stat-item").nth(1).locator(".stat-val")).to_have_text("56")
    expect(c1.locator(".stat-item").nth(1).locator(".stat-label")).to_have_text("Likes")
    expect(c1.locator(".slot-title")).to_have_text("Live de teste um")
    expect(cards.nth(1).locator(".stat-val").nth(0)).to_have_text("2.5M")
    expect(cards.nth(1).locator(".stat-val").nth(1)).to_have_text("1.5K")

    # sem views (0) mas com URL: aguardando; sem URL: convite a inserir
    expect(cards.nth(2).locator(".slot-stats")).to_have_text("Aguardando dados...")
    expect(cards.nth(2).locator(".slot-title")).to_have_text("Live três agendada")
    expect(cards.nth(3).locator(".slot-stats")).to_have_text("Sem dados — insira uma URL")
    expect(cards.nth(3).locator(".slot-title")).to_be_hidden()


def test_web_slot_03b_sem_estatisticas_ainda(page: Page, api, base_url):
    api.slots = [URLS[0], "", "", ""]  # stats = null em todos (antes do 1º ciclo)
    do_login(page, base_url, api)
    cards = page.locator(".slot-card")
    expect(cards.nth(0).locator(".slot-stats")).to_have_text("Aguardando dados...")
    for i in (1, 2, 3):
        expect(cards.nth(i).locator(".slot-stats")).to_have_text("Sem dados — insira uma URL")
    assert (cards.nth(0).locator(".slot-status").get_attribute("class") or "").split() == ["slot-status"]


def test_web_slot_02_salvar_envia_put_mostra_aviso_e_recarrega(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    for n, url in enumerate(URLS, start=1):
        page.get_by_label(f"Slot {n}", exact=True).fill(f"  {url}  ")
    antes = n_get_slots(api)

    page.get_by_role("button", name="Salvar slots").click()
    msg = page.locator("#saveMsg")
    expect(msg).to_have_text("✓ Salvo com sucesso")
    expect(msg).to_be_visible()

    puts = api.requests("PUT", "/api/admin/slots")
    assert len(puts) == 1
    # D34: cada slot vai como {url, goal, color}; sem mexer em meta e cor: meta nula e cor padrão
    assert puts[0].body == {"slots": [{"url": f"  {u}  ", "goal": None, "color": "#ff0033"} for u in URLS]}
    assert puts[0].status == 200
    assert api.slots == URLS  # o servidor aparou os espaços
    expect(page.get_by_label("Slot 1", exact=True)).to_have_value(URLS[0])  # campo reflete o salvo

    # some em 2,5 s (relógio do Playwright)
    page.clock.run_for(2499)
    expect(msg).to_be_visible()
    page.clock.run_for(1)
    expect(msg).to_be_hidden()

    # 3 s depois do salvamento recarrega as estatísticas
    assert n_get_slots(api) == antes
    api.set_stats(1, api.live_stats(1, views=4321, likes=99, titulo="Recarregado após salvar"))
    page.clock.run_for(499)
    page.wait_for_timeout(200)
    assert n_get_slots(api) == antes  # ainda não passaram 3 s
    page.clock.run_for(1)
    wait_until(lambda: n_get_slots(api) == antes + 1, "recarga 3 s após salvar")
    expect(page.locator(".slot-title").first).to_have_text("Recarregado após salvar")
    expect(page.locator(".stat-val").first).to_have_text("4.3K")


def test_web_slot_04_polling_de_15s_preserva_o_que_o_usuario_digita(page: Page, api, base_url):
    api.slots = [URLS[0], "", "", ""]
    api.set_stats(1, api.live_stats(1, titulo="Antes"))
    freeze_clock(page)
    do_login(page, base_url, api)
    base = n_get_slots(api)
    status_base = len(api.requests("GET", "/api/admin/workers/status"))

    page.get_by_label("Slot 2", exact=True).fill("rascunho que ainda nao foi salvo")
    api.set_stats(1, api.live_stats(1, titulo="Depois", views=777, likes=7))
    api.slots[0] = "https://youtube.com/watch?v=zzzzzzzzzzz"  # mudou no servidor (campo não editado)

    page.clock.run_for(14999)
    page.wait_for_timeout(300)
    assert n_get_slots(api) == base  # antes de 15 s não consulta
    page.clock.run_for(1)
    wait_until(lambda: n_get_slots(api) == base + 1, "1º polling após 15 s")
    wait_until(lambda: len(api.requests("GET", "/api/admin/workers/status")) == status_base + 1, "status junto")

    expect(page.locator(".slot-title").first).to_have_text("Depois")  # estatística atualizou
    expect(page.get_by_label("Slot 2", exact=True)).to_have_value("rascunho que ainda nao foi salvo")
    expect(page.get_by_label("Slot 1", exact=True)).to_have_value("https://youtube.com/watch?v=zzzzzzzzzzz")

    page.clock.run_for(15000)
    wait_until(lambda: n_get_slots(api) == base + 2, "2º polling")
    expect(page.get_by_label("Slot 2", exact=True)).to_have_value("rascunho que ainda nao foi salvo")


def test_web_slot_05_erro_422_ao_salvar_mostra_mensagem_e_nao_diz_salvo(page: Page, api, base_url):
    do_login(page, base_url, api)
    page.get_by_label("Slot 3", exact=True).fill("https://example.com/isto-nao-e-youtube")
    page.get_by_role("button", name="Salvar slots").click()

    erro = page.locator("#saveErr")
    expect(erro).to_be_visible()
    expect(erro).to_contain_text("Slot 3")
    expect(erro).to_contain_text("inválido")
    expect(page.locator("#saveMsg")).to_be_hidden()
    assert "Salvo" not in page.locator(".actions").inner_text()
    assert api.requests("PUT", "/api/admin/slots")[0].status == 422
    assert api.slots == ["", "", "", ""]  # nada gravado

    # corrigindo e salvando de novo: o erro some e o aviso de sucesso aparece
    page.get_by_label("Slot 3", exact=True).fill(URLS[2])
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()
    expect(erro).to_be_hidden()


def test_web_slot_06_lista_endpoints_com_base_publica(page: Page, api, base_url):
    api.public_base_url = "https://live.exemplo.com.br/"
    do_login(page, base_url, api)
    # a lista completa (17 endereços, agrupados e com "Copiar") é verificada em WEB-LIKE-05
    rows = page.locator("#epList .ep")
    expect(rows).to_have_count(17)
    for n in range(1, 5):
        expect(rows.nth(n - 1).locator(".ep-badge")).to_have_text("GET")
        expect(rows.nth(n - 1).locator(".ep-url")).to_have_text(f"https://live.exemplo.com.br/live{n}.json")
    expect(page.get_by_role("heading", name="Endpoints", exact=True)).to_be_visible()


def test_web_slot_06b_sem_base_publica_usa_a_origem_da_pagina(page: Page, api, base_url):
    api.public_base_url = None
    do_login(page, base_url, api)
    origem = page.evaluate("window.location.origin")
    rows = page.locator("#epList .ep")
    expect(rows).to_have_count(17)
    for n in range(1, 5):
        expect(rows.nth(n - 1).locator(".ep-url")).to_have_text(f"{origem}/live{n}.json")
