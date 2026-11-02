"""Upstream minimo de teste que responde em api:8000 (fronteira HTTP do Nginx, mesma logica da D26).

NAO faz parte de nenhuma imagem de producao: so e usado pelos testes de tests/infra, rodando num container
`python:3.12-slim` no lugar da api. Ele nao implementa a api; apenas ecoa o que o Nginx lhe enviou, para os
testes verificarem o que atravessa a fronteira (cabecalhos de IP real, caminho, metodo) e simula respostas da
api controladas por cabecalhos da requisicao:

  X-Fake-Status: <codigo>   responde com esse status (padrao 200)
  X-Fake-Cors: 1            a "api" tenta emitir CORS/CSP/XFO por conta propria (o Nginx tem que descarta-los)

Rota de inspecao (so alcancavel por dentro do container, nunca pelo Nginx):
  GET /__requests           JSON com todas as requisicoes recebidas desde o inicio
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LOG = []


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "fake-api"
    sys_version = ""

    def log_message(self, fmt, *args):
        return

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        if self.path == "/__requests" and self.client_address[0] in ("127.0.0.1", "::1"):
            payload = json.dumps(LOG).encode()
            self._send(200, payload, {})
            return
        headers = {k.lower(): v for k, v in self.headers.items()}
        entry = {
            "method": self.command,
            "path": self.path,
            "headers": headers,
            "client": self.client_address[0],
            "body_len": len(body),
        }
        LOG.append(entry)
        status = int(headers.get("x-fake-status", "200"))
        extra = {"Cache-Control": "no-store"}
        if headers.get("x-fake-cors") == "1":
            extra.update(
                {
                    "Access-Control-Allow-Origin": "https://evil.example",
                    "Access-Control-Allow-Credentials": "true",
                    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
                    "Content-Security-Policy": "default-src *",
                    "X-Frame-Options": "SAMEORIGIN",
                    "X-Powered-By": "fake",
                }
            )
        self._send(status, json.dumps(entry).encode(), extra)

    def _send(self, status, payload, extra):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for key, value in extra.items():
            self.send_header(key, value)
        if status >= 200 and status not in (204, 304):
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        else:
            self.end_headers()


for _method in ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"):
    setattr(Handler, "do_" + _method, Handler._handle)

if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()
