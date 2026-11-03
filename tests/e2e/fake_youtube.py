# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""YouTube FALSO da T8: a unica fronteira simulada da pilha de integracao.

Roda DENTRO do namespace de rede de um worker (compose.e2e.yml) e escuta so em 127.0.0.1:
  - Data API v3: GET /youtube/v3/videos (confere o cabecalho X-Goog-Api-Key; monta os itens a partir de uma
    resposta REAL gravada pela T4: tests/worker_lives/fixtures/videos_mixed.json);
  - Studio: GET /channel/... com a estrutura de `yta-smooth-counter` que o scraper le (estrategia 1: digitos em
    `.counter-value.last`); sem o cookie de autenticacao do teste (ou com login_ok=false) redireciona para /signin;
  - GET / (pagina inicial do "www") e GET /signin (pagina de login com um contador-isca, como o fixture da T5).

O estado vem de /state/state.json (arquivo escrito pelo teste, relido a cada requisicao) e cada requisicao e
anotada em /state/<nome>-requests.jsonl. A chave da API NUNCA e gravada: so um booleano `key_ok`.
Este arquivo so e usado por tests/e2e; nenhum codigo de producao o conhece.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STATE = "/state/state.json"
RECORDED = "/fake/videos_recorded.json"
NAME = sys.argv[1] if len(sys.argv) > 1 else "fake"
LOG = f"/state/{NAME}-requests.jsonl"
ROTATED_FLAG = f"/state/{NAME}-rotated"  # D37: a rotacao do cookie de sessao sobrevive a reinicio do falso
TEMPLATE_INDEX = {"live": 0, "upcoming": 1, "none": 2}


def load_state() -> dict:
    with open(STATE, encoding="utf-8") as handle:
        return json.load(handle)


def recorded_items() -> list[dict]:
    with open(RECORDED, encoding="utf-8") as handle:
        return json.load(handle)["body"]["items"]


def build_item(video_id: str, spec: dict, templates: list[dict]) -> dict:
    item = copy.deepcopy(templates[TEMPLATE_INDEX[spec["broadcast"]]])
    item["id"] = video_id
    item["snippet"]["title"] = spec["title"]
    item["snippet"]["liveBroadcastContent"] = spec["broadcast"]
    item["statistics"]["viewCount"] = str(spec["views"])
    item["statistics"]["likeCount"] = str(spec["likes"])
    if spec["broadcast"] == "live":
        item["liveStreamingDetails"]["concurrentViewers"] = str(spec.get("concurrent", spec["views"]))
    return item


