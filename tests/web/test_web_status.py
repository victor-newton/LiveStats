"""WEB-ST-01: status de inscritos, cookies e workers."""

from __future__ import annotations

from playwright.sync_api import Page, expect
from web_helpers import do_login, freeze_clock, open_tab, wait_until

LEITURA = {"inscritos": "460.600", "raw": "460.600", "atualizado_em": "2026-11-02T14:32:10.654321Z"}


def test_web_st_01_mostra_leitura_cookies_e_workers(page: Page, api, base_url):
    api.subscribers = dict(LEITURA)
    api.cookies = {"configured": True, "count": 12, "uploaded_at": "2026-11-02T10:00:00Z", "applied": True, "source": "admin", "created_at": "2026-11-02T10:00:00Z", "renewed_from": None}
    api.lives_worker = api.worker_state("ok")
    api.inscritos_worker = {
        **api.worker_state("degraded", consecutive_failures=3, last_error_code="counter_not_found",
                           last_error_message="Contador não encontrado na página"),
        "auth_state": "ok",
    }
    do_login(page, base_url, api)

    stats = page.locator(".stat-row")
    expect(stats.get_by_text("Inscritos atuais")).to_be_visible()
    expect(page.locator("#subsCurrent")).to_have_text("460.600")
    expect(stats.get_by_text("Última leitura")).to_be_visible()
    expect(page.locator("#subsLast")).to_have_text("2026-11-02T14:32:10.654321Z")
    expect(page.locator("#cookieBadge")).to_have_text("✅ 12 cookies carregados")
    expect(page.locator("#cookieBadge")).to_have_class("status-badge ok")

    # D36: o worker de inscritos fica na aba Inscritos; o de lives, na aba Lives
    expect(page.locator('#workerList [data-worker="lives"]')).to_have_count(0)
    expect(page.locator('#workerListLives [data-worker="inscritos"]')).to_have_count(0)
    ins = page.locator('#workerList [data-worker="inscritos"]')
    expect(ins.locator(".worker-name")).to_have_text("Inscritos")
    expect(ins.locator(".worker-state")).to_have_text("degradado")
    expect(ins.locator(".worker-detail")).to_contain_text("falhas seguidas: 3")
    expect(ins.locator(".worker-detail")).to_contain_text("Contador não encontrado na página")
    expect(page.locator("#authAlert")).to_be_hidden()
    open_tab(page, "lives")
    lives = page.locator('#workerListLives [data-worker="lives"]')
    expect(lives).to_be_visible()
    expect(lives.locator(".worker-name")).to_have_text("Lives")
    expect(lives.locator(".worker-state")).to_have_text("ok")


def test_web_st_01b_sem_cookies_e_sem_leitura(page: Page, api, base_url):
    api.lives_worker = api.worker_state("unknown", last_heartbeat_at=None, last_success_at=None)
    api.inscritos_worker = {**api.worker_state("unknown", last_heartbeat_at=None, last_success_at=None),
                            "auth_state": "no_cookies"}
    do_login(page, base_url, api)
    expect(page.locator("#subsCurrent")).to_have_text("—")
    expect(page.locator("#subsLast")).to_have_text("—")
    expect(page.locator("#cookieBadge")).to_have_text("❌ Nenhum cookie — scraper não autenticado")
    expect(page.locator("#cookieBadge")).to_have_class("status-badge bad")
    open_tab(page, "lives")
    expect(page.locator('#workerListLives [data-worker="lives"] .worker-state')).to_have_text("desconhecido")
    open_tab(page, "inscritos")
    expect(page.locator("#authAlert")).to_contain_text("nenhum cookie foi aplicado")


def test_web_st_01c_cookies_expirados_geram_aviso_destacado(page: Page, api, base_url):
    api.cookies = {"configured": True, "count": 12, "uploaded_at": "2026-11-02T10:00:00Z", "applied": True, "source": "admin", "created_at": "2026-11-02T10:00:00Z", "renewed_from": None}
    api.inscritos_worker = {
        **api.worker_state("error", consecutive_failures=5, last_error_code="login_required",
                           last_error_message="O YouTube Studio pediu login"),
        "auth_state": "login_required",
    }
    do_login(page, base_url, api)
    alerta = page.locator("#authAlert")
    expect(alerta).to_be_visible()
    expect(alerta).to_contain_text("cookies expirados")
    expect(alerta).to_have_attribute("role", "alert")
    expect(page.locator('#workerList [data-worker="inscritos"] .worker-state')).to_have_text("erro")
    # o aviso é destacado: fundo/borda de erro, não texto comum
    assert alerta.evaluate("e => getComputedStyle(e).borderTopWidth") != "0px"


def test_web_st_01d_status_se_atualiza_a_cada_15s(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    expect(page.locator("#subsCurrent")).to_have_text("—")
    api.subscribers = dict(LEITURA)
    api.inscritos_worker = {**api.worker_state("ok"), "auth_state": "login_required"}
    page.clock.run_for(15000)
    wait_until(lambda: len(api.requests("GET", "/api/admin/workers/status")) >= 2, "novo status")
    expect(page.locator("#subsCurrent")).to_have_text("460.600")
    expect(page.locator("#authAlert")).to_be_visible()
    # e o aviso some quando o worker volta a ficar autenticado
    api.inscritos_worker = {**api.worker_state("ok"), "auth_state": "ok"}
    page.clock.run_for(15000)
    expect(page.locator("#authAlert")).to_be_hidden()
