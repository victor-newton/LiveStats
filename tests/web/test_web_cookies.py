"""WEB-CK-01..05: colar cookies do EditThisCookie."""

from __future__ import annotations

import json
import re

import pytest
from api_double import error, json_reply
from playwright.sync_api import Page, expect
from web_helpers import do_login, wait_until

COOKIES = [
    {"name": "SID", "value": "SEGREDO-UNICO-123", "domain": ".youtube.com", "path": "/", "secure": True, "httpOnly": True},
    {"name": "HSID", "value": "OUTRO-SEGREDO-456", "domain": ".youtube.com"},
]


def post_cookies(api):
    return api.requests("POST", "/api/admin/cookies")


def test_web_ck_01_instrucoes_do_editthiscookie_e_textarea(page: Page, api, base_url):
    do_login(page, base_url, api)
    passos = page.locator("#cookieSteps")
    expect(passos).to_be_visible()
    expect(passos).to_contain_text("Como exportar pelo EditThisCookie")
    expect(passos).to_contain_text("studio.youtube.com")
    expect(passos).to_contain_text("Export")
    expect(passos).to_contain_text("Cole aqui abaixo e clique em Salvar")
    area = page.get_by_label("JSON de cookies do EditThisCookie")
    expect(area).to_be_visible()
    expect(area).to_have_attribute("placeholder", re.compile(r"Cole aqui o JSON do EditThisCookie"))
    expect(page.get_by_role("button", name="Salvar cookies e recarregar scraper")).to_be_visible()


@pytest.mark.parametrize(
    "texto, esperado, exato",
    [
        ("", "Cole o JSON dos cookies antes de salvar.", True),
        ("   \n  ", "Cole o JSON dos cookies antes de salvar.", True),
        ('[{"name": "a", ', "JSON inválido: ", False),
        ("isto nem é json", "JSON inválido: ", False),
        ('{"name": "a", "value": "b"}', "JSON inválido: Deve ser um array JSON", True),
        ("42", "JSON inválido: Deve ser um array JSON", True),
    ],
)
def test_web_ck_02_validacao_no_navegador_nao_envia_nada(page: Page, api, base_url, texto, esperado, exato):
    do_login(page, base_url, api)
    page.locator("#cookiesInput").fill(texto)
    page.get_by_role("button", name="Salvar cookies").click()
    msg = page.locator("#cookiesMsg")
    expect(msg).to_be_visible()
    if exato:
        expect(msg).to_have_text(esperado)
    else:
        expect(msg).to_contain_text(esperado)
        assert len(msg.inner_text()) > len(esperado)  # traz o motivo do parser
    page.wait_for_timeout(200)
    assert post_cookies(api) == []


def test_web_ck_03_envio_valido_manda_o_array_com_csrf_e_atualiza_o_status(page: Page, api, base_url):
    do_login(page, base_url, api)
    expect(page.locator("#cookieBadge")).to_have_text("❌ Nenhum cookie — scraper não autenticado")
    page.evaluate("window.__sem_recarga = 'ainda aqui'")
    csrf = next(iter(api.sessions.values()))["csrf"]

    page.locator("#cookiesInput").fill(json.dumps(COOKIES, indent=2))
    page.get_by_role("button", name="Salvar cookies").click()

    msg = page.locator("#cookiesMsg")
    expect(msg).to_contain_text("2 cookies salvos com sucesso!")
    expect(msg).to_contain_text("O scraper vai recarregar automaticamente")
    expect(page.locator("#cookiesInput")).to_have_value("")  # o segredo não fica na tela
    reqs = post_cookies(api)
    assert len(reqs) == 1
    assert reqs[0].body == COOKIES  # o ARRAY, sem embrulho
    assert isinstance(reqs[0].body, list)
    assert reqs[0].headers["x-csrf-token"] == csrf
    assert reqs[0].headers["content-type"].startswith("application/json")
    assert reqs[0].status == 200
    # status atualizado sem recarregar a página
    expect(page.locator("#cookieBadge")).to_have_text("✅ 2 cookies carregados")
    assert page.evaluate("window.__sem_recarga") == "ainda aqui"


@pytest.mark.parametrize(
    "resposta, trecho",
    [
        (error(400, "Esperado um array JSON com pelo menos 1 cookie"), "Esperado um array JSON"),
        (error(403, "CSRF token inválido ou ausente"), "CSRF token inválido ou ausente"),
        (
            json_reply(422, {"detail": [{"loc": ["body", 0, "name"], "msg": "Valor inválido (type)", "type": "type"}]}),
            "Valor inválido (type)",
        ),
        (error(503, "Chave pública de cifragem indisponível"), "Chave pública de cifragem indisponível"),
    ],
)
def test_web_ck_04_erros_do_servidor_aparecem_como_texto(page: Page, api, base_url, resposta, trecho):
    do_login(page, base_url, api)
    api.override("POST", "/api/admin/cookies", resposta)
    page.locator("#cookiesInput").fill('[{"name":"a","value":"b"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    msg = page.locator("#cookiesMsg")
    expect(msg).to_contain_text(trecho)
    expect(msg).to_have_class("msg err")
    expect(page.locator("#panelView")).to_be_visible()
    assert post_cookies(api)[0].status == resposta.status
    # em erro de validação o texto colado permanece, para o usuário corrigir
    if resposta.status in (400, 422, 503):
        expect(page.locator("#cookiesInput")).to_have_value('[{"name":"a","value":"b"}]')


def test_web_ck_04b_erro_400_real_do_contrato_cookie_sem_campos(page: Page, api, base_url):
    do_login(page, base_url, api)
    page.locator("#cookiesInput").fill('[{"name": "so-o-nome"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#cookiesMsg")).to_contain_text("Cookie #0 sem campo 'name' ou 'value'")
    assert post_cookies(api)[0].status == 400


def test_web_ck_04c_401_volta_ao_login(page: Page, api, base_url):
    do_login(page, base_url, api)
    api.expire_sessions()
    page.locator("#cookiesInput").fill('[{"name":"a","value":"b"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#panelView")).to_be_hidden()
    expect(page.locator("#loginMsg")).to_contain_text("Sessão expirada")
    assert post_cookies(api)[0].status == 401
    # o texto colado não sobrevive à perda da sessão
    assert page.locator("#cookiesInput").input_value() == ""


def test_web_ck_05_cookies_nunca_reaparecem(page: Page, api, base_url):
    do_login(page, base_url, api)
    page.locator("#cookiesInput").fill(json.dumps(COOKIES))
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#cookiesMsg")).to_contain_text("2 cookies salvos")
    wait_until(lambda: len(api.requests("GET", "/api/admin/workers/status")) >= 2, "status atualizado")

    segredos = ["SEGREDO-UNICO-123", "OUTRO-SEGREDO-456"]
    visivel = page.evaluate(
        """() => [
            document.documentElement.outerHTML,
            document.body.innerText,
            [...document.querySelectorAll('input, textarea')].map((e) => e.value).join('|'),
            JSON.stringify(Object.entries(localStorage)),
            JSON.stringify(Object.entries(sessionStorage)),
            document.cookie,
        ].join('\\n')"""
    )
    for s in segredos:
        assert s not in visivel
