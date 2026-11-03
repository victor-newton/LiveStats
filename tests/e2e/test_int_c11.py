# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""INT-C11-01..03 (D35): canal e aparencia configurados no painel (8081) chegam ao worker e aos overlays (8080), pela pilha real."""

from __future__ import annotations

import json
import time

import pytest
from e2e_browser import chromium_context, ui_login, watch_dialogs
from e2e_stack import ADMIN_PORT, LIVE_INTERVAL, PUBLIC_PORT, SCRAPE_INTERVAL, USERNAME, wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

ADMIN = f"https://127.0.0.1:{ADMIN_PORT}"
PUBLIC = f"http://127.0.0.1:{PUBLIC_PORT}"
NEW_CHANNEL = "UC" + "N3wCh4nn3l" * 2 + "_-"  # UC + 22 caracteres
assert len(NEW_CHANNEL) == 24


def _guard(page, sink: list[str]) -> None:
    page.on("console", lambda m: sink.append(f"console:{m.text}") if "Content Security Policy" in m.text or "Refused to" in m.text else None)
    page.on("request", lambda r: sink.append(f"externo:{r.url}") if not r.url.startswith(("http://127.0.0.1", "https://127.0.0.1", "data:")) else None)


def _studio_channels(stack, since: float) -> list[str]:
    """Canal de cada abertura do Studio falso desde `since` (o caminho e /channel/<id>/analytics/...)."""
    return [r["path"].split("/")[2] for r in stack.fake_requests("fake-studio", "studio", since=since)]


def test_int_c11_01_trocar_o_canal_no_painel_faz_o_worker_abrir_o_canal_novo(stack):
    stack.ensure_subs("460600")
    original = stack.channel_id
    problems: list[str] = []
    t0 = time.time()
    try:
        with chromium_context(ignore_https_errors=True) as ctx:
            admin = ctx.new_page()
            dialogs = watch_dialogs(admin)
            _guard(admin, problems)
            ui_login(admin, ADMIN, USERNAME, stack.password)
            # canal invalido e barrado no navegador; nada chega a api
            admin.get_by_label("ID do canal do YouTube").fill("UC123")
            admin.get_by_role("button", name="Salvar canal").click()
            assert "inválido" in admin.locator("#channelErr").text_content()
            assert stack.api("GET", "/api/admin/settings")["body"].count(NEW_CHANNEL) == 0

            admin.get_by_label("ID do canal do YouTube").fill(NEW_CHANNEL)
            admin.get_by_role("button", name="Salvar canal").click()
            admin.locator("#channelOk").wait_for(state="visible", timeout=15000)
            assert json.loads(stack.api("GET", "/api/admin/settings")["body"])["channel_id"] == NEW_CHANNEL

            # o worker le o banco a cada ciclo e recarrega o Studio no canal novo (o falso so conhece o caminho)
            wait_until(lambda: NEW_CHANNEL in _studio_channels(stack, t0), timeout=SCRAPE_INTERVAL * 3 + 60, interval=1)
            wait_until(
                lambda: stack.workers_status()["workers"]["inscritos"]["state"] == "ok" and stack.workers_status()["cookies"]["applied"],
                timeout=60, interval=1,
            )
            assert dialogs == []
    finally:
        r = stack.api("PUT", "/api/admin/settings", {"channel_id": original})
        assert r["status"] == 200, r["body"]
        t1 = time.time()
        wait_until(lambda: original in _studio_channels(stack, t1), timeout=SCRAPE_INTERVAL * 3 + 60, interval=1)
    assert problems == []


