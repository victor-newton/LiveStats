"""INT-02, INT-09 (animacao), INT-11: cookies -> worker-inscritos -> Studio falso -> /inscritos.json -> overlay/painel."""

from __future__ import annotations

import re
import time

import pytest
from e2e_browser import chromium_context, ui_login, watch_dialogs
from e2e_stack import SCRAPE_INTERVAL, USERNAME, parse_ts, wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]


def test_int_02_fluxo_de_cookies_de_ponta_a_ponta(stack):
    stack.fake_set(login_ok=True, subscribers="460600")
    t_up = time.time()
    result = stack.upload_cookies()
    assert result["cookies_count"] == 2

    wait_until(
        lambda: stack.workers_status()["cookies"]["applied"] and stack.public_json("/inscritos.json")["inscritos"] == "460600",
        timeout=90,
        interval=1,
    )

    # o Chromium do worker abriu o Studio falso ENVIANDO os cookies que o admin colou (decifrados no worker)
    studio = [r for r in stack.fake_requests("fake-studio", "studio", since=t_up) if r["cookie_ok"]]
    assert studio, "o Studio falso nunca recebeu o cookie de autenticacao decifrado"
    header_sent = studio[0]["cookie_header"]
    for cookie in (stack.auth_cookie, stack.extra_cookie):
        assert f"{cookie['name']}={cookie['value']}" in header_sent
    # antes da pagina do Studio o worker abre o "www" (fluxo do scraper original)
    assert stack.fake_requests("fake-studio", "www", since=t_up)

    body = stack.public_json("/inscritos.json")
    assert list(body) == ["inscritos", "raw", "atualizado_em"]
    assert body["inscritos"] == "460600" and body["raw"] == "460600"

    # atualizado_em avanca a cada ciclo de leitura (SCRAPE_INTERVAL do ambiente de teste)
    stamps = [parse_ts(body["atualizado_em"])]
    while len(stamps) < 4:
        previous = stamps[-1]
        nxt = wait_until(
            lambda p=previous: (lambda t: t if t > p else None)(parse_ts(stack.public_json("/inscritos.json")["atualizado_em"])),
            timeout=3 * SCRAPE_INTERVAL,
            interval=0.5,
        )
        stamps.append(nxt)
    gaps = [(stamps[i + 1] - stamps[i]).total_seconds() for i in range(3)]
    assert all(SCRAPE_INTERVAL - 1.5 <= g <= SCRAPE_INTERVAL + 3 for g in gaps), gaps

    # o numero muda no Studio -> chega ao /inscritos.json no ciclo seguinte (a pagina nao e recarregada)
    stack.fake_set(subscribers="461234")
    wait_until(lambda: stack.public_json("/inscritos.json")["inscritos"] == "461234", timeout=3 * SCRAPE_INTERVAL, interval=1)
    assert stack.public_json("/api/public/subscribers")["raw"] == "461234"
    stack.fake_set(subscribers="460600")
    wait_until(lambda: stack.public_json("/inscritos.json")["inscritos"] == "460600", timeout=3 * SCRAPE_INTERVAL, interval=1)


def _overlay_digits(page) -> str:
    return page.evaluate(
        """() => [...document.querySelectorAll('#contador .digito-inner, .digito-inner')]
            .map(e => { const m = /translateY\\(-?(\\d+)(?:em)?\\)/.exec(e.style.transform); return m ? m[1] : '?'; }).join('')"""
    )


def test_int_09_overlay_contador_anima_ao_mudar_o_numero_no_worker(stack):
    stack.ensure_subs("460600")
    with chromium_context() as ctx:
        page = ctx.new_page()
        dialogs = watch_dialogs(page)
        page.goto("http://127.0.0.1:8080/contador")
        wait_until(lambda: _overlay_digits(page) == "460600", timeout=20, interval=0.5)

        stack.fake_set(subscribers="461789")
        # o worker le em <= SCRAPE_INTERVAL e o overlay consulta a cada 5 s
        wait_until(lambda: _overlay_digits(page) == "461789", timeout=SCRAPE_INTERVAL + 5 + 8, interval=0.25)
        # a troca foi ANIMADA (a transicao do carrossel esta ativa nos digitos que mudaram), nao recarga da pagina
        durations = page.evaluate(
            "() => [...document.querySelectorAll('.digito-inner')].map(e => getComputedStyle(e).transitionDuration)"
        )
        assert "0.45s" in durations, durations  # animarParaValor: 0,45 s nos digitos que mudaram
        assert page.evaluate("performance.getEntriesByType('navigation').length") == 1
        assert dialogs == []
    stack.fake_set(subscribers="460600")
    stack.ensure_subs("460600")


def test_int_11_cookies_expirados_avisam_no_painel_e_mantem_o_ultimo_valor(stack):
    stack.ensure_subs("460600")
    before = stack.public_json("/inscritos.json")
    assert before["inscritos"] == "460600"
    try:
        stack.fake_set(login_ok=False)  # o Studio falso passa a redirecionar a pagina carregada para o login

        def expired():
            s = stack.workers_status()["workers"]["inscritos"]
            return s["auth_state"] == "login_required" and s["state"] == "error" and s["last_error_code"] == "login_required"

        wait_until(expired, timeout=3 * SCRAPE_INTERVAL + 10, interval=1)

        # o ultimo valor continua sendo servido (nao zera, nao some)
        later = stack.public_json("/inscritos.json")
        assert later["inscritos"] == "460600" and later["raw"] == "460600", later
        assert parse_ts(later["atualizado_em"]) >= parse_ts(before["atualizado_em"])
        time.sleep(2 * SCRAPE_INTERVAL)  # sem login nao ha leitura nova: o valor e o carimbo ficam congelados
        frozen = stack.public_json("/inscritos.json")
        assert frozen == later

        # o painel avisa que os cookies expiraram
        with chromium_context() as ctx:
            page = ctx.new_page()
            ui_login(page, "http://127.0.0.1:8081", USERNAME, stack.password)
            alert = page.locator("#authAlert")
            alert.wait_for(state="visible", timeout=20000)
            text = alert.inner_text()
            assert re.search(r"cookie", text, re.I), text
    finally:
        stack.fake_set(login_ok=True)
        stack.upload_cookies()  # "novos cookies" => conjunto novo => o worker recarrega o Studio
    wait_until(
        lambda: stack.workers_status()["workers"]["inscritos"]["auth_state"] == "ok"
        and stack.workers_status()["workers"]["inscritos"]["state"] == "ok",
        timeout=90,
        interval=1,
    )
    with chromium_context() as ctx:
        page = ctx.new_page()
        ui_login(page, "http://127.0.0.1:8081", USERNAME, stack.password)
        assert not page.locator("#authAlert").is_visible()
