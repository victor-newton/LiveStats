# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""Proxy HTTPS de TESTE (o "Nginx Proxy Manager" do SEC-25): termina TLS com um certificado de teste e repassa ao web:8081.

Uso (dentro de um container na rede edge): python tls_proxy.py <porta> <host_upstream> <porta_upstream> <cert.pem> <key.pem>
Acrescenta X-Forwarded-For com o IP do cliente, como o NPM. So existe em tests/security.
"""

from __future__ import annotations

import http.client
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LISTEN_PORT, UP_HOST, UP_PORT, CERT, KEY = int(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5]
SKIP_REQUEST = {"host", "connection", "x-forwarded-for", "content-length"}
SKIP_RESPONSE = {"transfer-encoding", "connection", "content-length", "server", "date"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:
        return

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in SKIP_REQUEST}
        headers["X-Forwarded-For"] = self.client_address[0]
        conn = http.client.HTTPConnection(UP_HOST, UP_PORT, timeout=30)
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


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", LISTEN_PORT), Handler)  # noqa: S104 - container de teste
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(CERT, KEY)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
