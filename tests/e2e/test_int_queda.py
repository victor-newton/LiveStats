# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""INT-QUEDA-01..02: o numero de inscritos DESCE no Studio falso e a pilha real acompanha (regressao do app antigo).

No app antigo o contador ficava parado ate o numero voltar a subir e ultrapassar. Aqui o Studio falso (unica fronteira
simulada) mostra 753880, depois 753879, depois 753878, e o worker real, a api real, o Nginx real e o overlay real
precisam refletir sempre o valor MENOR.
"""

from __future__ import annotations

import pytest
from e2e_browser import chromium_context, watch_dialogs
from e2e_stack import PUBLIC_PORT, SCRAPE_INTERVAL, wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

PUBLIC = f"http://127.0.0.1:{PUBLIC_PORT}"
CICLO = 3 * SCRAPE_INTERVAL + 10  # folga para a leitura do worker + o polling de 5 s do overlay


def _overlay_digits(page) -> str:
    return page.evaluate(
        """() => [...document.querySelectorAll('#contador .digito-inner, .digito-inner')]
            .map(e => { const m = /translateY\\(-?(\\d+)(?:em)?\\)/.exec(e.style.transform); return m ? m[1] : '?'; }).join('')"""
    )


def _publico(stack, valor: str) -> bool:
    legado = stack.public_json("/inscritos.json", port=PUBLIC_PORT)
    novo = stack.public_json("/api/public/subscribers", port=PUBLIC_PORT)
    return legado["inscritos"] == valor and legado["raw"] == valor and novo["inscritos"] == valor and novo["raw"] == valor


def test_int_queda_01_estudio_mostra_numero_menor_e_json_e_overlay_acompanham(stack):
    stack.ensure_subs("753880")
    wait_until(lambda: _publico(stack, "753880"), timeout=CICLO, interval=1)
    antes = stack.public_json("/inscritos.json", port=PUBLIC_PORT)["atualizado_em"]
    try:
        with chromium_context() as ctx:
            page = ctx.new_page()
            dialogs = watch_dialogs(page)
            page.goto(f"{PUBLIC}/contador")
            wait_until(lambda: _overlay_digits(page) == "753880", timeout=20, interval=0.5)

            for menor in ("753879", "753878"):
                stack.fake_set(subscribers=menor)
                wait_until(lambda m=menor: _publico(stack, m), timeout=CICLO, interval=1)  # JSON legado e novo
                wait_until(lambda m=menor: _overlay_digits(page) == m, timeout=CICLO, interval=0.25)  # overlay aberto
                sub = stack.workers_status()["subscribers"]
                assert sub["inscritos"] == menor
                assert stack.workers_status()["workers"]["inscritos"]["state"] == "ok"

            durations = page.evaluate(
                "() => [...document.querySelectorAll('.digito-inner')].map(e => getComputedStyle(e).transitionDuration)"
            )
            assert "0.45s" in durations, durations  # a queda foi ANIMADA no overlay, sem recarregar a pagina
            assert page.evaluate("performance.getEntriesByType('navigation').length") == 1

            # e o contador continua acompanhando quando o numero sobe de novo
            stack.fake_set(subscribers="753881")
            wait_until(lambda: _publico(stack, "753881"), timeout=CICLO, interval=1)
            wait_until(lambda: _overlay_digits(page) == "753881", timeout=CICLO, interval=0.25)
            assert dialogs == []
        depois = stack.public_json("/inscritos.json", port=PUBLIC_PORT)["atualizado_em"]
        assert depois > antes
    finally:
        stack.fake_set(subscribers="460600")
        stack.ensure_subs("460600")


def test_int_queda_02_numero_de_digitos_diminui_1000_para_999(stack):
    stack.ensure_subs("1000")
    wait_until(lambda: _publico(stack, "1000"), timeout=CICLO, interval=1)
    try:
        with chromium_context() as ctx:
            page = ctx.new_page()
            page.goto(f"{PUBLIC}/contador")
            wait_until(lambda: _overlay_digits(page) == "1000", timeout=20, interval=0.5)

            stack.fake_set(subscribers="999")
            wait_until(lambda: _publico(stack, "999"), timeout=CICLO, interval=1)
            wait_until(lambda: _overlay_digits(page) == "999", timeout=CICLO, interval=0.25)
            assert page.locator(".digito-wrap").count() == 3
    finally:
        stack.fake_set(subscribers="460600")
        stack.ensure_subs("460600")
