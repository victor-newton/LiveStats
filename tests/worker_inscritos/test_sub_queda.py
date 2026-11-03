"""SUB-QUEDA-01..03: o numero de inscritos DESCE e o worker grava o valor menor.

No app antigo o contador ficava parado quando os inscritos caiam. Aqui o Chromium real abre uma pagina que
imita o `yta-smooth-counter` e, entre dois ciclos do worker, o proprio script da pagina troca o numero NO
LUGAR (sem recarregar), como o componente do Studio faz: o digito novo entra na coluna ao lado do antigo
(variante A: sai o `.last`; variante B e animada: o novo vira o ultimo `div` da pilha; variante C: texto).
O worker, o scraper e o SQLite sao os reais; so o YouTube e simulado.
"""

from __future__ import annotations

import logging

import pytest

from sub_support import cookie, query

# Script da pagina. `window.definir(texto)` troca o numero alinhando as colunas pela direita:
# coluna que muda de digito ganha o digito novo (o antigo continua na coluna); coluna a mais some pela esquerda;
# coluna que falta nasce na esquerda. O separador ("." ) e uma coluna de um caractere so.
SCRIPT = r"""
const VARIANTE = "__VARIANTE__";
const contador = document.querySelector('yta-smooth-counter');
const colunas = [];

function novaColuna(c, anterior) {
  const raiz = document.createElement('div');
  if (VARIANTE === 'animada') { raiz.style.cssText = 'height:1em;overflow:hidden'; }
  if (VARIANTE === 'a') {
    if (anterior !== null) { const v = document.createElement('div'); v.className = 'counter-value'; v.textContent = anterior; raiz.appendChild(v); }
    const u = document.createElement('div'); u.className = 'counter-value last'; u.textContent = c; raiz.appendChild(u);
  } else {
    const seq = document.createElement('div'); seq.className = 'counter-sequence-inner';
    if (VARIANTE === 'animada') { seq.style.cssText = 'animation:rolar 0.2s forwards'; }
    if (anterior !== null) { const v = document.createElement('div'); v.textContent = anterior; seq.appendChild(v); }
    const u = document.createElement('div'); u.textContent = c; seq.appendChild(u);
    raiz.appendChild(seq);
  }
  return raiz;
}

function atual(raiz) {
  return VARIANTE === 'a' ? raiz.querySelector('.counter-value.last').textContent
                          : [...raiz.querySelectorAll('.counter-sequence-inner > div')].pop().textContent;
}

function trocar(raiz, c) {
  if (VARIANTE === 'a') {
    raiz.querySelector('.counter-value.last').className = 'counter-value';
    const u = document.createElement('div'); u.className = 'counter-value last'; u.textContent = c; raiz.appendChild(u);
  } else {
    const seq = raiz.querySelector('.counter-sequence-inner');
    const u = document.createElement('div'); u.textContent = c; seq.appendChild(u);
    if (VARIANTE === 'animada') { seq.style.animation = 'none'; void seq.offsetWidth; seq.style.animation = 'rolar 0.2s forwards'; }
  }
}

window.definir = function (texto) {
  if (VARIANTE === 'c') { contador.textContent = texto; return; }
  const chars = [...texto];
  while (colunas.length > chars.length) { colunas.shift().remove(); }
  while (colunas.length < chars.length) {
    const c = chars[chars.length - colunas.length - 1];
    const raiz = novaColuna(c, /\d/.test(c) ? String((Number(c) + 1) % 10) : null);
    contador.prepend(raiz);
    colunas.unshift(raiz);
  }
  chars.forEach((c, i) => { if (atual(colunas[i]) !== c) { trocar(colunas[i], c); } });
};
window.definir("__INICIAL__");
"""

ESTILO = "@keyframes rolar { from { transform: translateY(0); } to { transform: translateY(-3em); } }"


def pagina(variante: str, inicial: str) -> str:
    script = SCRIPT.replace("__VARIANTE__", variante).replace("__INICIAL__", inicial)
    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'><title>queda</title>"
        f"<style>{ESTILO}</style></head><body><yta-smooth-counter style='display:flex'></yta-smooth-counter>"
        f"<script>{script}</script></body></html>"
    )


VARIANTES = ["a", "b", "c", "animada"]


