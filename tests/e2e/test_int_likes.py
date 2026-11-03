# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""INT-C10-01..03 (D34): meta e cor salvas no painel (8081) chegam ao overlay de likes (8080) pela pilha real."""

from __future__ import annotations

import json

import pytest
from e2e_browser import chromium_context, ui_login, watch_dialogs
from e2e_stack import ADMIN_PORT, LIVE_INTERVAL, PUBLIC_PORT, SLOT_URL, USERNAME, VID_LIVE, wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

OVERLAY = f"http://127.0.0.1:{PUBLIC_PORT}/overlay/live.html?slot=1"
ADMIN = f"https://127.0.0.1:{ADMIN_PORT}"
# O worker consulta o YouTube a cada LIVE_INTERVAL s e o overlay a api a cada 5 s.
CHANGE_TIMEOUT = LIVE_INTERVAL + 5 + 10


def _label(page) -> str | None:
    return page.evaluate("document.getElementById('contagem') && document.getElementById('contagem').getAttribute('aria-label')")


def _width(page) -> str:
    return page.evaluate("document.getElementById('barra-preenchimento').style.width")


def _save(admin, n: int = 1) -> None:
    """D36: cada cartao de live tem o seu Salvar (na aba Lives)."""
    admin.get_by_role("button", name=f"Salvar slot {n}", exact=True).click()
    admin.locator(f"#saveMsg{n}").wait_for(state="visible", timeout=15000)


def _guard(page, sink: list[str]) -> None:
    """Junta violacoes de CSP e requisicoes a outras origens (nao pode haver nenhuma)."""
    page.on("console", lambda m: sink.append(f"console:{m.text}") if "Content Security Policy" in m.text or "Refused to" in m.text else None)
    page.on("request", lambda r: sink.append(f"externo:{r.url}") if not r.url.startswith(("http://127.0.0.1", "https://127.0.0.1", "data:")) else None)


def test_int_c10_01_meta_e_cor_do_painel_chegam_ao_overlay_e_o_overlay_anima(stack):
    stack.ensure_lives()
    stack.fake_set_video(VID_LIVE, likes=1200)
    wait_until(lambda: stack.public_json("/live1.json")[0]["likes"] == 1200, timeout=CHANGE_TIMEOUT, interval=1)
    problems: list[str] = []
    try:
        with chromium_context(ignore_https_errors=True) as ctx:
            admin = ctx.new_page()
            dialogs = watch_dialogs(admin)
            _guard(admin, problems)
            ui_login(admin, ADMIN, USERNAME, stack.password)
            admin.locator("#tab-lives").click()
            assert admin.get_by_label("Meta de likes do slot 1").input_value() == ""  # padrao: sem meta
            admin.get_by_label("Meta de likes do slot 1").fill("10000")
            admin.get_by_label("Cor da barra do slot 1").fill("#12ab34")
            _save(admin)

            # a api real guardou (cor em maiusculas) e o formato novo ganhou goal/color; o legado nao
            pub = wait_until(lambda: (lambda j: j if j.get("goal") == 10000 else None)(stack.public_json("/api/public/lives/1")), timeout=15, interval=0.5)
            assert pub["color"] == "#12AB34" and pub["likes"] == 1200
            assert stack.public_json("/api/public/lives/3")["goal"] is None and stack.public_json("/api/public/lives/3")["color"] == "#FF0033"
            legacy = stack.on_port(PUBLIC_PORT, "/live1.json")
            assert b"goal" not in legacy["body"].encode() and b"color" not in legacy["body"].encode()

            overlay = ctx.new_page()
            _guard(overlay, problems)
            overlay.goto(OVERLAY)
            wait_until(lambda: _label(overlay) == "1.200/10.000", timeout=20, interval=0.5)
            wait_until(lambda: _width(overlay) == "12%", timeout=5, interval=0.25)
            assert overlay.evaluate("getComputedStyle(document.getElementById('barra-preenchimento')).backgroundColor") == "rgb(18, 171, 52)"
            assert overlay.evaluate("getComputedStyle(document.body).backgroundColor") == "rgba(0, 0, 0, 0)"

            # os likes mudam no YouTube (falso): o worker le, a api serve e o overlay ANIMA (sem recarregar a pagina)
            stack.fake_set_video(VID_LIVE, likes=1234)
            wait_until(lambda: _label(overlay) == "1.234/10.000", timeout=CHANGE_TIMEOUT, interval=0.25)
            durations = overlay.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => getComputedStyle(e).transitionDuration)")
            assert "0.45s" in durations, durations
            assert overlay.evaluate("performance.getEntriesByType('navigation').length") == 1
            wait_until(lambda: _width(overlay) == "12.34%", timeout=5, interval=0.25)

            # passou da meta: barra cheia, numero exato
            stack.fake_set_video(VID_LIVE, likes=12000)
            wait_until(lambda: _label(overlay) == "12.000/10.000", timeout=CHANGE_TIMEOUT, interval=0.25)
            wait_until(lambda: _width(overlay) == "100%", timeout=5, interval=0.25)

            # meta apagada no painel: some a barra e ficam so os likes
            admin.get_by_label("Meta de likes do slot 1").fill("")
            _save(admin)
            wait_until(lambda: _label(overlay) == "12.000", timeout=15, interval=0.25)
            assert overlay.evaluate("document.getElementById('barra').hidden") is True
            assert dialogs == []
    finally:
        stack.fake_set_video(VID_LIVE, likes=640)
        stack.ensure_lives()
    assert problems == []


