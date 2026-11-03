"""WEB-XSS-01/02: tudo que vem do servidor é renderizado como TEXTO."""

from __future__ import annotations

from api_double import error, json_reply
from playwright.sync_api import Page, expect
from web_helpers import Watch, do_login, no_html_injected, wait_until

IMG_PAYLOAD = "<img src=x onerror=alert(1)>"


def test_web_xss_01_titulo_com_html_aparece_como_texto(page: Page, api, base_url, watch: Watch):
    api.slots[0] = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    api.set_stats(1, api.live_stats(1, titulo=IMG_PAYLOAD))
    do_login(page, base_url, api)

    card = page.locator(".slot-card").first
    expect(card.locator(".slot-title")).to_have_text(IMG_PAYLOAD)
    assert card.locator("img").count() == 0
    assert page.locator("img").count() == 0
    assert page.evaluate("window.__xss") is None
    page.wait_for_timeout(300)  # dá tempo de um onerror (se existisse) disparar
    assert watch.dialogs == []
    # o texto literal está no DOM como nó de texto, não como marcação
    assert card.locator(".slot-title").evaluate("e => e.children.length") == 0
    assert IMG_PAYLOAD in card.locator(".slot-title").evaluate("e => e.textContent")


def test_web_xss_02_outros_payloads_viram_texto(page: Page, api, base_url, watch: Watch):
    script = "\"><script>window.__xss=1</script>"
    svg = "<svg onload=window.__xss=1>"
    js_url = "javascript:window.__xss=1"
    html_user = "<img src=x onerror=window.__xss=1>"
    api.username = html_user
    api.public_base_url = 'https://x.example/"><img src=x onerror=window.__xss=1>'
    api.slots[0] = "https://youtube.com/watch?v=dQw4w9WgXcQ"
    api.slots[3] = js_url
    api.set_stats(1, api.live_stats(1, titulo=script))
    api.set_stats(2, api.live_stats(2, titulo=svg))
    api.lives_worker = api.worker_state("error", last_error_message=svg, last_error_code="http_error")
    do_login(page, base_url, api)

    expect(page.locator(".slot-title").nth(0)).to_have_text(script)
    expect(page.locator(".slot-title").nth(1)).to_have_text(svg)
    expect(page.locator("#inp4")).to_have_value(js_url)  # só valor de campo; nunca vira link
    expect(page.locator("#userName")).to_have_text(html_user)
    expect(page.locator("#epList .ep-url").first).to_have_text(api.public_base_url.rstrip("/") + "/live1.json")
    expect(page.locator("#workerList")).to_contain_text(svg)
    assert page.locator("a[href^='javascript']").count() == 0
    no_html_injected(page)

    # mensagens de erro do servidor (detail) em salvar slots e cookies
    api.override("POST", "/api/admin/cookies", error(400, script))
    page.locator("#cookiesInput").fill('[{"name":"a","value":"b"}]')
    page.get_by_role("button", name="Salvar cookies").click()
    expect(page.locator("#cookiesMsg")).to_contain_text(script)
    no_html_injected(page)

    api.override("PUT", "/api/admin/slots", error(400, svg))
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveErr")).to_have_text(svg)
    no_html_injected(page)

    page.wait_for_timeout(300)
    assert watch.dialogs == []


def test_web_xss_02b_html_em_erro_de_login_vira_texto(page: Page, api, base_url, watch: Watch):
    payload = "<img src=x onerror=window.__xss=1>"
    api.override("POST", "/api/admin/login", error(503, payload))
    page.goto(f"{base_url}/")
    page.get_by_label("Usuário").fill("qualquer")
    page.get_by_label("Senha").fill("qualquer")
    page.get_by_role("button", name="Entrar").click()
    expect(page.locator("#loginMsg")).to_have_text(payload)
    no_html_injected(page)

    # erro de validação (422) também só vira texto, sem o valor enviado
    api.override(
        "POST",
        "/api/admin/login",
        json_reply(422, {"detail": [{"loc": ["body", "password"], "msg": payload, "type": "x"}]}),
    )
    page.get_by_role("button", name="Entrar").click()
    wait_until(lambda: len(api.requests("POST", "/api/admin/login")) == 2, "segunda tentativa de login")
    expect(page.locator("#loginMsg")).to_have_text(payload)
    no_html_injected(page)
