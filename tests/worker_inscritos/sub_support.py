"""Apoio dos testes do worker-inscritos: servidor de fixture local, banco SQLite real, cookies cifrados.

Fronteira externa simulada: SOMENTE o YouTube (www, Studio e login), por um servidor HTTP local em
127.0.0.1 que serve as paginas de `fixtures/`. Chromium, Playwright, SQLite e PyNaCl sao reais.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from nacl.public import PrivateKey, PublicKey, SealedBox

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"

PLACEHOLDER_CHANNEL = "UCxxxxxxxxxxxxxxxxxxxxxx"

LEGACY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


class FixtureServer:
    """Simula www, Studio e login do YouTube. Paginas vem de `fixtures/` (ou de `html_override`)."""

    def __init__(self) -> None:
        self.page_name = "variante_a.html"
        self.html_override: str | None = None
        self.require_cookie: tuple[str, str] | None = None
        self.login_path = "/signin/v2/identifier?service=youtube"
        self.requests: list[dict[str, str]] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silencia o stderr do pytest
                pass

            def _send(self, status: int, body: str, headers: dict[str, str] | None = None) -> None:
                dados = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(dados)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(dados)

            def do_GET(self):
                with server._lock:
                    server.requests.append(
                        {
                            "path": self.path,
                            "cookie": self.headers.get("Cookie", ""),
                            "user_agent": self.headers.get("User-Agent", ""),
                        }
                    )
                    page_name, override = server.page_name, server.html_override
                    required, login_path = server.require_cookie, server.login_path
                if self.path.startswith("/www"):
                    self._send(200, "<!DOCTYPE html><html><body>www</body></html>")
                elif self.path.startswith("/channel/"):
                    if required is not None and f"{required[0]}={required[1]}" not in parse_cookie_header(
                        self.headers.get("Cookie", "")
                    ):
                        self._send(302, "", {"Location": login_path})
                        return
                    html = override if override is not None else (FIXTURES / page_name).read_text("utf-8")
                    self._send(200, html)
                elif self.path.startswith(("/signin", "/gate")):
                    self._send(200, (FIXTURES / "login.html").read_text("utf-8"))
                else:
                    self._send(404, "nao encontrado")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def www_base(self) -> str:
        return f"http://127.0.0.1:{self.port}/www"

    @property
    def studio_base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def studio_requests(self) -> list[dict[str, str]]:
        with self._lock:
            return [r for r in self.requests if r["path"].startswith("/channel/")]

    def studio_hits(self) -> int:
        return len(self.studio_requests())

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def parse_cookie_header(header: str) -> set[str]:
    return {parte.strip() for parte in header.split(";") if parte.strip()}


# ---------------------------------------------------------------------- banco e cookies
def create_db(path: Path) -> Path:
    """Cria o banco com o esquema oficial (db/schema.sql), como faria a api."""
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    finally:
        con.close()
    return path


def query(db_path: Path, sql: str, params: tuple = ()) -> list[tuple]:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(sql, params).fetchall()
    finally:
        con.close()


def write_key_file(path: Path, private_key: PrivateKey) -> Path:
    path.write_text(base64.b64encode(bytes(private_key)).decode("ascii") + "\n", encoding="ascii")
    return path


def public_key_id(public_key: PublicKey) -> str:
    return hashlib.sha256(bytes(public_key)).hexdigest()[:16]


def now_z() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat() + "Z"


def insert_cookie_set(
    db_path: Path,
    public_key: PublicKey,
    cookies: list[dict],
    *,
    key_id: str | None = None,
    ciphertext: bytes | None = None,
) -> int:
    """Faz o papel da api: cifra com SealedBox(chave publica), insere e apaga os conjuntos anteriores."""
    if ciphertext is None:
        corpo = json.dumps(cookies, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ciphertext = SealedBox(public_key).encrypt(corpo)
    con = sqlite3.connect(db_path, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        cur = con.execute(
            "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at) "
            "VALUES (?, ?, ?, 'admin', ?)",
            (ciphertext, key_id or public_key_id(public_key), max(1, min(len(cookies), 200)), now_z()),
        )
        novo = int(cur.lastrowid)
        con.execute("DELETE FROM cookie_sets WHERE id <> ?", (novo,))
        con.execute("COMMIT")
        return novo
    finally:
        con.close()


def cookie(name: str, value: str, **extra) -> dict:
    """Cookie no formato do EditThisCookie, valido para o servidor de fixture (127.0.0.1)."""
    base = {"name": name, "value": value, "domain": "127.0.0.1", "path": "/", "secure": False}
    base.update(extra)
    return base


def dump_text(db_path: Path) -> str:
    """Todo o conteudo textual do banco (colunas TEXT e BLOB como texto), para procurar vazamentos."""
    con = sqlite3.connect(db_path)
    try:
        partes: list[str] = []
        for (tabela,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            if tabela == "cookie_sets":
                linhas = con.execute("SELECT id, key_id, cookie_count, uploaded_by, created_at FROM cookie_sets")
            else:
                linhas = con.execute(f'SELECT * FROM "{tabela}"')  # noqa: S608 - nomes vindos do sqlite_master
            partes.extend(repr(linha) for linha in linhas.fetchall())
        return "\n".join(partes)
    finally:
        con.close()
