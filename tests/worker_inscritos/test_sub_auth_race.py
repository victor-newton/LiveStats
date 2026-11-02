"""Regressao (INT-11 da T8): a pagina do Studio redireciona SOZINHA para o login depois de carregada.

O valor do contador da pagina de login (decoy) nunca pode ser gravado; o ultimo valor valido fica.
"""

from __future__ import annotations

import time

from sub_support import cookie, query

STUDIO_QUE_EXPIRA = """<!DOCTYPE html><html><body>
<yta-smooth-counter>460600</yta-smooth-counter>
<script>setTimeout(function () { location.href = '/signin/v2/identifier?service=youtube'; }, 300);</script>
</body></html>"""


def test_sub_auth_03_redirecionamento_espontaneo_nao_grava_o_contador_da_pagina_de_login(
    env, make_worker, add_cookies
):
    env.server.html_override = STUDIO_QUE_EXPIRA
    add_cookies([cookie("a", "1")])
    worker = make_worker()

    worker.step()  # a pagina ainda e o Studio: le 460600
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]

    time.sleep(1.5)  # a pagina navega sozinha para o login; nenhuma chamada do Playwright no meio
    worker.step()
    worker.step()

    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)], "decoy 999999 foi gravado"
    assert query(env.db, "SELECT state, auth_state, last_error_code FROM worker_status") == [
        ("error", "login_required", "login_required")
    ]
