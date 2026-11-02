"""SEC-03 (XSS armazenado de ponta a ponta) e SEC-23 (credenciais/admin sem CORS, incluindo o navegador)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from e2e_browser import chromium_context, ui_login, watch_dialogs
from e2e_stack import LIVE_INTERVAL, SLOT_URL, USERNAME, header, wait_until
from sec_support import JSON_HEADERS, cors_headers, first_with_status, flood, openapi_routes

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

class _BlankPage(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        body = b"<!doctype html><title>outra origem</title><p>x</p>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


XSS_ALERT = "<img src=x onerror=alert(1)>"
XSS_FLAG = "<img src=x onerror=window.__xss=1>"
VID_XSS_1, VID_XSS_2 = "Xss00000001", "Xss00000002"


def test_sec_03_titulo_malicioso_do_youtube_vira_texto_no_painel_sem_alert(stack):
    # o YouTube FALSO devolve titulos maliciosos; quem grava e o worker-lives REAL; quem mostra e o painel REAL (8081)
    stack.fake_set_video(VID_XSS_1, title=XSS_ALERT, broadcast="live", views=10, likes=1, concurrent=10)
    stack.fake_set_video(VID_XSS_2, title=XSS_FLAG, broadcast="live", views=20, likes=2, concurrent=20)
    stack.set_slots([SLOT_URL.format(VID_XSS_1), SLOT_URL.format(VID_XSS_2), "", ""])
    try:
        wait_until(
            lambda: stack.public_json("/live1.json")[0]["titulo"] == XSS_ALERT
            and stack.public_json("/live2.json")[0]["titulo"] == XSS_FLAG,
            timeout=2 * LIVE_INTERVAL + 5,
            interval=1,
        )
        # o dado malicioso percorreu o caminho inteiro: esta no JSON publico como DADO (escapado pelo JSON, nunca HTML)
        raw = stack.public("/live1.json")
        assert header(raw, "Content-Type").startswith("application/json")
        assert json.loads(raw["body"])[0]["titulo"] == XSS_ALERT

        with chromium_context() as ctx:
            page = ctx.new_page()
            dialogs = watch_dialogs(page)
            page_errors: list[str] = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            ui_login(page, "http://127.0.0.1:8081", USERNAME, stack.password)
            titles = page.locator(".slot-card .slot-title")
            wait_until(lambda: titles.nth(0).inner_text() == XSS_ALERT and titles.nth(1).inner_text() == XSS_FLAG, timeout=30, interval=0.5)

            # aparece como TEXTO literal, sem nenhum elemento criado a partir do titulo
            assert titles.nth(0).evaluate("e => e.textContent") == XSS_ALERT
            assert titles.nth(0).evaluate("e => e.children.length") == 0
            assert titles.nth(1).evaluate("e => e.children.length") == 0
            assert page.locator("img").count() == 0, "um <img> foi criado a partir do titulo"
            assert page.evaluate("window.__xss") is None
            page.wait_for_timeout(2000)  # tempo para um onerror (se existisse) disparar
            assert dialogs == [], f"alert disparou: {dialogs}"
            assert page.evaluate("window.__xss") is None
            assert page_errors == []

            # os overlays/paginas publicas que leem o mesmo dado tambem nao executam nada
            public = ctx.new_page()
            public_dialogs = watch_dialogs(public)
            public.goto("http://127.0.0.1:8080/api/public/lives/1")
            public.wait_for_timeout(500)
            assert public_dialogs == []
    finally:
        stack.ensure_lives()


def _sample_requests(stack):
    """(porta, metodo, caminho, corpo) para varrer TODAS as rotas do openapi.yaml, com e sem Origin."""
    for port in (8080, 8081):
        for method, path in openapi_routes():
            body = "{}" if method in ("POST", "PUT") else None
            yield port, method, path, body


def test_sec_23_credenciais_e_admin_sem_cors_em_todas_as_rotas_e_no_navegador(stack):
    stack.ensure_lives()
    stack.ensure_subs("460600")
    seen = 0
    admin_seen = set()
    for port, method, path, body in _sample_requests(stack):
        for origin in (None, "https://qualquer.example"):
            for authenticated in (False, True):
                headers = dict(JSON_HEADERS)
                if origin:
                    headers["Origin"] = origin
                if method == "OPTIONS":
                    headers["Access-Control-Request-Method"] = "PUT"
                    headers["Access-Control-Request-Headers"] = "content-type,x-csrf-token"
                use_body = body
                if authenticated:
                    headers["Cookie"] = stack.session.cookie_header
                    headers["X-CSRF-Token"] = stack.session.csrf
                    if path == "/api/admin/slots" and method == "PUT":
                        use_body = json.dumps({"slots": []})  # 422: passa auth/CSRF e NAO altera nada
                    if path == "/api/admin/cookies" and method == "POST":
                        use_body = "[]"  # 400: idem
                    if path == "/api/admin/logout":
                        continue  # encerraria a sessao compartilhada; o 401 sem sessao ja cobre a rota
                r = stack.on_port(port, path, method, headers, use_body)
                seen += 1
                assert header(r, "Access-Control-Allow-Credentials") is None, (port, method, path, "Allow-Credentials")
                if path.startswith("/api/admin"):
                    assert cors_headers(r) == [], (port, method, path, r["status"], cors_headers(r))
                    admin_seen.add((port, method, path, r["status"]))
    assert seen > 100
    # a varredura passou por 401, 403 (nao: sem CSRF abaixo), 404 e por respostas com sessao (200/400/422)
    assert {s for *_, s in admin_seen} >= {401, 200, 422, 400}, sorted({s for *_, s in admin_seen})

    # 403 (sessao sem CSRF), 404 (com sessao) e 429 (rajada) do admin, com Origin: tambem sem nenhum Access-Control-*
    h = {"Cookie": stack.session.cookie_header, "Origin": "https://qualquer.example", **JSON_HEADERS}
    r403 = stack.admin("/api/admin/slots", "PUT", h, json.dumps({"slots": ["", "", "", ""]}))
    r404 = stack.admin("/api/admin/nao-existe", "GET", h)
    r429 = first_with_status(flood(stack)["admin_8081"], 429)
    assert (r403["status"], r404["status"]) == (403, 404)
    for r in (r403, r404, r429):
        assert cors_headers(r) == []

    # --- navegador: de OUTRA origem (servidor local qualquer, sem CSP), fetch com credentials:'include' a rota publica FALHA
    page_server = ThreadingHTTPServer(("127.0.0.1", 0), _BlankPage)
    threading.Thread(target=page_server.serve_forever, daemon=True).start()
    other_origin = f"http://127.0.0.1:{page_server.server_address[1]}/"
    probe = (
        "async ([u, c]) => { try { const r = await fetch(u, c ? {credentials: c} : {});"
        " return {ok: true, status: r.status, body: await r.text()}; } catch (e) { return {ok: false, err: String(e)}; } }"
    )
    try:
        with chromium_context() as ctx:
            page = ctx.new_page()
            page.goto(other_origin)
            target = "http://127.0.0.1:8080/api/public/subscribers"
            without = page.evaluate(probe, [target, None])
            assert without["ok"] is True and without["status"] == 200, without  # CORS * permite leitura sem credenciais
            assert json.loads(without["body"])["inscritos"]
            with_credentials = page.evaluate(probe, [target, "include"])
            assert with_credentials["ok"] is False, f"fetch com credenciais de outra origem NAO deveria funcionar: {with_credentials}"
            # o admin nao pode ser lido de outra origem, nem com nem sem credenciais (nao ha Access-Control-Allow-Origin)
            for url in ("http://127.0.0.1:8081/api/admin/session", "http://127.0.0.1:8081/api/admin/workers/status"):
                for creds in ("omit", "include"):
                    result = page.evaluate(probe, [url, creds])
                    assert result["ok"] is False, (url, creds, result)
    finally:
        page_server.shutdown()
