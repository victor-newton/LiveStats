"""WEB-LIKE-15..20 (C15): a barra de progresso fica colada embaixo do número likes/meta, grossa e arredondada.

No vMix só se recorta a área do número; por isso número e barra formam UM bloco compacto
(`#bloco-likes`). Todas as medidas vêm do layout real (getBoundingClientRect/getComputedStyle).
"""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect
from web_helpers import wait_until

PATH = "/overlay/live.html"
TAMANHOS = [{"width": 1280, "height": 720}, {"width": 1920, "height": 1080}]

MEDIDAS = """() => {
  const r = (id) => document.getElementById(id).getBoundingClientRect();
  const nome = (id) => { const b = r(id); return {x: b.x, y: b.y, w: b.width, h: b.height, b: b.bottom, r: b.right}; };
  const cs = (id) => getComputedStyle(document.getElementById(id));
  const bloco = document.querySelector('#bloco-likes');
  return {
    bloco: bloco ? (({x, y, width, height, bottom, right}) => ({x, y, w: width, h: height, b: bottom, r: right}))(bloco.getBoundingClientRect()) : null,
    contagem: nome('contagem'),
    barra: nome('barra'),
    fill: nome('barra-preenchimento'),
    fonte: parseFloat(cs('contagem').fontSize),
    raioBarra: parseFloat(cs('barra').borderTopLeftRadius),
    raioFill: parseFloat(cs('barra-preenchimento').borderTopLeftRadius),
  };
}"""


def configura(api, likes=2500, goal=10000) -> None:
    api.set_stats(1, api.live_stats(1, likes=likes))
    api.goals[0] = goal
    api.colors[0] = "#00AA11"


def abre(page: Page, api, base_url, viewport, likes=2500, goal=10000) -> dict:
    page.set_viewport_size(viewport)
    configura(api, likes, goal)
    page.goto(f"{base_url}{PATH}?slot=1")
    wait_until(lambda: page.evaluate("document.getElementById('barra-preenchimento').style.width") != "", "barra preenchida")
    page.wait_for_timeout(900)  # termina a transição da largura e a animação dos dígitos
    return page.evaluate(MEDIDAS)


@pytest.mark.parametrize("viewport", TAMANHOS, ids=lambda v: f"{v['width']}x{v['height']}")
def test_web_like_15_barra_colada_embaixo_do_numero(page: Page, api, base_url, viewport):
    m = abre(page, api, base_url, viewport)
    distancia = m["barra"]["y"] - m["contagem"]["b"]
    assert 0 <= distancia <= 0.25 * m["fonte"], (distancia, m["fonte"])
    assert m["barra"]["y"] >= m["contagem"]["b"] - 0.5  # abaixo do número, sem sobrepor


@pytest.mark.parametrize("viewport", TAMANHOS, ids=lambda v: f"{v['width']}x{v['height']}")
def test_web_like_16_trilho_tem_a_mesma_largura_do_bloco_do_numero(page: Page, api, base_url, viewport):
    m = abre(page, api, base_url, viewport)
    assert abs(m["barra"]["w"] - m["contagem"]["w"]) <= 1, (m["barra"], m["contagem"])
    assert abs(m["barra"]["x"] - m["contagem"]["x"]) <= 1


@pytest.mark.parametrize("viewport", TAMANHOS, ids=lambda v: f"{v['width']}x{v['height']}")
def test_web_like_17_cantos_totalmente_arredondados_no_trilho_e_no_preenchimento(page: Page, api, base_url, viewport):
    m = abre(page, api, base_url, viewport, likes=2500, goal=10000)  # 25%: preenchimento parcial
    assert 0 < m["fill"]["w"] < m["barra"]["w"]
    assert m["raioBarra"] >= m["barra"]["h"] / 2, m
    assert m["raioFill"] >= m["fill"]["h"] / 2, m
    # "totalmente arredondado" (999px), e não só 4 px de um trilho de 8 px
    assert m["raioBarra"] >= 100 and m["raioFill"] >= 100, m
    overflow = page.evaluate("getComputedStyle(document.getElementById('barra')).overflow")
    assert overflow == "hidden"


@pytest.mark.parametrize("viewport", TAMANHOS, ids=lambda v: f"{v['width']}x{v['height']}")
def test_web_like_18_espessura_10_a_14_px_na_fonte_padrao_e_proporcional_ao_tamanho(page: Page, api, base_url, viewport):
    m = abre(page, api, base_url, viewport)
    assert m["fonte"] == 80
    assert 10 <= m["barra"]["h"] <= 14, m["barra"]
    # dobrar a fonte (CSSOM, permitido pela CSP) dobra a espessura e a folga
    page.evaluate("document.getElementById('bloco-likes').style.setProperty('font-size', '160px')")
    page.wait_for_timeout(300)
    d = page.evaluate(MEDIDAS)
    assert d["fonte"] == 160
    assert abs(d["barra"]["h"] - 2 * m["barra"]["h"]) <= 1, (d["barra"]["h"], m["barra"]["h"])
    assert d["barra"]["y"] - d["contagem"]["b"] <= 0.25 * 160
    assert abs(d["barra"]["w"] - d["contagem"]["w"]) <= 1


@pytest.mark.parametrize("viewport", TAMANHOS, ids=lambda v: f"{v['width']}x{v['height']}")
def test_web_like_19_bloco_envolve_exatamente_numero_e_barra(page: Page, api, base_url, viewport):
    m = abre(page, api, base_url, viewport)
    b = m["bloco"]
    assert b is not None
    topo = min(m["contagem"]["y"], m["barra"]["y"])
    base = max(m["contagem"]["b"], m["barra"]["b"])
    esq = min(m["contagem"]["x"], m["barra"]["x"])
    dir_ = max(m["contagem"]["r"], m["barra"]["r"])
    assert abs(b["y"] - topo) <= 2 and abs(b["b"] - base) <= 2, (b, m["contagem"], m["barra"])
    assert abs(b["x"] - esq) <= 2 and abs(b["r"] - dir_) <= 2
    # e o bloco continua centralizado na página transparente
    assert abs((b["x"] + b["r"]) / 2 - viewport["width"] / 2) <= 2
    assert abs((b["y"] + b["b"]) / 2 - viewport["height"] / 2) <= 2


@pytest.mark.parametrize("viewport", TAMANHOS, ids=lambda v: f"{v['width']}x{v['height']}")
def test_web_like_20_sem_meta_nao_ha_barra_e_o_bloco_e_so_o_numero(page: Page, api, base_url, viewport):
    page.set_viewport_size(viewport)
    configura(api, likes=1234, goal=None)
    page.goto(f"{base_url}{PATH}?slot=1")
    expect(page.locator("#overlay-likes")).to_be_visible()
    page.wait_for_timeout(900)
    expect(page.locator("#barra")).to_be_hidden()
    assert page.locator("#barra").bounding_box() is None
    assert page.locator("#barra-preenchimento").bounding_box() is None
    m = page.evaluate(MEDIDAS.replace("barra: nome('barra'),", "barra: null,").replace("fill: nome('barra-preenchimento'),", "fill: null,").replace("raioBarra: parseFloat(cs('barra').borderTopLeftRadius),", "raioBarra: 0,").replace("raioFill: parseFloat(cs('barra-preenchimento').borderTopLeftRadius),", "raioFill: 0,"))
    b, c = m["bloco"], m["contagem"]
    assert b is not None
    assert abs(b["h"] - c["h"]) <= 2 and abs(b["w"] - c["w"]) <= 2, (b, c)
