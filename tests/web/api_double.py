"""Servidor-dublê do contrato HTTP da LiveStats (D26).

Faz o papel do Nginx + api para o front:
  * serve `web/static` nos caminhos do contrato (repo-layout T6) com a MESMA
    CSP e cabeçalhos do ARCHITECTURE §7;
  * responde `/api/admin/*`, `/api/public/*`, aliases legados e `/health`
    seguindo `docs/openapi.yaml`.

TODA resposta de API passa por `contract_check.Contract.check_response`. Se o
dublê gerar algo fora do contrato, a resposta vira 500 e a violação fica em
`ApiDouble.violations` (a fixture `api` falha o teste que a provocou).

O estado é controlado pelos testes (slots, estatísticas, sessões...); o dublê
em si nunca "adivinha" nada do front: ele só implementa as regras do contrato
(sessão, CSRF, limite de login, validação de slots e cookies).
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from contract_check import Contract, ContractError

REPO = Path(__file__).resolve().parents[2]
STATIC = REPO / "web" / "static"
LEGACY_DIR = REPO / "tests" / "contract" / "legacy"

SESSION_COOKIE = "__Host-livestats_session"

# ARCHITECTURE §7 (literal do contrato).
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
CSP_OVERLAY = CSP.replace("frame-ancestors 'none'", "frame-ancestors *")
PERMISSIONS_POLICY = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"

VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|/live/)([a-zA-Z0-9_-]{11})")
VIDEO_ID_BARE = re.compile(r"^([a-zA-Z0-9_-]{11})$")

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}

# D35: lista fechada de fontes (copia do contrato para o dublê; a api real a define em api/app/fonts.py)
FONTS = [
    "Roboto", "Segoe UI", "Segoe UI Black", "Arial", "Arial Black", "Bahnschrift", "Calibri", "Cambria", "Candara",
    "Consolas", "Constantia", "Corbel", "Franklin Gothic Medium", "Georgia", "Impact", "Lucida Console",
    "Palatino Linotype", "Tahoma", "Times New Roman", "Trebuchet MS", "Verdana",
]
HTML_PAGES = {"/", "/admin"}
OVERLAY_PAGES = {"/contador", "/overlay/inscritos"}


def utc_iso(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def legacy_ts(dt: datetime | None = None) -> str:
    dt = dt or datetime.now(timezone.utc)
    return dt.replace(tzinfo=None).isoformat() + "Z"


def extrai_video_id(texto: str) -> str | None:
    texto = texto.strip()
    m = VIDEO_ID.search(texto) or VIDEO_ID_BARE.search(texto)
    return m.group(1) if m else None


def formatar(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}K"
    return str(n)


@dataclass
class Reply:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)


def json_reply(status: int, payload: Any, headers: dict[str, str] | None = None) -> Reply:
    h = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    h.update(headers or {})
    return Reply(status, json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(), h)


def error(status: int, detail: str, headers: dict[str, str] | None = None) -> Reply:
    return json_reply(status, {"detail": detail}, headers)


def validation_error(errors: list[dict[str, Any]]) -> Reply:
    """ValidationError do contrato: nunca devolve o valor enviado."""
    detail = [{"loc": e["loc"], "msg": e["msg"], "type": e["type"]} for e in errors]
    return json_reply(422, {"detail": detail})


@dataclass
class RequestRecord:
    method: str
    path: str  # sem query
    query: str
    headers: dict[str, str]  # chaves em minúsculas
    body: Any  # JSON decodificado, ou None
    raw_body: bytes
    status: int = 0

    @property
    def url(self) -> str:
        return self.path + (f"?{self.query}" if self.query else "")


class ApiDouble:
    """Estado + regras do contrato. Thread-safe por um lock único."""

    def __init__(self) -> None:
        self.contract = Contract()
        self.lock = threading.RLock()
        self.violations: list[str] = []
        self.log: list[RequestRecord] = []
        self.reset()

    # ---- estado ------------------------------------------------------------

    def reset(self) -> None:
        with self.lock:
            self.username = "admin"
            self.password = "senha-de-teste-do-dublê"
            self.public_base_url: str | None = None
            self.sessions: dict[str, dict[str, Any]] = {}
            self.slots = ["", "", "", ""]
            self.goals: list[int | None] = [None, None, None, None]  # D34: meta de likes por slot
            self.colors = ["#FF0033"] * 4  # D34: cor da barra por slot
            self.fonts = ["Roboto"] * 4  # D35: fonte do texto por slot
            self.text_colors = ["#FFFFFF"] * 4  # D35: cor do texto por slot
            self.channel_id: str | None = None  # D35
            self.subs_font = "Roboto"
            self.subs_text_color = "#FFFFFF"
            self.saved_at: str | None = None
            self.stats: dict[int, dict[str, Any] | None] = {1: None, 2: None, 3: None, 4: None}
            self.subscribers: dict[str, str] | None = None
            self.subscribers_status: int = 200
            self.lives_worker = self.worker_state("ok")
            self.inscritos_worker = {**self.worker_state("ok"), "auth_state": "ok"}
            self.cookies = {"configured": False, "count": 0, "uploaded_at": None, "applied": False}
            self.login_failures: list[tuple[float, str]] = []
            self.overrides: dict[tuple[str, str], list[Reply]] = {}
            self.violations.clear()
            self.log.clear()

    @staticmethod
    def worker_state(state: str = "ok", **extra: Any) -> dict[str, Any]:
        now = utc_iso()
        base: dict[str, Any] = {
            "state": state,
            "last_heartbeat_at": now,
            "last_success_at": now,
            "consecutive_failures": 0,
            "interval_seconds": 30,
            "next_attempt_at": None,
            "last_error_code": None,
            "last_error_message": None,
        }
        base.update(extra)
        return base

    def live_stats(self, slot: int, **kw: Any) -> dict[str, Any]:
        """Monta um LiveSlot válido; `kw` sobrescreve campos."""
        views = kw.pop("views", 1234)
        likes = kw.pop("likes", 56)
        data = {
            "slot": slot,
            "ativo": "true",
            "video_id": "dQw4w9WgXcQ",
            "titulo": "Sorteio ao vivo",
            "status": "ao_vivo",
            "views": views,
            "likes": likes,
            "views_formatado": formatar(views),
            "likes_formatado": formatar(likes),
            "atualizado_em": legacy_ts(),
        }
        data.update(kw)
        return data

    def set_stats(self, slot: int, stats: dict[str, Any] | None) -> None:
        with self.lock:
            self.stats[slot] = stats

    def expire_sessions(self) -> None:
        """Simula a expiração no servidor: o cookie do navegador deixa de valer."""
        with self.lock:
            self.sessions.clear()

    def override(self, method: str, path: str, *replies: Reply) -> None:
        """Respostas forçadas (uma por chamada, na ordem) para um endpoint.

        Passam pela MESMA validação de contrato: só dá para forçar respostas que
        o openapi.yaml prevê.
        """
        with self.lock:
            self.overrides[(method.upper(), path)] = list(replies)

    def requests(self, method: str, path: str) -> list[RequestRecord]:
        with self.lock:
            return [r for r in self.log if r.method == method and r.path == path]

    # ---- sessão ------------------------------------------------------------

    def _session(self, headers: dict[str, str]) -> tuple[str, dict[str, Any]] | None:
        for part in headers.get("cookie", "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == SESSION_COOKIE and value in self.sessions:
                return value, self.sessions[value]
        return None

    def _session_payload(self, sess: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        return {
            "authenticated": True,
            "username": sess["username"],
            "csrf_token": sess["csrf"],
            "expires_at": utc_iso(now + timedelta(hours=8)),
            "idle_expires_at": utc_iso(now + timedelta(minutes=30)),
            "public_base_url": self.public_base_url,
        }

    # ---- despacho ----------------------------------------------------------

    def handle(self, rec: RequestRecord) -> Reply:
        """Aplica as regras do contrato e devolve a resposta (já validada)."""
        with self.lock:
            self.log.append(rec)
            reply = self._dispatch(rec)
            rec.status = reply.status
            return self.validated(rec, reply)

    def validated(self, rec: RequestRecord, reply: Reply) -> Reply:
        try:
            self.contract.check_response(rec.method, rec.path, reply.status, reply.headers, reply.body)
        except ContractError as exc:
            self.violations.append(str(exc))
            rec.status = 500
            return error(500, f"VIOLAÇÃO DO CONTRATO: {exc}")
        return reply

    def _dispatch(self, rec: RequestRecord) -> Reply:
        key = (rec.method, rec.path)
        queue = self.overrides.get(key)
        if queue:
            return queue.pop(0) if len(queue) > 1 else queue[0]

        if rec.method == "OPTIONS":
            return Reply(204, b"", cors())

        path = rec.path
        if path == "/health":
            return self._health()
        if path.startswith("/api/admin/"):
            return self._admin(rec)
        if rec.method != "GET":
            return error(405, "Method Not Allowed")
        if path in ("/inscritos.json", "/api/public/subscribers"):
            return self._subscribers(new_format=path == "/api/public/subscribers")
        m = re.fullmatch(r"/live(\d+)\.json", path)
        if m:
            return self._live(int(m.group(1)), legacy=True)
        m = re.fullmatch(r"/api/public/lives/(\d+)", path)
        if m:
            return self._live(int(m.group(1)), legacy=False)
        return error(404, "Not Found")

    # ---- público -----------------------------------------------------------

    def _health(self) -> Reply:
        s = self.subscribers
        return json_reply(
            200,
            {
                "status": "ok",
                "server_time": legacy_ts(),
                "data_file_exists": s is not None,
                "inscritos": s["inscritos"] if s else None,
                "last_update": s["atualizado_em"] if s else None,
            },
        )

    def _subscribers(self, new_format: bool = False) -> Reply:
        if self.subscribers_status == 503 or self.subscribers is None:
            return error(503, "Dados ainda não disponíveis. Scraper inicializando...", cors())
        if new_format:  # D35: o legado + font e text_color do contador
            body = {**self.subscribers, "font": self.subs_font, "text_color": self.subs_text_color}
            return json_reply(200, body, cors())
        return json_reply(200, self.subscribers, cors())

    def _live(self, n: int, legacy: bool) -> Reply:
        if not 1 <= n <= 4:
            return error(404, "Slot inválido (1-4)", cors())
        stats = self.stats[n]
        if stats is None:
            return error(503, "Dados ainda não disponíveis", cors())
        if legacy:
            return json_reply(200, [stats], cors())
        # formato novo: o legado + goal e color do slot (D34); o legado NAO os tem
        extra = {
            "goal": self.goals[n - 1], "color": self.colors[n - 1],
            "font": self.fonts[n - 1], "text_color": self.text_colors[n - 1],
        }
        return json_reply(200, {**stats, **extra}, cors())

    # ---- admin -------------------------------------------------------------

    def _admin(self, rec: RequestRecord) -> Reply:
        path, method = rec.path, rec.method
        if path == "/api/admin/login" and method == "POST":
            return self._login(rec)

        found = self._session(rec.headers)
        if found is None:
            return error(401, "Não autenticado")
        sid, sess = found

        route = (method, path)
        if route == ("GET", "/api/admin/session"):
            return json_reply(200, self._session_payload(sess))

        if method in ("POST", "PUT", "PATCH", "DELETE"):
            token = rec.headers.get("x-csrf-token", "")
            known = route in {
                ("POST", "/api/admin/logout"),
                ("PUT", "/api/admin/slots"),
                ("PUT", "/api/admin/settings"),
                ("POST", "/api/admin/cookies"),
            }
            if known and (not token or not same(token, sess["csrf"])):
                return error(403, "CSRF token inválido ou ausente")

        if route == ("POST", "/api/admin/logout"):
            self.sessions.pop(sid, None)
            return json_reply(
                200,
                {"ok": True},
                {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"},
            )
        if route == ("GET", "/api/admin/slots"):
            return json_reply(200, self._slots_read())
        if route == ("PUT", "/api/admin/slots"):
            return self._slots_write(rec)
        if route == ("POST", "/api/admin/cookies"):
            return self._cookies(rec)
        if route == ("GET", "/api/admin/workers/status"):
            return json_reply(200, self._workers_status())
        if route == ("GET", "/api/admin/fonts"):
            return json_reply(200, {"fonts": FONTS, "default": "Roboto"})
        if route == ("GET", "/api/admin/settings"):
            return json_reply(200, self._settings())
        if route == ("PUT", "/api/admin/settings"):
            return self._settings_write(rec)
        return error(404, "Not Found")

    def _login(self, rec: RequestRecord) -> Reply:
        errs = self.contract.request_errors(self.contract.find("POST", rec.path), rec.body)  # type: ignore[arg-type]
        if errs:
            return validation_error(errs)
        user = rec.body["username"]
        ip = "127.0.0.1"
        now = time.monotonic()
        self.login_failures = [(t, k) for t, k in self.login_failures if now - t < 60]
        by_user = sum(1 for _, k in self.login_failures if k == f"u:{user.lower()}")
        by_ip = sum(1 for _, k in self.login_failures if k == f"i:{ip}")
        if by_user >= 5 or by_ip >= 5:
            return error(429, "Muitas tentativas. Tente novamente em instantes.", {"Retry-After": "60"})
        if user == self.username and same(rec.body["password"], self.password):
            sid = secrets.token_urlsafe(32)
            sess = {"csrf": secrets.token_urlsafe(32), "username": user}
            self.sessions[sid] = sess
            payload = self._session_payload(sess)
            body = {
                "ok": True,
                "username": user,
                "csrf_token": sess["csrf"],
                "expires_at": payload["expires_at"],
            }
            return json_reply(
                200,
                body,
                {"Set-Cookie": f"{SESSION_COOKIE}={sid}; Path=/; Max-Age=28800; HttpOnly; Secure; SameSite=Strict"},
            )
        self.login_failures += [(now, f"u:{user.lower()}"), (now, f"i:{ip}")]
        return error(401, "Credenciais inválidas")

    def _slots_read(self) -> dict[str, Any]:
        return {
            "slots": [
                {
                    "slot": i + 1,
                    "url": self.slots[i],
                    "goal": self.goals[i],
                    "color": self.colors[i],
                    "font": self.fonts[i],
                    "text_color": self.text_colors[i],
                    "stats": self.stats[i + 1],
                }
                for i in range(4)
            ],
            "saved_at": self.saved_at,
        }

    def _slots_write(self, rec: RequestRecord) -> Reply:
        errs = self.contract.request_errors(self.contract.find("PUT", rec.path), rec.body)  # type: ignore[arg-type]
        if errs:
            return validation_error(errs)
        items = rec.body["slots"]
        values = [str(item["url"]).strip() for item in items]
        bad = [
            {"loc": ["body", "slots", i, "url"], "msg": "Value error, URL ou ID de vídeo do YouTube inválido", "type": "value_error"}
            for i, v in enumerate(values)
            if v and extrai_video_id(v) is None
        ]
        if bad:
            return validation_error(bad)
        self.slots = values
        self.goals = [item["goal"] for item in items]
        self.colors = [str(item["color"]).upper() for item in items]  # a api normaliza em maiúsculas
        for i, item in enumerate(items):  # D35: font/text_color opcionais (ausente = mantém)
            if "font" in item:
                if item["font"] not in FONTS:
                    return validation_error([{"loc": ["body", "slots", i, "font"], "msg": "Value error, Fonte fora da lista permitida", "type": "value_error"}])
        for i, item in enumerate(items):
            if "font" in item:
                self.fonts[i] = item["font"]
            if "text_color" in item:
                self.text_colors[i] = str(item["text_color"]).upper()
        self.saved_at = utc_iso()
        saved = [
            {"url": values[i], "goal": self.goals[i], "color": self.colors[i], "font": self.fonts[i], "text_color": self.text_colors[i]}
            for i in range(4)
        ]
        return json_reply(200, {"ok": True, "slots": saved, "saved_at": self.saved_at})

    def _settings(self) -> dict[str, Any]:
        return {"channel_id": self.channel_id, "subs_font": self.subs_font, "subs_text_color": self.subs_text_color}

    def _settings_write(self, rec: RequestRecord) -> Reply:
        errs = self.contract.request_errors(self.contract.find("PUT", rec.path), rec.body)  # type: ignore[arg-type]
        if errs:
            return validation_error(errs)
        body = rec.body
        if "subs_font" in body and body["subs_font"] not in FONTS:
            return validation_error([{"loc": ["body", "subs_font"], "msg": "Value error, Fonte fora da lista permitida", "type": "value_error"}])
        if "channel_id" in body:
            self.channel_id = body["channel_id"]
        if "subs_font" in body:
            self.subs_font = body["subs_font"]
        if "subs_text_color" in body:
            self.subs_text_color = str(body["subs_text_color"]).upper()
        return json_reply(200, {"ok": True, **self._settings()})

    def _cookies(self, rec: RequestRecord) -> Reply:
        # Ordem de validação do openapi.yaml (descrição de POST /api/admin/cookies).
        try:
            data = json.loads(rec.raw_body.decode("utf-8"))
        except ValueError:
            return error(400, "JSON inválido no body da requisição")
        if not isinstance(data, list) or not data:
            return error(400, "Esperado um array JSON com pelo menos 1 cookie")
        for i, c in enumerate(data):
            if not isinstance(c, dict) or "name" not in c or "value" not in c:
                return error(400, f"Cookie #{i} sem campo 'name' ou 'value'")
        errs = self.contract.request_errors(self.contract.find("POST", rec.path), data)  # type: ignore[arg-type]
        if errs:
            return validation_error(errs)
        n = len(data)
        self.cookies = {"configured": True, "count": n, "uploaded_at": utc_iso(), "applied": False}
        return json_reply(
            200,
            {
                "ok": True,
                "message": f"{n} cookies salvos com sucesso! O scraper vai recarregar automaticamente em alguns segundos.",
                "cookies_count": n,
            },
        )

    def _workers_status(self) -> dict[str, Any]:
        return {
            "server_time": utc_iso(),
            "workers": {"lives": self.lives_worker, "inscritos": self.inscritos_worker},
            "cookies": self.cookies,
            "subscribers": self.subscribers,
        }


def same(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def cors() -> dict[str, str]:
    return {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "GET"}


# ---------------------------------------------------------------------------
# Servidor HTTP (estáticos + api)
# ---------------------------------------------------------------------------

def security_headers(csp: str, overlay: bool) -> dict[str, str]:
    h = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": PERMISSIONS_POLICY,
        "Cross-Origin-Opener-Policy": "same-origin",
    }
    if not overlay:
        h["X-Frame-Options"] = "DENY"
    return h


class StackServer:
    """Sobe o dublê em 127.0.0.1:<porta livre> numa thread."""

    def __init__(self, api: ApiDouble) -> None:
        self.api = api
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # silencia stderr
                pass

            def _serve(self) -> None:
                parts = urlsplit(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                body = None
                if raw:
                    try:
                        body = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        body = None
                rec = RequestRecord(
                    self.command,
                    parts.path,
                    parts.query,
                    {k.lower(): v for k, v in self.headers.items()},
                    body,
                    raw,
                )
                reply = outer.respond(rec)
                self.send_response(reply.status)
                for k, v in reply.headers.items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(reply.body)))
                self.end_headers()
                self.wfile.write(reply.body)

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _serve

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)

    # ---- roteamento --------------------------------------------------------

    def respond(self, rec: RequestRecord) -> Reply:
        path = rec.path
        if rec.method == "GET":
            static = self.static(path)
            if static is not None:
                if path in HTML_PAGES or path in OVERLAY_PAGES:
                    with self.api.lock:
                        static = self.api.validated(rec, static)
                return static
        if path == "/__legacy__/inscritos.json":
            rec.path = "/inscritos.json"
        reply = self.api.handle(rec)
        reply.headers.update(security_headers(CSP, overlay=False))
        return reply

    def static(self, path: str) -> Reply | None:
        # Página legada (golden file) para comparação visual: roda SEM a CSP
        # estrita (o original usa script/estilo inline) e só existe neste dublê.
        if path == "/__legacy__/contador.html":
            return Reply(200, (LEGACY_DIR / "contador.original.html").read_bytes(), {"Content-Type": MIME[".html"]})
        csp, overlay = CSP, False
        if path in HTML_PAGES:
            file = STATIC / "admin" / "index.html"
        elif path in OVERLAY_PAGES:
            file, csp, overlay = STATIC / "overlay" / "contador.html", CSP_OVERLAY, True
        elif path.startswith("/admin-assets/"):
            file = self._inside(STATIC / "admin", path[len("/admin-assets/"):])
        elif path.startswith("/overlay/"):
            file, csp, overlay = self._inside(STATIC / "overlay", path[len("/overlay/"):]), CSP_OVERLAY, True
        else:
            return None
        if not file.is_file():
            reply = error(404, "Not Found")
            reply.headers.update(security_headers(csp, overlay))
            return reply
        headers = {"Content-Type": MIME.get(file.suffix, "application/octet-stream"), "Cache-Control": "no-store"}
        headers.update(security_headers(csp, overlay))
        return Reply(200, file.read_bytes(), headers)

    @staticmethod
    def _inside(root: Path, rel: str) -> Path:
        target = (root / rel).resolve()
        if root.resolve() not in target.parents:
            return root / "__inexistente__"
        return target
