# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018, S104
"""Proxy HTTP de TESTE que faz o papel do NPM (8081, X-Forwarded-For) ou do cloudflared (8080, CF-Connecting-IP).

Usado so por INT-08/INT-09: a suite tests/web (funcional, ~90 testes, ~15 requisicoes de estaticos por pagina) roda toda
a partir de UM IP e esbarra no `limit_req` de verdade do Nginx. O proxy entra pelo mecanismo documentado (IP fixo
confiavel na rede edge) e informa um "IP de visitante" diferente a cada requisicao, como seriam varios usuarios reais.
O limite em si e verificado por INT-12 / SEC-02 / SEC-05 / SEC-24, nao por esta suite.

Uso: python ip_proxy.py <porta> <host_upstream> <porta_upstream> <nome_do_cabecalho>
"""

from __future__ import annotations

import http.client
import random
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_PORT, UP_HOST, UP_PORT, HEADER = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
SKIP_REQUEST = {"host", "connection", "content-length", "x-forwarded-for", "cf-connecting-ip"}
SKIP_RESPONSE = {"transfer-encoding", "connection", "content-length", "server", "date"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        return

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_REQUEST}
        headers[HEADER] = f"10.{random.randrange(256)}.{random.randrange(256)}.{random.randrange(1, 255)}"
        conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=60)
        conn.request(self.command, self.path, body, headers)
        resp = conn.getresponse()
        data = resp.read()
        self.send_response(resp.status)
        for key, value in resp.getheaders():
            if key.lower() not in SKIP_RESPONSE:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)
        conn.close()

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = _forward


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler).serve_forever()
