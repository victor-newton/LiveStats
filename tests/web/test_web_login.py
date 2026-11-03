"""WEB-LOGIN-01..05: login, CSRF em memória, expiração e logout."""

from __future__ import annotations

from web_helpers import Watch, do_login, fetch_status, freeze_clock, open_tab
from playwright.sync_api import Page, expect

ADMIN_PATHS = ("/api/admin/slots", "/api/admin/workers/status")


def current_csrf(api) -> str:
    assert len(api.sessions) == 1, "esperava exatamente uma sessão ativa no dublê"
    return next(iter(api.sessions.values()))["csrf"]


def test_web_login_01_sem_sessao_mostra_so_o_login(page: Page, api, base_url, watch: Watch):
    page.goto(f"{base_url}/")
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#panelView")).to_be_hidden()
    expect(page.locator("#userBox")).to_be_hidden()
    expect(page.get_by_label("Usuário")).to_be_visible()
    expect(page.get_by_label("Senha")).to_be_visible()
    assert page.title() == "LiveStats"
    # a única chamada à api é a verificação de sessão
    page.wait_for_load_state("networkidle")
    api_calls = [r.path for r in api.log if r.path.startswith("/api/")]
    assert api_calls == ["/api/admin/session"]
    assert not any(p in url for url in watch.requests for p in ADMIN_PATHS)


def test_web_login_02_senha_errada_e_bloqueio_tem_mensagens_distintas(page: Page, api, base_url):
    page.goto(f"{base_url}/")
    senha = page.get_by_label("Senha")
    msg = page.locator("#loginMsg")

    page.get_by_label("Usuário").fill(api.username)
    senha.fill("senha-errada")
    page.get_by_role("button", name="Entrar").click()
    expect(msg).to_have_text("Credenciais inválidas")
    expect(senha).to_have_value("")
    expect(page.locator("#panelView")).to_be_hidden()

    for _ in range(4):  # completa 5 falhas
        senha.fill("senha-errada")
        page.get_by_role("button", name="Entrar").click()
        expect(senha).to_have_value("")
    assert len(api.requests("POST", "/api/admin/login")) == 5

    # 6ª tentativa, até com a senha certa: bloqueada (429)
    senha.fill(api.password)
    page.get_by_role("button", name="Entrar").click()
    expect(msg).to_contain_text("Muitas tentativas")
    expect(msg).to_contain_text("60 s")  # Retry-After
    expect(msg).not_to_contain_text("Credenciais inválidas")
    expect(senha).to_have_value("")
    expect(page.locator("#panelView")).to_be_hidden()
    assert api.requests("POST", "/api/admin/login")[-1].status == 429


def test_web_login_03_csrf_so_em_memoria_e_enviado_nas_escritas(page: Page, api, base_url):
    api.set_stats(1, api.live_stats(1))
    do_login(page, base_url, api, tab="lives")
    csrf = current_csrf(api)

    page.locator("#inp1").fill("https://youtube.com/watch?v=dQw4w9WgXcQ")
    page.get_by_role("button", name="Salvar slot 1", exact=True).click()
    expect(page.locator("#saveMsg1")).to_be_visible()
    open_tab(page, "inscritos")
    page.locator("#cookiesInput").fill('[{"name":"SID","value":"abc","domain":".youtube.com"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#cookiesMsg")).to_contain_text("cookies salvos")
    page.get_by_role("button", name="Sair").click()
    expect(page.locator("#loginView")).to_be_visible()

    for method, path in (("PUT", "/api/admin/slots"), ("POST", "/api/admin/cookies"), ("POST", "/api/admin/logout")):
        reqs = api.requests(method, path)
        assert len(reqs) == 1, (method, path)
        assert reqs[0].headers.get("x-csrf-token") == csrf
        assert reqs[0].status == 200


def test_web_login_03b_token_nao_persiste_em_lugar_nenhum(page: Page, api, base_url):
    do_login(page, base_url, api)
    csrf = current_csrf(api)
    # controle: o dublê realmente recusa escrita sem o token (o teste não é vazio)
    assert fetch_status(page, "PUT", "/api/admin/slots", {"slots": ["", "", "", ""]}) == 403

    armazenado = page.evaluate(
        """() => JSON.stringify({
            local: Object.entries(localStorage),
            session: Object.entries(sessionStorage),
            cookie: document.cookie,
        })"""
    )
    assert csrf not in armazenado
    html = page.evaluate("() => document.documentElement.outerHTML")
    assert csrf not in html
    texto = page.evaluate(
        """() => [...document.querySelectorAll('*')].flatMap(
            (e) => [e.textContent, ...[...e.attributes].map((a) => a.value), e.value || '']
        ).join('\\n')"""
    )
    assert csrf not in texto
    assert csrf not in page.url


def test_web_login_04_401_durante_a_sessao_volta_ao_login_e_limpa_o_painel(page: Page, api, base_url):
    api.slots[0] = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    api.set_stats(1, api.live_stats(1, titulo="Titulo que nao pode sobrar"))
    freeze_clock(page)
    do_login(page, base_url, api, tab="lives")
    expect(page.locator(".slot-title:not([hidden])")).to_have_text("Titulo que nao pode sobrar")
    expect(page.locator("#inp1")).to_have_value("https://youtube.com/watch?v=dQw4w9WgXcQ")

    api.expire_sessions()
    page.clock.run_for(15000)

    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#panelView")).to_be_hidden()
    expect(page.locator("#loginMsg")).to_contain_text("Sessão expirada")
    # nada do painel antigo restou no DOM
    assert page.locator("#inp1").input_value() == ""
    assert page.locator("#slotsGrid .slot-title:not([hidden])").count() == 0
    assert "Titulo que nao pode sobrar" not in page.evaluate("() => document.body.innerText")
    assert page.locator(".ep").count() == 0  # nenhum endereço (de nenhuma aba) sobra no DOM
    # e o painel não continua consultando a api
    antes = len(api.requests("GET", "/api/admin/slots"))
    page.clock.run_for(45000)
    assert len(api.requests("GET", "/api/admin/slots")) == antes


def test_web_login_05_logout_chama_a_api_com_csrf_e_volta_ao_login(page: Page, api, base_url):
    do_login(page, base_url, api)
    csrf = current_csrf(api)
    page.get_by_role("button", name="Sair").click()
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#panelView")).to_be_hidden()
    reqs = api.requests("POST", "/api/admin/logout")
    assert len(reqs) == 1
    assert reqs[0].headers["x-csrf-token"] == csrf
    assert api.sessions == {}  # encerrada no servidor, não só escondida
    assert fetch_status(page, "GET", "/api/admin/slots") == 401