def abrir(env, make_worker, add_cookies, variante: str, inicial: str):
    env.server.html_override = pagina(variante, inicial)
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    return worker


def gravado(env):
    return query(env.db, "SELECT id, value, raw FROM subscriber_reading")


def mudancas(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if "[MUDANCA]" in r.getMessage()]


# ---------------------------------------------------------------------- SUB-QUEDA-01
@pytest.mark.parametrize("variante", VARIANTES)
def test_sub_queda_01_753880_desce_para_753879_e_753878_e_grava_o_menor(env, make_worker, add_cookies, caplog, variante):
    with caplog.at_level(logging.INFO, logger="worker_inscritos"):
        worker = abrir(env, make_worker, add_cookies, variante, "753.880")
        assert gravado(env) == [(1, "753.880", "753.880")]

        page = worker.session.page
        for novo in ("753.879", "753.878"):
            page.evaluate("(t) => window.definir(t)", novo)
            worker.step()
            assert gravado(env) == [(1, novo, novo)], f"a leitura mais recente tem de ser o valor MENOR {novo}"
            assert query(env.db, "SELECT state, consecutive_failures FROM worker_status") == [("ok", 0)]

    assert mudancas(caplog) == [
        "[MUDANCA] None -> 753.880",
        "[MUDANCA] 753.880 -> 753.879",
        "[MUDANCA] 753.879 -> 753.878",
    ]


@pytest.mark.parametrize("variante", VARIANTES)
def test_sub_queda_01b_depois_de_descer_o_worker_acompanha_quando_sobe_de_novo(env, make_worker, add_cookies, caplog, variante):
    with caplog.at_level(logging.INFO, logger="worker_inscritos"):
        worker = abrir(env, make_worker, add_cookies, variante, "753.880")
        page = worker.session.page
        for novo, esperado in [("753.879", "753.879"), ("753.878", "753.878"), ("753.879", "753.879"), ("753.881", "753.881")]:
            page.evaluate("(t) => window.definir(t)", novo)
            worker.step()
            assert gravado(env) == [(1, esperado, esperado)]
    assert mudancas(caplog) == [
        "[MUDANCA] None -> 753.880",
        "[MUDANCA] 753.880 -> 753.879",
        "[MUDANCA] 753.879 -> 753.878",
        "[MUDANCA] 753.878 -> 753.879",
        "[MUDANCA] 753.879 -> 753.881",
    ]


# ---------------------------------------------------------------------- SUB-QUEDA-02
@pytest.mark.parametrize("variante", VARIANTES)
def test_sub_queda_02_queda_que_muda_varias_colunas_e_o_numero_de_digitos(env, make_worker, add_cookies, caplog, variante):
    with caplog.at_level(logging.INFO, logger="worker_inscritos"):
        worker = abrir(env, make_worker, add_cookies, variante, "760.000")
        page = worker.session.page
        for novo in ("759.999", "1.000", "999"):
            page.evaluate("(t) => window.definir(t)", novo)
            worker.step()
            assert gravado(env) == [(1, novo, novo)]
    assert mudancas(caplog) == [
        "[MUDANCA] None -> 760.000",
        "[MUDANCA] 760.000 -> 759.999",
        "[MUDANCA] 759.999 -> 1.000",
        "[MUDANCA] 1.000 -> 999",
    ]


# ---------------------------------------------------------------------- SUB-QUEDA-03
@pytest.mark.parametrize("variante", ["b", "animada"])
def test_sub_queda_03_a_pilha_tem_o_digito_antigo_e_o_novo_e_vale_o_ultimo(env, make_worker, add_cookies, variante):
    """Prova de que a fixture reproduz a queda: a coluna guarda os DOIS digitos e o worker le o ultimo."""
    worker = abrir(env, make_worker, add_cookies, variante, "753.880")
    page = worker.session.page
    page.evaluate("window.definir('753.879')")
    pilha = page.evaluate(
        "[...document.querySelectorAll('.counter-sequence-inner')].pop().querySelectorAll('div').length "
        "? [...[...document.querySelectorAll('.counter-sequence-inner')].pop().querySelectorAll('div')].map((d) => d.textContent) : []"
    )
    assert pilha == ["1", "0", "9"]  # semente, digito antigo (0) e digito novo (9): o ultimo e o atual
    worker.step()
    assert gravado(env) == [(1, "753.879", "753.879")]
