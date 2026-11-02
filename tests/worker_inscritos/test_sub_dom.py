"""SUB-DOM-01..06: as tres estrategias de leitura do `yta-smooth-counter`, em Chromium real."""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest

from worker_inscritos.browser import ESTRATEGIAS, JS_ESTRATEGIA_1, JS_ESTRATEGIA_2, JS_ESTRATEGIA_3
from sub_support import FIXTURES, REPO_ROOT, cookie, query

PAGINAS_DE_CONTADOR = [
    "variante_a.html",
    "variante_b.html",
    "variante_c.html",
    "dois_contadores.html",
    "animada.html",
    "sem_contador.html",
    "valor_curto.html",
    "login.html",
]


def carregar(env, make_worker, add_cookies, pagina):
    env.server.page_name = pagina
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    return worker


def leitura(env):
    return query(env.db, "SELECT id, value, raw FROM subscriber_reading")


def test_sub_dom_01_fixture_a_estrategia_1_counter_value_last(env, make_worker, add_cookies):
    worker = carregar(env, make_worker, add_cookies, "variante_a.html")
    assert leitura(env) == [(1, "460600", "460600")]
    page = worker.session.page
    assert page.evaluate(JS_ESTRATEGIA_1) == "460600"
    assert page.evaluate(JS_ESTRATEGIA_2) is None  # a fixture A nao tem .counter-sequence-inner
    assert page.evaluate(JS_ESTRATEGIA_3) is None  # os digitos ficam em divs separados: nada com 3 digitos seguidos


def test_sub_dom_02_fixture_b_estrategia_2_ultimo_div_de_cada_sequencia(env, make_worker, add_cookies):
    worker = carregar(env, make_worker, add_cookies, "variante_b.html")
    assert leitura(env) == [(1, "460600", "460600")]
    page = worker.session.page
    assert page.evaluate(JS_ESTRATEGIA_1) is None  # sem .counter-value.last
    assert page.evaluate(JS_ESTRATEGIA_2) == "460600"
    assert page.evaluate(JS_ESTRATEGIA_3) is None


def test_sub_dom_03_fixture_c_estrategia_3_texto_do_contador(env, make_worker, add_cookies):
    worker = carregar(env, make_worker, add_cookies, "variante_c.html")
    assert leitura(env) == [(1, "460600", "460600")]
    page = worker.session.page
    assert page.evaluate(JS_ESTRATEGIA_1) is None
    assert page.evaluate(JS_ESTRATEGIA_2) is None
    assert page.evaluate(JS_ESTRATEGIA_3) == "460600"


def test_sub_dom_04_dois_contadores_o_primeiro_sem_3_digitos(env, make_worker, add_cookies):
    carregar(env, make_worker, add_cookies, "dois_contadores.html")
    assert leitura(env) == [(1, "460600", "460600")]


def test_sub_dom_04_sem_nenhum_contador_valido_e_contador_nao_encontrado(env, make_worker, add_cookies):
    worker = carregar(env, make_worker, add_cookies, "sem_contador.html")
    assert worker.session.read_counter() is None
    assert leitura(env) == []  # o "460600" solto fora do componente nao pode ser lido
    assert query(env.db, "SELECT state, last_error_code FROM worker_status") == [("degraded", "counter_not_found")]
    assert worker.falhas == 1


def test_sub_dom_05_fixture_animada_le_o_digito_atual_de_cada_posicao(env, make_worker, add_cookies):
    worker = carregar(env, make_worker, add_cookies, "animada.html")
    assert leitura(env) == [(1, "460600", "460600")]
    # prova de que a fixture e uma pilha: o innerText do container junta todos os divs empilhados
    container = worker.session.page.evaluate("document.querySelector('yta-smooth-counter').innerText")
    assert len(re.sub(r"\D", "", container)) > 6


