"""C9: o Studio abre, mas a conta dos cookies nao tem acesso ao canal (YOUTUBE_CHANNEL_ID errado).

A URL segue em studio.youtube.com (nao e login); a pagina mostra "Ops, voce nao tem permissao...".
O worker deve dizer isso (config_error) em vez de "contador nao encontrado".
"""

from __future__ import annotations

import logging

import pytest
from sub_support import cookie, query

MSG = "Sem permissao para este canal no YouTube Studio: confira o YOUTUBE_CHANNEL_ID e se a conta dos cookies tem acesso"

FRASES_COMPLETAS = [
    "Ops, você não tem permissão para acessar esta página",
    "Oops, you don't have permission to access this page",
]


def _pagina_com_contador_e_frase(frase: str) -> str:
    return (
        "<!DOCTYPE html><html><body>"
        "<yta-smooth-counter>460600</yta-smooth-counter>"
        f"<p>{frase}</p>"
        "</body></html>"
    )


@pytest.mark.parametrize("pagina", ["sem_permissao_ptbr.html", "sem_permissao_en.html"])
def test_sub_auth_04_pagina_sem_permissao_vira_config_error(env, make_worker, add_cookies, caplog, pagina):
    env.server.page_name = "variante_a.html"
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    valido = query(env.db, "SELECT value FROM subscriber_reading")
    assert len(valido) == 1

    env.server.page_name = pagina
    add_cookies([cookie("a", "2")])  # conjunto novo: recarrega o Studio, agora na pagina de erro
    with caplog.at_level(logging.DEBUG):
        worker.step()

    assert query(
        env.db, "SELECT state, auth_state, last_error_code, last_error_message FROM worker_status"
    ) == [("error", "ok", "config_error", MSG)]
    assert query(env.db, "SELECT value FROM subscriber_reading") == valido, "ultimo valor valido deve ficar"
    assert any("Sem permissao para este canal" in r.getMessage() and r.levelno == logging.WARNING for r in caplog.records)
    texto = "\n".join(r.getMessage() for r in caplog.records)
    assert "Ops" not in texto and "Mudar de conta" not in texto and "Switch account" not in texto
    assert "Contador nao encontrado" not in texto
    assert worker.falhas == 1 and worker.consecutive_failures == 1


def test_sub_auth_04_sem_permissao_conta_para_o_reload_de_30(env, make_worker, add_cookies):
    env.server.page_name = "sem_permissao_ptbr.html"
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    antes = env.server.studio_hits()
    for _ in range(29):
        worker.step()
    assert env.server.studio_hits() > antes, "apos 30 falhas a pagina deve ser recarregada"
    assert query(env.db, "SELECT value FROM subscriber_reading") == []


@pytest.mark.parametrize("frase", FRASES_COMPLETAS)
def test_sub_auth_05_contador_presente_com_frase_de_erro_nao_e_sem_permissao(env, make_worker, add_cookies, frase):
    env.server.html_override = _pagina_com_contador_e_frase(frase)
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()

    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]
    assert query(env.db, "SELECT state, auth_state, last_error_code FROM worker_status") == [("ok", "ok", None)]
