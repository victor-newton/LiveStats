"""Estado que NÃO pode sobreviver à troca de sessão (WEB-LOGIN-06/07): token CSRF e mensagens."""

from __future__ import annotations

from api_double import error
from playwright.sync_api import Page, expect
from web_helpers import do_login, freeze_clock


def current_csrf(api) -> str:
    assert len(api.sessions) == 1, "esperava exatamente uma sessão ativa no dublê"
    return next(iter(api.sessions.values()))["csrf"]


def tentar_login(page: Page, usuario: str, senha: str) -> None:
    page.get_by_label("Usuário").fill(usuario)
    page.get_by_label("Senha").fill(senha)
    page.get_by_role("button", name="Entrar").click()


def test_web_login_06_token_csrf_antigo_nao_sobrevive_ao_logout_nem_a_expiracao(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    antigo = current_csrf(api)

    # 1) depois do logout, o próximo POST (login com senha errada) não leva o token antigo
    page.get_by_role("button", name="Sair").click()
    expect(page.locator("#loginView")).to_be_visible()
    tentar_login(page, api.username, "senha-errada")
    expect(page.locator("#loginMsg")).to_have_text("Credenciais inválidas")
    logins = api.requests("POST", "/api/admin/login")
    assert logins[-1].status == 401
    assert "x-csrf-token" not in logins[-1].headers
    assert antigo not in str(logins[-1].headers)

    # 2) novo login: a sessão nova tem token novo, e é ele que vai nas escritas
    tentar_login(page, api.username, api.password)
    expect(page.locator("#panelView")).to_be_visible()
    novo = current_csrf(api)
    assert novo != antigo
    assert "x-csrf-token" not in api.requests("POST", "/api/admin/login")[-1].headers
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()
    assert api.requests("PUT", "/api/admin/slots")[-1].headers["x-csrf-token"] == novo

    # 3) perda de sessão por expiração (401): o token também é descartado
    api.expire_sessions()
    page.clock.run_for(15000)
    expect(page.locator("#loginView")).to_be_visible()
    tentar_login(page, api.username, "senha-errada")
    expect(page.locator("#loginMsg")).to_have_text("Credenciais inválidas")
    ultimo = api.requests("POST", "/api/admin/login")[-1]
    assert "x-csrf-token" not in ultimo.headers
    assert novo not in str(ultimo.headers)


def test_web_login_07_mensagens_de_erro_somem_quando_o_estado_muda(page: Page, api, base_url):
    # login: erro, depois sucesso, depois logout -> o texto do erro não pode sobrar
    page.goto(f"{base_url}/")
    tentar_login(page, api.username, "senha-errada")
    expect(page.locator("#loginMsg")).to_have_text("Credenciais inválidas")
    tentar_login(page, api.username, api.password)
    expect(page.locator("#panelView")).to_be_visible()
    expect(page.locator("#loginMsg")).to_have_text("")
    page.get_by_role("button", name="Sair").click()
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#loginMsg")).to_have_text("")
    assert "Credenciais" not in page.locator("#loginView").inner_text()

    # salvar slots: erro 422, depois sucesso -> o erro é apagado (texto, não só escondido)
    tentar_login(page, api.username, api.password)
    expect(page.locator("#panelView")).to_be_visible()
    page.get_by_label("Slot 1", exact=True).fill("https://example.com/nao-e-youtube")
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveErr")).to_contain_text("Slot 1")
    page.get_by_label("Slot 1", exact=True).fill("dQw4w9WgXcQ")
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()
    expect(page.locator("#saveErr")).to_have_text("")

    # erros de slots e de cookies não vazam para a sessão seguinte
    page.get_by_label("Slot 2", exact=True).fill("https://example.com/de-novo-invalido")
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveErr")).to_contain_text("Slot 2")
    page.locator("#cookiesInput").fill('[{"name": "so-o-nome"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#cookiesMsg")).to_contain_text("Cookie #0")
    page.get_by_role("button", name="Sair").click()
    expect(page.locator("#loginView")).to_be_visible()
    tentar_login(page, api.username, api.password)
    expect(page.locator("#panelView")).to_be_visible()
    expect(page.locator("#saveErr")).to_have_text("")
    expect(page.locator("#cookiesMsg")).to_have_text("")
    assert "Cookie #0" not in page.locator("#panelView").inner_text()


def test_web_login_07b_aviso_de_falha_na_atualizacao_some_quando_volta_ao_normal(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    expect(page.locator("#panelNotice")).to_have_text("")

    api.override("GET", "/api/admin/slots", error(503, "Banco indisponível"))
    page.clock.run_for(15000)
    expect(page.locator("#panelNotice")).to_contain_text("Banco indisponível")
    expect(page.locator("#panelNotice")).to_be_visible()

    api.overrides.clear()
    page.clock.run_for(15000)
    expect(page.locator("#panelNotice")).to_have_text("")
    assert "Banco indisponível" not in page.locator("body").inner_text()


def test_web_login_07c_aviso_de_cookies_expirados_some_quando_o_worker_volta(page: Page, api, base_url):
    freeze_clock(page)
    api.inscritos_worker = {**api.worker_state("error"), "auth_state": "login_required"}
    do_login(page, base_url, api)
    expect(page.locator("#authAlert")).to_contain_text("cookies expirados")
    api.inscritos_worker = {**api.worker_state("ok"), "auth_state": "ok"}
    page.clock.run_for(15000)
    expect(page.locator("#authAlert")).to_have_text("")
    assert "cookies expirados" not in page.locator("body").inner_text()