def digits_html(value: str) -> str:
    cells = "".join(
        f'<div><div class="counter-value">{(int(d) + 1) % 10}</div><div class="counter-value last">{d}</div></div>'
        for d in value
    )
    return f"<yta-smooth-counter>{cells}</yta-smooth-counter>"


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Studio falso</title></head>
<body>
<div id="host">__COUNTER__</div>
<script>
// Simula o Studio: o contador muda sozinho e, se a sessao for perdida, a propria pagina vai para o login.
async function tick() {
  try {
    const r = await fetch('/__counter', {credentials: 'same-origin', cache: 'no-store'});
    const j = await r.json();
    if (!j.ok) { location.href = '/signin?continue=studio'; return; }
    const html = j.value.split('').map(function (d) {
      return '<div><div class="counter-value">' + ((+d + 1) % 10) + '</div><div class="counter-value last">' + d + '</div></div>';
    }).join('');
    document.getElementById('host').innerHTML = '<yta-smooth-counter>' + html + '</yta-smooth-counter>';
  } catch (e) { /* proximo tick */ }
}
setInterval(tick, 1000);
</script>
</body></html>
"""

LOGIN_PAGE = (
    '<!DOCTYPE html><html><head><meta charset="utf-8"><title>Entrar - login falso</title></head>'
    "<body><yta-smooth-counter>999999</yta-smooth-counter></body></html>"
)


class Handler(BaseHTTPRequestHandler):
    server_version = "fake-youtube"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # silencio: nada de URL/headers em stdout
        return

    # ------------------------------------------------------------------ utilidades
    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, obj: object) -> None:
        self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=UTF-8")

    def _record(self, **fields: object) -> None:
        entry = {"t": time.time(), "path": urlsplit(self.path).path, **fields}
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _cookie_valid(self, state: dict) -> tuple[bool, list[str]]:
        jar = SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        wanted = state["auth_cookie"]
        names = sorted(jar.keys())
        ok = wanted["name"] in jar and jar[wanted["name"]].value == wanted["value"]
        return ok, names

    def _rotation_ok(self, state: dict) -> bool:
        """Chamada da propria pagina: o valor novo vale; o antigo vale ate ser gasto na abertura do Studio."""
        rot = state["rotate"]
        jar = SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        value = jar[rot["name"]].value if rot["name"] in jar else None
        return value == rot["new"] or (value == rot["old"] and not os.path.exists(ROTATED_FLAG))

    def _rotation(self, state: dict) -> tuple[bool, list[str], dict]:
        """D37: como o Google, rotaciona o cookie de sessao. O valor `old` vale UMA vez (a resposta traz `Set-Cookie`
        com o `new`); depois so o `new` vale. Devolve (valido, nomes, cabecalhos extras da resposta)."""
        rot = state["rotate"]
        jar = SimpleCookie()
        jar.load(self.headers.get("Cookie", ""))
        names = sorted(jar.keys())
        value = jar[rot["name"]].value if rot["name"] in jar else None
        if value == rot["new"]:
            return True, names, {}
        if value == rot["old"] and not os.path.exists(ROTATED_FLAG):
            with open(ROTATED_FLAG, "w", encoding="utf-8") as handle:
                handle.write("1")
            return True, names, {"Set-Cookie": f"{rot['name']}={rot['new']}; Path=/"}
        return False, names, {}

    # ------------------------------------------------------------------ rotas
    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        state = load_state()
        if parts.path == "/youtube/v3/videos":
            return self._videos(parts, state)
        if parts.path.startswith("/channel/"):
            extra: dict = {}
            if state.get("rotate"):
                ok, names, extra = self._rotation(state)
            else:
                ok, names = self._cookie_valid(state)
            # o cabecalho Cookie cru fica so no log do teste (diretorio do host), para provar o que o Chromium enviou
            self._record(kind="studio", cookie_ok=ok, cookie_names=names, cookie_header=self.headers.get("Cookie", ""))
            if ok and state.get("login_ok", True):
                page = PAGE.replace("__COUNTER__", digits_html(state["subscribers"]))
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8", extra)
            return self._send(302, b"", "text/plain", {"Location": "/signin?continue=studio"})
        if parts.path == "/__counter":
            ok = self._rotation_ok(state) if state.get("rotate") else self._cookie_valid(state)[0]
            return self._json(200, {"ok": bool(ok and state.get("login_ok", True)), "value": state["subscribers"]})
        if parts.path == "/signin":
            self._record(kind="signin")
            return self._send(200, LOGIN_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        if parts.path == "/":
            self._record(kind="www")
            return self._send(200, b"<!DOCTYPE html><title>www falso</title><p>ok</p>", "text/html; charset=utf-8")
        return self._json(404, {"error": {"code": 404, "message": "not found"}})

    def _videos(self, parts, state: dict) -> None:
        query = parse_qs(parts.query)
        ids = [i for i in query.get("id", [""])[0].split(",") if i]
        key = self.headers.get("X-Goog-Api-Key", "")
        key_ok = hashlib.sha256(key.encode()).hexdigest() == state["api_key_sha256"]
        self._record(
            kind="videos",
            ids=ids,
            part=query.get("part", [""])[0],
            key_ok=key_ok,
            key_in_url="key" in query,
        )
        if not key_ok:
            return self._json(
                400,
                {"error": {"code": 400, "message": "API key not valid.", "errors": [{"reason": "keyInvalid"}]}},
            )
        templates = recorded_items()
        items = [build_item(i, state["videos"][i], templates) for i in ids if i in state["videos"]]
        body = {
            "kind": "youtube#videoListResponse",
            "etag": "t8-fake",
            "items": items,
            "pageInfo": {"totalResults": len(items), "resultsPerPage": len(items)},
        }
        return self._json(200, body)


def main() -> None:
    port = int(os.environ["FAKE_PORT"])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    main()
