"""WEB-SAU-*, WEB-CFG-*, WEB-APR-*, WEB-BTN-*, WEB-END-* (C11/D35): saúde, configurações, aparência dos overlays,
botões metálicos e endpoints do painel. Chromium real contra o dublê validado pelo openapi.yaml."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml
from api_double import FONTS, error, json_reply
from playwright.sync_api import Page, expect
from web_helpers import do_login, freeze_clock, no_html_injected, wait_until

OPENAPI = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
CHANNEL = "UC" + "x1Y2z3" * 3 + "ab_-"  # 22 caracteres depois do UC
assert re.fullmatch(r"UC[0-9A-Za-z_-]{22}", CHANNEL)

SUBS = {"inscritos": "460600", "raw": "460600", "atualizado_em": "2026-11-02T14:32:10.654321Z"}


def entra(page: Page, base_url: str, api) -> None:
    """Login + dropdowns de fonte preenchidos + configurações aplicadas."""
    do_login(page, base_url, api)
    expect(page.locator("#font-subs option")).to_have_count(len(FONTS))
    wait_until(lambda: len(api.requests("GET", "/api/admin/settings")) >= 1, "settings carregado")
    page.wait_for_timeout(100)


def estado(page: Page, componente: str) -> str:
    return page.locator(f".hc[data-component='{componente}'] .hc-state").text_content()  # sem o text-transform do CSS


def link(page: Page, nome: str) -> str:
    return page.evaluate("(n) => document.querySelector(`[data-link='${n}']`).dataset.state", nome)


def campo(page: Page, componente: str, rotulo: str) -> str:
    return page.locator(f".hc[data-component='{componente}'] dt:text-is('{rotulo}') + dd").inner_text()


# --------------------------------------------------------------------------------------------- WEB-CFG
def test_web_cfg_01_dropdown_de_fontes_vem_da_api_e_cada_opcao_usa_a_propria_fonte(page: Page, api, base_url):
    entra(page, base_url, api)
    assert len(api.requests("GET", "/api/admin/fonts")) >= 1
    rotulos = ["Fonte do contador"] + [f"Fonte da live {n}" for n in range(1, 5)]
    for rotulo in rotulos:
        select = page.get_by_label(rotulo, exact=True)
        expect(select).to_be_visible()
        assert select.locator("option").all_inner_texts() == FONTS
    opcoes = page.evaluate(
        "[...document.querySelectorAll('#font-subs option')].map((o) => [o.textContent, o.style.fontFamily])"
    )
    assert len(opcoes) == len(FONTS)
    for nome, familia in opcoes:
        # o navegador serializa sem aspas os nomes de uma palavra; o fallback genérico vem sempre no fim
        assert familia.lstrip('"').startswith(nome) and familia.endswith("sans-serif"), (nome, familia)
    # o próprio dropdown mostra a fonte escolhida
    page.get_by_label("Fonte do contador", exact=True).select_option("Impact")
    assert page.evaluate("document.getElementById('font-subs').style.fontFamily").lstrip('"').startswith("Impact")


def test_web_cfg_02_dropdowns_refletem_o_estado_salvo(page: Page, api, base_url):
    api.channel_id = CHANNEL
    api.subs_font, api.subs_text_color = "Georgia", "#12AB34"
    api.fonts[1], api.text_colors[1] = "Consolas", "#FF8800"
    entra(page, base_url, api)
    expect(page.get_by_label("ID do canal do YouTube")).to_have_value(CHANNEL)
    expect(page.get_by_label("Fonte do contador", exact=True)).to_have_value("Georgia")
    expect(page.get_by_label("Cor do texto do contador", exact=True)).to_have_value("#12ab34")
    expect(page.get_by_label("Fonte da live 2", exact=True)).to_have_value("Consolas")
    expect(page.get_by_label("Cor do texto da live 2", exact=True)).to_have_value("#ff8800")
    expect(page.get_by_label("Fonte da live 1", exact=True)).to_have_value("Roboto")
    expect(page.get_by_label("Cor do texto da live 1", exact=True)).to_have_value("#ffffff")


@pytest.mark.parametrize(
    "valor",
    ["", "abc", "UC" + "a" * 21, "uc" + "a" * 22, "UB" + "a" * 22, "UC" + "a" * 21 + "!", "UC" + "a" * 21 + " é"],
)
def test_web_cfg_03_canal_invalido_e_barrado_no_navegador(page: Page, api, base_url, valor):
    entra(page, base_url, api)
    page.get_by_label("ID do canal do YouTube").fill(valor)
    page.get_by_role("button", name="Salvar canal").click()
    expect(page.locator("#channelErr")).to_contain_text("ID de canal inválido")
    assert api.requests("PUT", "/api/admin/settings") == []
    expect(page.locator("#channelOk")).to_be_hidden()


def test_web_cfg_04_salvar_canal_manda_so_o_canal_com_csrf(page: Page, api, base_url):
    entra(page, base_url, api)
    page.get_by_label("ID do canal do YouTube").fill(CHANNEL)
    page.get_by_role("button", name="Salvar canal").click()
    expect(page.locator("#channelOk")).to_be_visible()
    (req,) = api.requests("PUT", "/api/admin/settings")
    assert req.body == {"channel_id": CHANNEL}
    assert req.headers["x-csrf-token"]
    assert api.channel_id == CHANNEL
    expect(page.get_by_label("ID do canal do YouTube")).to_have_value(CHANNEL)


def test_web_cfg_05_erro_422_da_api_aparece_como_texto(page: Page, api, base_url):
    entra(page, base_url, api)
    rejeita = json_reply(422, {"detail": [{"loc": ["body", "channel_id"], "msg": "<img src=x onerror=window.__xss=1>", "type": "value_error"}]})
    api.override("PUT", "/api/admin/settings", rejeita)
    page.get_by_label("ID do canal do YouTube").fill(CHANNEL)
    page.get_by_role("button", name="Salvar canal").click()
    expect(page.locator("#channelErr")).to_contain_text("<img src=x")
    expect(page.locator("#channelOk")).to_be_hidden()
    no_html_injected(page)


def test_web_cfg_06_salvar_aparencia_manda_settings_e_slots_sem_mexer_em_url_meta_e_cor(page: Page, api, base_url):
    api.slots = ["dQw4w9WgXcQ", "", "", ""]
    api.goals[0], api.colors[0] = 5000, "#00AA11"
    entra(page, base_url, api)
    expect(page.get_by_label("Slot 1", exact=True)).to_have_value("dQw4w9WgXcQ")
    page.get_by_label("Slot 1", exact=True).fill("rascunho nao salvo")  # edição pendente NÃO é gravada aqui

    page.get_by_label("Fonte do contador", exact=True).select_option("Segoe UI Black")
    page.get_by_label("Cor do texto do contador", exact=True).fill("#ff8800")
    page.get_by_label("Fonte da live 3", exact=True).select_option("Times New Roman")
    page.get_by_label("Cor do texto da live 3", exact=True).fill("#0a0b0c")
    page.get_by_role("button", name="Salvar aparência").click()
    expect(page.locator("#lookOk")).to_be_visible()

    (settings,) = api.requests("PUT", "/api/admin/settings")
    assert settings.body == {"subs_font": "Segoe UI Black", "subs_text_color": "#ff8800"}
    (slots,) = api.requests("PUT", "/api/admin/slots")
    assert slots.headers["x-csrf-token"]
    enviados = slots.body["slots"]
    assert len(enviados) == 4
    assert enviados[0] == {"url": "dQw4w9WgXcQ", "goal": 5000, "color": "#00AA11", "font": "Roboto", "text_color": "#ffffff"}
    assert enviados[2] == {"url": "", "goal": None, "color": "#FF0033", "font": "Times New Roman", "text_color": "#0a0b0c"}
    assert api.subs_font == "Segoe UI Black" and api.subs_text_color == "#FF8800"
    assert api.fonts == ["Roboto", "Roboto", "Times New Roman", "Roboto"]
    assert api.text_colors[2] == "#0A0B0C"
    expect(page.get_by_label("Slot 1", exact=True)).to_have_value("rascunho nao salvo")  # segue só no navegador
    assert api.slots[0] == "dQw4w9WgXcQ"


def test_web_cfg_07_erro_ao_salvar_aparencia_mostra_mensagem_e_nao_diz_salvo(page: Page, api, base_url):
    entra(page, base_url, api)
    api.override("PUT", "/api/admin/settings", error(503, "Banco indisponível"))
    page.get_by_label("Fonte do contador", exact=True).select_option("Arial")
    page.get_by_role("button", name="Salvar aparência").click()
    expect(page.locator("#lookErr")).to_contain_text("Banco indisponível")
    expect(page.locator("#lookOk")).to_be_hidden()
    assert api.requests("PUT", "/api/admin/slots") == []


def test_web_cfg_08_slots_salvos_pelo_botao_antigo_nao_apagam_a_aparencia(page: Page, api, base_url):
    api.fonts[0], api.text_colors[0] = "Impact", "#112233"
    entra(page, base_url, api)
    page.get_by_label("Slot 1", exact=True).fill("dQw4w9WgXcQ")
    page.get_by_role("button", name="Salvar slots").click()
    expect(page.locator("#saveMsg")).to_be_visible()
    (req,) = api.requests("PUT", "/api/admin/slots")
    assert "font" not in req.body["slots"][0] and "text_color" not in req.body["slots"][0]
    assert api.fonts[0] == "Impact" and api.text_colors[0] == "#112233"


def test_web_cfg_09_a11y_nomes_unicos_e_teclado(page: Page, api, base_url):
    entra(page, base_url, api)
    for rotulo in ["Fonte do contador", "Cor do texto do contador", "ID do canal do YouTube"] + [
        f"{t} da live {n}" for n in range(1, 5) for t in ("Fonte", "Cor do texto")
    ]:
        expect(page.get_by_label(rotulo, exact=True)).to_have_count(1)
    page.get_by_label("Fonte da live 4", exact=True).focus()
    foco = page.evaluate("(() => { const c = getComputedStyle(document.activeElement); return [c.outlineStyle, c.outlineWidth]; })()")
    assert foco[0] == "solid" and foco[1] != "0px"
    page.keyboard.press("ArrowDown")
    assert page.get_by_label("Fonte da live 4", exact=True).input_value() != "Roboto"


# --------------------------------------------------------------------------------------------- WEB-APR
def abre_contador(page: Page, api, base_url, **overrides):
    api.subscribers = SUBS
    api.subs_font = overrides.get("font", api.subs_font)
    api.subs_text_color = overrides.get("color", api.subs_text_color)
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)


def estilo(page: Page, seletor: str) -> tuple[str, str]:
    return tuple(
        page.evaluate("(s) => { const c = getComputedStyle(document.querySelector(s)); return [c.fontFamily, c.color]; }", seletor)
    )


def test_web_apr_01_contador_aplica_fonte_e_cor_vindas_da_api(page: Page, api, base_url):
    abre_contador(page, api, base_url, font="Impact", color="#FF8800")
    familia, cor = estilo(page, "#contador")
    assert familia == 'Impact, sans-serif' and cor == "rgb(255, 136, 0)"
    # os dígitos herdam, e a configuração aplicada vai pelo CSSOM (o HTML não ganhou atributo style)
    assert estilo(page, ".digito-inner span") == (familia, cor)
    assert page.evaluate("document.getElementById('contador').getAttribute('style')") is not None


def test_web_apr_01b_padrao_roboto_mantem_o_visual_original(page: Page, api, base_url):
    abre_contador(page, api, base_url)  # Roboto + branco = o que o overlay sempre teve
    familia, cor = estilo(page, "#contador")
    assert familia == '"YouTube Sans", Roboto, sans-serif' and cor == "rgb(255, 255, 255)"


def test_web_apr_02_contador_acompanha_mudancas_no_proximo_polling(page: Page, api, base_url):
    freeze_clock(page)
    abre_contador(page, api, base_url)
    assert estilo(page, "#contador")[1] == "rgb(255, 255, 255)"
    api.subs_font, api.subs_text_color = "Georgia", "#00FF00"  # o número NÃO muda
    page.clock.run_for(5000)
    wait_until(lambda: estilo(page, "#contador") == ("Georgia, sans-serif", "rgb(0, 255, 0)"), "nova aparência")


MALICIOSOS = [
    ("Arial; } body{background:red", "#FF0033; background:red"),
    ("url(http://evil.example/x)", "url(x)"),
    ('"Arial"', "red"),
    ("Arial, sans-serif", "#GGGGGG"),
    ("Arial\n} * {display:none", "#FFF"),
    ("a" * 41, "#FF00331"),
    ("</style><script>window.__xss=1</script>", "expression(alert(1))"),
]


@pytest.mark.parametrize("fonte, cor", MALICIOSOS, ids=lambda v: str(v)[:20])
def test_web_apr_03_valor_estranho_vindo_da_rede_e_descartado_no_contador(page: Page, api, base_url, fonte, cor):
    corpo = json.dumps({**SUBS, "font": fonte, "text_color": cor})
    page.route("**/api/public/subscribers*", lambda r: r.fulfill(status=200, content_type="application/json", body=corpo))
    page.goto(f"{base_url}/contador")
    expect(page.locator(".digito-wrap")).to_have_count(6)
    familia, cor_final = estilo(page, "#contador")
    assert familia == '"YouTube Sans", Roboto, sans-serif' and cor_final == "rgb(255, 255, 255)"  # padrão
    assert page.evaluate("getComputedStyle(document.body).backgroundColor") == "rgba(0, 0, 0, 0)"
    assert page.evaluate("document.getElementById('contador').style.length") == 0
    assert page.evaluate("window.__xss") is None


def configura_live(api, slot=1, **kw):
    api.set_stats(slot, api.live_stats(slot, likes=1000))
    api.goals[slot - 1] = 10000
    api.fonts[slot - 1] = kw.get("font", "Roboto")
    api.text_colors[slot - 1] = kw.get("color", "#FFFFFF")


def test_web_apr_04_live_aplica_fonte_e_cor_do_slot_certo(page: Page, api, base_url):
    configura_live(api, 2, font="Verdana", color="#12AB34")
    configura_live(api, 1, font="Impact", color="#FF0000")
    page.goto(f"{base_url}/overlay/live.html?slot=2")
    expect(page.locator(".digito-wrap")).not_to_have_count(0)
    assert estilo(page, "#contagem") == ("Verdana, sans-serif", "rgb(18, 171, 52)")
    assert estilo(page, ".digito-inner span") == ("Verdana, sans-serif", "rgb(18, 171, 52)")
    # a cor da barra (C10) segue independente da cor do texto
    assert page.evaluate("getComputedStyle(document.getElementById('barra-preenchimento')).backgroundColor") == "rgb(255, 0, 51)"


@pytest.mark.parametrize("fonte, cor", MALICIOSOS, ids=lambda v: str(v)[:20])
def test_web_apr_05_valor_estranho_vindo_da_rede_e_descartado_na_live(page: Page, api, base_url, fonte, cor):
    base = api.live_stats(1, likes=1000)
    corpo = json.dumps({**base, "goal": 10000, "color": "#00AA11", "font": fonte, "text_color": cor})
    page.route("**/api/public/lives/1*", lambda r: r.fulfill(status=200, content_type="application/json", body=corpo))
    page.goto(f"{base_url}/overlay/live.html?slot=1")
    expect(page.locator(".digito-wrap")).not_to_have_count(0)
    familia, cor_final = estilo(page, "#contagem")
    assert familia == '"YouTube Sans", Roboto, sans-serif' and cor_final == "rgb(255, 255, 255)"
    assert page.evaluate("document.getElementById('contagem').style.length") == 0
    assert page.evaluate("window.__xss") is None


# --------------------------------------------------------------------------------------------- WEB-SAU
def test_web_sau_01_cartoes_e_diagrama_com_tudo_saudavel(page: Page, api, base_url):
    api.cookies = {"configured": True, "count": 12, "uploaded_at": api.worker_state()["last_heartbeat_at"], "applied": True}
    entra(page, base_url, api)
    expect(page.locator(".hc")).to_have_count(5)
    nomes = page.locator(".hc .hc-name").all_inner_texts()
    assert nomes == ["API", "Banco de dados", "Worker de lives", "Worker de inscritos", "Cookies e autenticação"]
    for comp in ("api", "db", "lives", "inscritos", "auth"):
        assert estado(page, comp) == "ok", comp
        for rotulo in ("Último sinal", "Último sucesso", "Falhas seguidas", "Intervalo", "Mensagem"):
            assert campo(page, comp, rotulo) != "", (comp, rotulo)
    assert campo(page, "lives", "Falhas seguidas") == "0" and campo(page, "lives", "Intervalo") == "30 s"
    rotulos = page.evaluate("[...document.querySelectorAll('#healthDiagram .node text')].map((t) => t.textContent)")
    assert rotulos == ["YouTube", "worker-lives", "worker-inscritos", "Banco", "API", "Web"]
    assert page.locator("#healthDiagram [data-link]").count() == 6
    for nome in ("youtube-lives", "youtube-inscritos", "lives-db", "inscritos-db", "db-api", "api-web"):
        assert link(page, nome) == "ok", nome
    # pontos animados nas ligações saudáveis (CSS)
    nome_anim = page.evaluate("getComputedStyle(document.querySelector('[data-link=\"db-api\"] .link-dots')).animationName")
    assert nome_anim == "flow"
    assert "Falha" not in page.locator("#diagramLabel").inner_html() and "sem falhas" in page.locator("#diagramLabel").text_content()


def test_web_sau_02_worker_degradado_destaca_a_ligacao_com_o_youtube(page: Page, api, base_url):
    api.lives_worker = api.worker_state(
        "degraded", consecutive_failures=3, last_error_code="http_error", last_error_message="O YouTube respondeu com erro"
    )
    entra(page, base_url, api)
    assert estado(page, "lives") == "degradado"
    assert campo(page, "lives", "Falhas seguidas") == "3"
    assert campo(page, "lives", "Mensagem") == "O YouTube respondeu com erro"
    assert link(page, "youtube-lives") == "degraded"
    assert link(page, "lives-db") == "ok" and link(page, "db-api") == "ok"
    assert page.evaluate("document.querySelector('[data-node=\"lives\"]').dataset.state") == "degraded"


def test_web_sau_03_erro_de_cookies_marca_a_ligacao_em_falha_sem_animacao(page: Page, api, base_url):
    api.cookies = {"configured": True, "count": 3, "uploaded_at": api.worker_state()["last_heartbeat_at"], "applied": True}
    api.inscritos_worker = {
        **api.worker_state("error", last_error_code="login_required", last_error_message="Login perdido no YouTube Studio"),
        "auth_state": "login_required",
    }
    entra(page, base_url, api)
    assert estado(page, "inscritos") == "erro" and estado(page, "auth") == "erro"
    assert "pediu login" in campo(page, "auth", "Mensagem")
    assert link(page, "youtube-inscritos") == "error"
    assert link(page, "youtube-lives") == "ok" and link(page, "inscritos-db") == "ok"
    falha = page.evaluate(
        """() => { const l = document.querySelector('[data-link="youtube-inscritos"]');
        const dots = getComputedStyle(l.querySelector('.link-dots')); const line = getComputedStyle(l.querySelector('.link-line'));
        return [dots.animationName, line.stroke, line.strokeDasharray]; }"""
    )
    assert falha[0] == "none"  # sem pontos correndo na ligação em falha
    assert falha[1] == "rgb(248, 113, 113)" and falha[2] != "none"  # vermelha e tracejada
    assert "youtube → inscritos: erro" in page.locator("#diagramLabel").text_content()


def test_web_sau_04_db_error_marca_a_ligacao_worker_banco_e_nao_a_do_youtube(page: Page, api, base_url):
    api.lives_worker = api.worker_state("degraded", last_error_code="db_error", last_error_message="Erro ao acessar o banco de dados")
    entra(page, base_url, api)
    assert link(page, "lives-db") == "error" and link(page, "youtube-lives") == "ok"


def test_web_sau_05_workers_sem_dados_ficam_sem_dados(page: Page, api, base_url):
    api.lives_worker = {**api.worker_state("starting"), "last_heartbeat_at": None, "last_success_at": None, "interval_seconds": 30}
    entra(page, base_url, api)
    assert estado(page, "lives") == "sem dados"
    assert campo(page, "lives", "Último sucesso") == "—"
    assert link(page, "youtube-lives") == "unknown"


def test_web_sau_06_atualiza_a_cada_10_segundos_com_dados_so_de_health_e_status(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    wait_until(lambda: len(api.requests("GET", "/health")) >= 1, "primeiro /health")
    expect(page.locator(".hc")).to_have_count(5)
    h0 = len(api.requests("GET", "/health"))
    s0 = len(api.requests("GET", "/api/admin/workers/status"))
    assert estado(page, "lives") == "ok"

    api.lives_worker = api.worker_state("error", consecutive_failures=7, last_error_code="quota_exceeded", last_error_message="Cota diária esgotada")
    page.clock.run_for(9900)
    page.wait_for_timeout(200)
    assert len(api.requests("GET", "/health")) == h0  # ainda não passaram 10 s
    page.clock.run_for(100)
    wait_until(lambda: len(api.requests("GET", "/health")) == h0 + 1, "10 s: /health")
    wait_until(lambda: len(api.requests("GET", "/api/admin/workers/status")) == s0 + 1, "10 s: status")
    expect(page.locator(".hc[data-component='lives'] .hc-state")).to_have_text("erro")
    assert campo(page, "lives", "Falhas seguidas") == "7"
    assert link(page, "youtube-lives") == "error"

    api.lives_worker = api.worker_state("ok")
    page.clock.run_for(10000)
    expect(page.locator(".hc[data-component='lives'] .hc-state")).to_have_text("ok")
    assert link(page, "youtube-lives") == "ok"
    # a seção só conversa com /health e /api/admin/workers/status (e a rotina dos demais painéis)
    admin_gets = {r.path for r in api.log if r.method == "GET" and r.path.startswith("/api/")}
    assert admin_gets <= {"/api/admin/session", "/api/admin/slots", "/api/admin/workers/status", "/api/admin/settings", "/api/admin/fonts"}


def test_web_sau_07_api_fora_do_ar_mostra_erro_na_api_e_o_diagrama_destaca(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    expect(page.locator(".hc")).to_have_count(5)
    api.override("GET", "/health", error(503, "Serviço indisponível"))
    page.clock.run_for(10000)
    expect(page.locator(".hc[data-component='api'] .hc-state")).to_have_text("erro")
    assert "HTTP 503" in campo(page, "api", "Mensagem")
    assert campo(page, "api", "Falhas seguidas") == "1"
    assert link(page, "db-api") == "error" and link(page, "api-web") == "error"
    api.overrides.clear()
    page.clock.run_for(10000)
    expect(page.locator(".hc[data-component='api'] .hc-state")).to_have_text("ok")
    assert campo(page, "api", "Falhas seguidas") == "0"


def test_web_sau_08_texto_do_servidor_nunca_vira_html_nem_entra_no_diagrama(page: Page, api, base_url):
    api.lives_worker = api.worker_state(
        "degraded", last_error_code="http_error", last_error_message="<img src=x onerror=window.__xss=1><svg onload=window.__xss=2>"
    )
    entra(page, base_url, api)
    assert "<img src=x" in campo(page, "lives", "Mensagem")
    no_html_injected(page)
    tags = set(page.evaluate("[...document.querySelectorAll('#healthDiagram *')].map((e) => e.tagName.toLowerCase())"))
    assert tags <= {"title", "g", "path", "rect", "text"}, tags
    assert page.evaluate("document.querySelectorAll('#healthCards img, #healthCards svg, #healthCards script').length") == 0


def test_web_sau_09_prefers_reduced_motion_desliga_as_animacoes(page: Page, api, base_url):
    page.emulate_media(reduced_motion="reduce")
    entra(page, base_url, api)
    nomes = page.evaluate(
        """() => [getComputedStyle(document.querySelector('[data-link="db-api"] .link-dots')).animationName,
                   getComputedStyle(document.querySelector('#saveBtn')).animationName]"""
    )
    assert nomes == ["none", "none"]


def test_web_sau_10_sessao_expirada_na_saude_volta_ao_login(page: Page, api, base_url):
    freeze_clock(page)
    do_login(page, base_url, api)
    expect(page.locator(".hc")).to_have_count(5)
    api.expire_sessions()
    page.clock.run_for(10000)
    expect(page.locator("#loginView")).to_be_visible()
    expect(page.locator("#healthCards .hc")).to_have_count(0)


# --------------------------------------------------------------------------------------------- WEB-BTN
def test_web_btn_01_botoes_em_pilula_com_borda_cromada_animada_so_com_css(page: Page, api, base_url):
    entra(page, base_url, api)
    for seletor in ("#saveBtn", "#channelBtn", "#lookBtn", "#cookiesBtn", "#logoutBtn", ".ep-copy"):
        info = page.evaluate(
            """(s) => { const e = document.querySelector(s), c = getComputedStyle(e);
            return [c.animationName, c.backgroundImage, parseFloat(c.borderTopLeftRadius) >= e.getBoundingClientRect().height / 2 - 1, c.animationIterationCount]; }""",
            seletor,
        )
        assert info[0] == "chrome-spin" and info[3] == "infinite", (seletor, info)
        assert "conic-gradient" in info[1], (seletor, info)
        assert info[2], f"{seletor} não é pílula"
    # o ângulo é uma propriedade registrada (@property) animada pelo navegador
    angulos = {
        page.evaluate("getComputedStyle(document.querySelector('#saveBtn')).getPropertyValue('--chrome-angle')")
        for _ in range(1)
    }
    assert all(a.strip().endswith("deg") for a in angulos), angulos


def test_web_btn_02_visual_do_painel_bento(page: Page, api, base_url):
    entra(page, base_url, api)
    info = page.evaluate(
        """() => { const c = getComputedStyle(document.querySelector('.card')), b = getComputedStyle(document.body);
        const pill = getComputedStyle(document.querySelector('.pill'));
        return [b.backgroundColor, c.borderTopWidth, c.borderTopLeftRadius, pill.borderTopLeftRadius,
                document.querySelectorAll('.section-title .dim').length]; }"""
    )
    assert info[0] == "rgb(5, 5, 6)"  # quase preto
    assert info[1] == "1px" and info[2] == "16px"
    assert float(info[3].replace("px", "")) >= 9  # pílula
    assert info[4] >= 5  # uma palavra em cinza nos títulos


# --------------------------------------------------------------------------------------------- WEB-END
def caminhos_publicos_do_openapi() -> set[str]:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    saida: set[str] = set()
    for path, item in spec["paths"].items():
        if "get" not in item or path.startswith("/api/admin") or path in ("/", "/admin"):
            continue
        if "{" in path:
            saida |= {re.sub(r"\{[a-z]+\}", str(n), path) for n in range(1, 5)}
        else:
            saida.add(path)
    return saida


def test_web_end_01_secao_endpoints_lista_tudo_que_e_publico_com_url_completa_e_copiar(page: Page, api, base_url):
    entra(page, base_url, api)
    esperados = caminhos_publicos_do_openapi()
    assert {"/health", "/contador", "/inscritos.json", "/api/public/subscribers", "/live1.json", "/api/public/lives/4"} <= esperados
    urls = page.locator("#endpoints .ep-url").all_inner_texts()
    base = base_url.rstrip("/")
    assert all(u.startswith(base + "/") for u in urls)
    listados = {u[len(base):] for u in urls}
    assert esperados <= listados, esperados - listados
    # os overlays de likes (fora do openapi por serem estáticos) continuam na lista
    assert {f"/overlay/live.html?slot={n}" for n in range(1, 5)} <= listados
    assert len(urls) == len(set(urls))
    expect(page.locator("#endpoints .ep-copy")).to_have_count(len(urls))
    assert not any("/api/admin" in u for u in urls)  # nada de admin entre as saídas públicas
    assert page.get_by_role("heading", name="Endpoints", level=2).count() == 1


# --------------------------------------------------------------------------------------------- autoteste do detector
@pytest.mark.parametrize(
    "onde, html",
    [
        (".slot-title", '<svg id="healthDiagram"></svg>'),  # titulo de slot com o id do diagrama
        ("body", '<svg id="healthDiagram"></svg>'),  # outro #healthDiagram fora do conteiner
        (".diagram-wrap", '<svg id="healthDiagram"></svg>'),  # segundo dentro do proprio conteiner
        ("#healthCards", '<svg></svg>'),
        ("#healthCards", '<img src="data:,">'),
    ],
    ids=["titulo-de-slot", "fora-do-conteiner", "segundo-no-conteiner", "svg-comum", "img"],
)
def test_web_sau_11_o_detector_anti_xss_pega_svg_injetado_mesmo_com_o_id_do_diagrama(page: Page, api, base_url, onde, html):
    from web_helpers import injected_count

    api.set_stats(1, api.live_stats(1))
    entra(page, base_url, api)
    assert injected_count(page) == 0  # so o diagrama estatico da secao Saude e aceito
    no_html_injected(page)
    page.evaluate("([sel, html]) => document.querySelector(sel).insertAdjacentHTML('beforeend', html)", [onde, html])
    assert injected_count(page) >= 1, html
    with pytest.raises(AssertionError, match="elementos injetados"):
        no_html_injected(page)
    page.evaluate("window.__xss = undefined")