# ---------------------------------------------------------------------- SUB-DOM-06: fidelidade da fixture
class _Estrutura(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: set[str] = set()
        self.classes: set[str] = set()
        self.divs_dentro_de_sequencia = 0
        self.texto_direto_no_contador = ""
        self._pilha: list[tuple[str, list[str]]] = []

    def handle_starttag(self, tag, attrs):
        classes = (dict(attrs).get("class") or "").split()
        self.tags.add(tag)
        self.classes.update(classes)
        if tag == "div" and any(("counter-sequence-inner" in c) for _, c in self._pilha):
            self.divs_dentro_de_sequencia += 1
        self._pilha.append((tag, classes))

    def handle_endtag(self, tag):
        while self._pilha:
            if self._pilha.pop()[0] == tag:
                break

    def handle_data(self, data):
        if self._pilha and self._pilha[-1][0] == "yta-smooth-counter" and data.strip():
            self.texto_direto_no_contador += data.strip()


def estrutura(nome: str) -> _Estrutura:
    parser = _Estrutura()
    parser.feed((FIXTURES / nome).read_text(encoding="utf-8"))
    return parser


def seletores_do_legado() -> set[str]:
    """Seletores listados em docs/legacy-formats.md 2.2 (o elemento e as 3 estrategias numeradas)."""
    texto = (REPO_ROOT / "docs" / "legacy-formats.md").read_text(encoding="utf-8")
    secao = texto.split("### 2.2", 1)[1].split("\n## ", 1)[0]
    tokens = set(re.findall(r"sobre os elementos `([^`]+)`", secao))
    for linha in secao.splitlines():
        if re.match(r"^\d\. ", linha):
            tokens.update(re.findall(r"`([^`]+)`", linha))
    padrao = re.compile(r"^\.?[a-z]+(?:-[a-z]+)*(?:\.[a-z]+(?:-[a-z]+)*)*$")
    return {t for t in tokens if padrao.match(t)}


def test_sub_dom_06_seletores_do_legado_sao_os_esperados():
    assert seletores_do_legado() == {"yta-smooth-counter", ".counter-value.last", ".counter-sequence-inner", "div"}


def test_sub_dom_06_o_scraper_le_exatamente_os_seletores_do_legado():
    usados = set(re.findall(r"querySelectorAll\('([^']+)'\)", "\n".join(ESTRATEGIAS)))
    assert usados == seletores_do_legado()


def test_sub_dom_06_fixtures_usam_so_seletores_que_o_scraper_le():
    permitidos = seletores_do_legado()
    classes_permitidas = {c for s in permitidos if s.startswith(".") for c in s.split(".") if c}
    tags_permitidas = {s for s in permitidos if not s.startswith(".")}
    tags_estruturais = {"html", "head", "meta", "title", "style", "body"}
    for nome in PAGINAS_DE_CONTADOR:
        e = estrutura(nome)
        assert e.classes <= classes_permitidas, (nome, e.classes - classes_permitidas)
        assert e.tags <= tags_permitidas | tags_estruturais, (nome, e.tags - tags_permitidas - tags_estruturais)
        assert "yta-smooth-counter" in e.tags, nome


def test_sub_dom_06_fixture_a_tem_counter_value_last():
    e = estrutura("variante_a.html")
    assert {"counter-value", "last"} <= e.classes
    assert "counter-sequence-inner" not in e.classes


def test_sub_dom_06_fixture_b_tem_counter_sequence_inner_com_divs_e_sem_last():
    e = estrutura("variante_b.html")
    assert "counter-sequence-inner" in e.classes
    assert e.divs_dentro_de_sequencia > 6  # pilhas de divs (mais de 1 por posicao)
    assert "last" not in e.classes and "counter-value" not in e.classes


def test_sub_dom_06_fixture_c_so_tem_texto_direto_no_contador():
    e = estrutura("variante_c.html")
    assert e.classes == set()
    assert "div" not in e.tags
    assert re.search(r"\d{3,}", e.texto_direto_no_contador)


@pytest.mark.parametrize("nome", ["animada.html", "dois_contadores.html"])
def test_sub_dom_06_fixtures_derivadas_usam_a_mesma_estrutura(nome):
    e = estrutura(nome)
    assert "yta-smooth-counter" in e.tags
    assert e.classes <= {"counter-value", "last", "counter-sequence-inner"}