def test_int_c11_02_fonte_e_cor_do_painel_chegam_aos_overlays_e_ao_json_novo_nao_ao_legado(stack):
    stack.ensure_lives()
    stack.ensure_subs("460600")
    problems: list[str] = []
    try:
        with chromium_context(ignore_https_errors=True) as ctx:
            admin = ctx.new_page()
            dialogs = watch_dialogs(admin)
            _guard(admin, problems)
            ui_login(admin, ADMIN, USERNAME, stack.password)
            admin.locator("#font-subs option").first.wait_for(state="attached", timeout=15000)
            assert admin.locator("#font-subs option").all_inner_texts() == json.loads(stack.api("GET", "/api/admin/fonts")["body"])["fonts"]
            admin.get_by_label("Fonte do contador", exact=True).select_option("Impact")
            admin.get_by_label("Cor do texto do contador", exact=True).fill("#ff8800")
            admin.get_by_label("Fonte da live 1", exact=True).select_option("Georgia")
            admin.get_by_label("Cor do texto da live 1", exact=True).fill("#12ab34")
            admin.get_by_role("button", name="Salvar aparência").click()
            admin.locator("#lookOk").wait_for(state="visible", timeout=15000)

            subs = wait_until(lambda: (lambda j: j if j.get("font") == "Impact" else None)(stack.public_json("/api/public/subscribers")), timeout=15, interval=0.5)
            assert subs["text_color"] == "#FF8800"
            live = stack.public_json("/api/public/lives/1")
            assert live["font"] == "Georgia" and live["text_color"] == "#12AB34"
            assert stack.public_json("/api/public/lives/2")["font"] == "Roboto"
            for path in ("/inscritos.json", "/live1.json"):
                body = stack.on_port(PUBLIC_PORT, path)["body"]
                assert "font" not in body and "text_color" not in body, path

            contador = ctx.new_page()
            _guard(contador, problems)
            contador.goto(f"{PUBLIC}/contador")
            contador.locator(".digito-wrap").first.wait_for(state="attached", timeout=20000)
            wait_until(
                lambda: contador.evaluate("[getComputedStyle(document.getElementById('contador')).fontFamily, getComputedStyle(document.getElementById('contador')).color]")
                == ["Impact, sans-serif", "rgb(255, 136, 0)"],
                timeout=15, interval=0.5,
            )
            overlay = ctx.new_page()
            _guard(overlay, problems)
            overlay.goto(f"{PUBLIC}/overlay/live.html?slot=1")
            overlay.locator(".digito-wrap").first.wait_for(state="attached", timeout=20000)
            wait_until(
                lambda: overlay.evaluate("[getComputedStyle(document.getElementById('contagem')).fontFamily, getComputedStyle(document.getElementById('contagem')).color]")
                == ["Georgia, sans-serif", "rgb(18, 171, 52)"],
                timeout=15, interval=0.5,
            )

            # mudar no painel muda o overlay aberto, sem recarregar a pagina
            admin.get_by_label("Cor do texto do contador", exact=True).fill("#0000ff")
            admin.get_by_role("button", name="Salvar aparência").click()
            admin.locator("#lookOk").wait_for(state="visible", timeout=15000)
            wait_until(lambda: contador.evaluate("getComputedStyle(document.getElementById('contador')).color") == "rgb(0, 0, 255)", timeout=15, interval=0.5)
            assert contador.evaluate("performance.getEntriesByType('navigation').length") == 1
            assert dialogs == []
    finally:
        stack.api("PUT", "/api/admin/settings", {"subs_font": "Roboto", "subs_text_color": "#FFFFFF"})
        stack.api("PUT", "/api/admin/slots", {"slots": [
            {"url": u, "goal": None, "color": "#FF0033", "font": "Roboto", "text_color": "#FFFFFF"} for u in stack.default_slots()
        ]})
    assert problems == []


def test_int_c11_03_api_real_recusa_canal_fonte_e_cor_invalidos_e_a_saude_mostra_o_estado_real(stack):
    stack.ensure_lives()
    stack.ensure_subs("460600")
    before = json.loads(stack.api("GET", "/api/admin/settings")["body"])
    bad_bodies = [
        {"channel_id": "UC123"}, {"channel_id": NEW_CHANNEL + "x"}, {"subs_font": "Arial; } body{"},
        {"subs_font": "url(x)"}, {"subs_font": '"Arial"'}, {"subs_text_color": "red"}, {}, {"channel_id": None},
    ]
    for body in bad_bodies:
        r = stack.api("PUT", "/api/admin/settings", body)
        assert r["status"] == 422, (body, r["status"], r["body"][:200])
        assert '"input"' not in r["body"]
    assert json.loads(stack.api("GET", "/api/admin/settings")["body"]) == before
    slots = [{"url": "", "goal": None, "color": "#FF0033", "font": "Comic Sans MS", "text_color": "#FFFFFF"}] + [
        {"url": "", "goal": None, "color": "#FF0033"} for _ in range(3)
    ]
    assert stack.api("PUT", "/api/admin/slots", {"slots": slots})["status"] == 422

    # secao Saude contra a pilha real: cartoes e diagrama refletem o estado dos workers
    with chromium_context(ignore_https_errors=True) as ctx:
        admin = ctx.new_page()
        ui_login(admin, ADMIN, USERNAME, stack.password)
        admin.locator(".hc").first.wait_for(state="attached", timeout=20000)
        wait_until(lambda: admin.locator(".hc").count() == 5, timeout=20, interval=0.5)
        states = admin.evaluate("Object.fromEntries([...document.querySelectorAll('.hc')].map((c) => [c.dataset.component, c.dataset.state]))")
        assert states["api"] == "ok" and states["db"] == "ok" and states["lives"] == "ok" and states["inscritos"] == "ok", states
        assert admin.evaluate("document.querySelector('[data-link=\"db-api\"]').dataset.state") == "ok"
