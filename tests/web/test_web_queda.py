"""WEB-QUEDA-01..03: o numero DESCE e os overlays acompanham (regressao do relato do app antigo).

No app antigo, quando os inscritos caiam o contador ficava parado ate o numero voltar a subir e ultrapassar.
Aqui o duble da api (validado pelo openapi.yaml) serve valores MENORES e o carrossel real, no Chromium real,
precisa animar para eles.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect
from web_helpers import freeze_clock, wait_until

CURVA = "cubic-bezier(0.25, 0.46, 0.45, 0.94)"
LIVE = "/overlay/live.html"


def leitura(valor: str) -> dict[str, str]:
    return {"inscritos": valor, "raw": valor, "atualizado_em": "2026-11-02T14:32:10.654321Z"}


def transforms(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => e.style.transform)")


def transitions(page: Page) -> list[str]:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].map((e) => e.style.transition)")


def esperado(texto: str) -> list[str]:
    # O Chromium serializa "-0em" como "0em"; os demais ficam como "-<d>em".
    return [f"translateY({'-' if d != '0' else ''}{d}em)" for d in texto if d.isdigit()]


def marcar_elementos(page: Page) -> None:
    page.evaluate("window.__inners = [...document.querySelectorAll('.digito-inner')]")


def mesmos_elementos(page: Page) -> bool:
    return page.evaluate("[...document.querySelectorAll('.digito-inner')].every((e, i) => e === window.__inners[i])")


def separadores(page: Page, seletor: str) -> list[str]:
    return page.evaluate(f"[...document.querySelectorAll('{seletor} .separador')].map((e) => e.textContent)")


def abrir_contador(page: Page, api, base_url: str, valor: str, caminho: str = "/contador") -> None:
    api.subscribers = leitura(valor)
    freeze_clock(page)
    page.goto(f"{base_url}{caminho}")
    expect(page.locator(".digito-wrap")).to_have_count(len([c for c in valor if c.isdigit()]))
    page.clock.run_for(1000)
    wait_until(lambda: transforms(page) == esperado(valor), f"animacao inicial de {valor}")


# ---------------------------------------------------------------------------
# WEB-QUEDA-01 contador de inscritos: 753.880 -> 753.879 -> 753.878, depois sobe de novo
# ---------------------------------------------------------------------------

def test_web_queda_01_inscritos_753880_desce_para_753879_e_753878_e_volta_a_subir(page: Page, api, base_url):
    abrir_contador(page, api, base_url, "753.880")
    marcar_elementos(page)
    assert transforms(page) == esperado("753880")

    for novo in ("753.879", "753.878"):
        antes = transforms(page)
        api.subscribers = leitura(novo)
        page.clock.run_for(5000)
        wait_until(lambda n=novo: transforms(page) == esperado(n), f"carrossel foi para {novo}")
        depois = transforms(page)
        assert depois[:4] == antes[:4], "os quatro primeiros digitos (753.8 ou 753.7) nao mudam"
        assert depois[5] != antes[5], "o ultimo digito se moveu"
        assert transitions(page)[5] == f"transform 0.45s {CURVA}"
        assert mesmos_elementos(page), "mesma estrutura: nada foi recriado"
        assert separadores(page, "#contador") == ["."]

    assert transforms(page)[5] == "translateY(-8em)"  # o valor MENOR (753.878) esta na tela

    # e continua acompanhando quando sobe de novo
    api.subscribers = leitura("753.881")
    page.clock.run_for(5000)
    wait_until(lambda: transforms(page) == esperado("753881"), "subiu para 753.881")


def test_web_queda_01b_queda_que_muda_varios_digitos_760000_para_759999(page: Page, api, base_url):
    abrir_contador(page, api, base_url, "760.000")
    marcar_elementos(page)
    api.subscribers = leitura("759.999")
    page.clock.run_for(5000)
    wait_until(lambda: transforms(page) == esperado("759999"), "carrossel foi para 759.999")
    assert transforms(page)[:2] == ["translateY(-7em)", "translateY(-5em)"]
    assert mesmos_elementos(page)


def test_web_queda_01c_caminho_novo_do_overlay_tambem_mostra_o_valor_menor(page: Page, api, base_url):
    abrir_contador(page, api, base_url, "753.880", "/overlay/inscritos")
    api.subscribers = leitura("753.879")
    page.clock.run_for(5000)
    wait_until(lambda: transforms(page) == esperado("753879"), "/overlay/inscritos: 753.879")


# ---------------------------------------------------------------------------
# WEB-QUEDA-02 contador: cair de 1.000 para 999 muda o numero de digitos para baixo
# ---------------------------------------------------------------------------

def test_web_queda_02_inscritos_1000_para_999_reconstroi_com_menos_digitos(page: Page, api, base_url):
    abrir_contador(page, api, base_url, "1.000")
    expect(page.locator(".digito-wrap")).to_have_count(4)
    assert separadores(page, "#contador") == ["."]

    api.subscribers = leitura("999")
    page.clock.run_for(5000)
    expect(page.locator(".digito-wrap")).to_have_count(3)
    expect(page.locator(".separador")).to_have_count(0)
    # recem-construido: parado no valor final; os atrasos sao 0, 40 e 80 ms
    assert transforms(page) == esperado("999")
    assert transitions(page)[1:] == ["none"] * 2
    anim = f"transform 0.5s {CURVA}"
    page.clock.run_for(1)
    assert transitions(page) == [anim, "none", "none"]
    page.clock.run_for(39)
    assert transitions(page) == [anim, anim, "none"]
    page.clock.run_for(40)
    assert transitions(page) == [anim] * 3
    assert transforms(page) == ["translateY(-9em)"] * 3

    # e volta a subir: 999 -> 1.000 reconstroi com 4 digitos
    api.subscribers = leitura("1.000")
    page.clock.run_for(5000)
    expect(page.locator(".digito-wrap")).to_have_count(4)
    wait_until(lambda: transforms(page) == esperado("1000"), "voltou a 1.000")


# ---------------------------------------------------------------------------
# WEB-QUEDA-03 overlay de likes: 1.000 -> 999 com meta, a barra diminui
# ---------------------------------------------------------------------------

def configura(api, likes: int, goal=10000) -> None:
    api.set_stats(1, api.live_stats(1, likes=likes))
    api.goals[0] = goal
    api.colors[0] = "#00AA11"


def texto_likes(page: Page) -> str:
    return page.evaluate("document.getElementById('contagem').getAttribute('aria-label')")


def largura(page: Page) -> str:
    return page.evaluate("document.getElementById('barra-preenchimento').style.width")


def abrir_likes(page: Page, api, base_url: str, likes: int, goal=10000) -> None:
    configura(api, likes, goal)
    freeze_clock(page)
    page.goto(f"{base_url}{LIVE}?slot=1")
    expect(page.locator(".digito-wrap").first).to_be_attached()  # a 1a resposta ja chegou
    page.clock.run_for(1000)


def test_web_queda_03_likes_1000_para_999_com_meta_muda_digitos_e_diminui_a_barra(page: Page, api, base_url):
    abrir_likes(page, api, base_url, 1000, 10000)
    wait_until(lambda: transforms(page) == esperado("1.000/10.000"), "1.000/10.000")
    assert largura(page) == "10%"

    api.set_stats(1, api.live_stats(1, likes=999))
    page.clock.run_for(5000)
    wait_until(lambda: texto_likes(page) == "999/10.000", "texto 999/10.000")
    expect(page.locator(".digito-wrap")).to_have_count(8)  # um digito e um separador a menos
    assert separadores(page, "#contagem") == ["/", "."]
    page.clock.run_for(1000)
    assert transforms(page) == esperado("999/10.000")
    assert largura(page) == "9.99%"  # menor que os 10% de antes

    page.wait_for_timeout(700)  # termina a transicao CSS da largura
    trilho = page.locator("#barra").bounding_box()
    preench = page.locator("#barra-preenchimento").bounding_box()
    assert trilho and preench and abs(preench["width"] - trilho["width"] * 0.0999) < 1.5

    # e continua acompanhando quando sobe de novo
    api.set_stats(1, api.live_stats(1, likes=1001))
    page.clock.run_for(5000)
    wait_until(lambda: texto_likes(page) == "1.001/10.000", "subiu para 1.001")
    assert largura(page) == "10.01%"


def test_web_queda_03b_likes_2500_para_2400_mesma_estrutura_so_troca_transform_e_encolhe_a_barra(page: Page, api, base_url):
    abrir_likes(page, api, base_url, 2500, 10000)
    wait_until(lambda: transforms(page) == esperado("2.500/10.000"), "2.500/10.000")
    assert largura(page) == "25%"
    marcar_elementos(page)
    antes = transforms(page)

    api.set_stats(1, api.live_stats(1, likes=2400))
    page.clock.run_for(5000)
    wait_until(lambda: texto_likes(page) == "2.400/10.000", "texto 2.400/10.000")
    depois = transforms(page)
    assert depois == esperado("2.400/10.000")
    assert depois[1] != antes[1] and depois[:1] == antes[:1] and depois[2:] == antes[2:]
    assert transitions(page)[1] == f"transform 0.45s {CURVA}"
    assert mesmos_elementos(page)
    assert largura(page) == "24%"
    page.wait_for_timeout(700)
    trilho = page.locator("#barra").bounding_box()
    preench = page.locator("#barra-preenchimento").bounding_box()
    assert trilho and preench and abs(preench["width"] - trilho["width"] * 0.24) < 1.5


def test_web_queda_03c_likes_caindo_sem_meta_mostra_so_o_valor_menor(page: Page, api, base_url):
    abrir_likes(page, api, base_url, 1000, None)
    wait_until(lambda: transforms(page) == esperado("1.000"), "1.000")
    api.set_stats(1, api.live_stats(1, likes=999))
    page.clock.run_for(5000)
    wait_until(lambda: texto_likes(page) == "999", "999")
    page.clock.run_for(1000)
    assert transforms(page) == esperado("999")
    expect(page.locator("#barra")).to_be_hidden()