def test_int_c10_02_slot_invalido_nao_exibe_nada_na_pilha_real(stack):
    stack.ensure_lives()
    problems: list[str] = []
    with chromium_context() as ctx:
        for query in ("", "?slot=0", "?slot=5", "?slot=abc", "?slot=1&slot=2"):
            page = ctx.new_page()
            _guard(page, problems)
            page.goto(f"http://127.0.0.1:{PUBLIC_PORT}/overlay/live.html{query}")
            page.wait_for_timeout(1500)
            assert page.evaluate("document.getElementById('overlay-likes').hidden") is True, query
            assert page.locator(".digito-wrap").count() == 0, query
            page.close()
    assert problems == []


def test_int_c10_03_a_api_real_recusa_cor_e_meta_invalidas_e_o_formato_antigo(stack):
    stack.ensure_lives()
    before = stack.api("GET", "/api/admin/slots")
    assert before["status"] == 200
    base = [{"url": SLOT_URL.format(VID_LIVE), "goal": None, "color": "#FF0033"}] + [{"url": "", "goal": None, "color": "#FF0033"}] * 3

    def attempt(first: dict | str | None, whole=None):
        slots = whole if whole is not None else [first, *base[1:]]
        return stack.api("PUT", "/api/admin/slots", {"slots": slots})

    for bad in ({**base[0], "color": "red"}, {**base[0], "color": "#FFF"}, {**base[0], "color": "url(x)"},
                {**base[0], "color": "#FF0033;x"}, {**base[0], "goal": 0}, {**base[0], "goal": "10"}, {**base[0], "goal": 1_000_000_001}):
        resp = attempt(bad)
        assert resp["status"] == 422, (bad, resp["body"])
        assert '"input"' not in resp["body"]
    old_format = attempt(None, whole=[SLOT_URL.format(VID_LIVE), "", "", ""])
    assert old_format["status"] == 422 and '"input"' not in old_format["body"]
    # nada mudou
    after = stack.api("GET", "/api/admin/slots")
    config = lambda r: [{k: item[k] for k in ("slot", "url", "goal", "color")} for item in json.loads(r["body"])["slots"]]  # noqa: E731
    assert config(after) == config(before)  # as estatisticas mudam sozinhas; a configuracao nao
    assert json.loads(after["body"])["saved_at"] == json.loads(before["body"])["saved_at"]
