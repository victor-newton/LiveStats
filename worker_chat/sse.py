"""Servidor SSE minimo (D57): `GET /eventos/<set_id>` na porta interna 8090 (so o Nginx fala com ele).

HTTP/1.1 escrito a mao sobre `asyncio.start_server`, de proposito pequeno: aceita so `GET /eventos/<id>`, le o cabecalho
com limite de tamanho e de tempo, ignora o resto e responde com `Connection: close` (corpo delimitado pelo fechamento).

Respostas: 200 `text/event-stream` (eventos `msg`, `del`, `clear`, `config` e o comentario `: ping`); 404 se o conjunto nao
existe, esta com o chat desligado ou sem lives no chat; 503 acima de `MAX_CHAT_CLIENTS`; 405 para outro metodo.
Nada do que passa aqui e registrado em log (so contagens, no gerente).
"""

from __future__ import annotations

import asyncio
import re

from worker_chat.hub import Hub

PING_INTERVAL = 15.0
HEADER_TIMEOUT = 10.0
WRITE_TIMEOUT = 10.0
MAX_HEADER_BYTES = 16 * 1024
RETRY_MS = 5000
_PATH_RE = re.compile(r"/eventos/([1-9][0-9]{0,9})")
_REQUEST_LINE_RE = re.compile(r"([A-Z]{3,8}) (\S{1,2048}) HTTP/1\.[01]")

_REASONS = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    405: "Method Not Allowed",
    431: "Request Header Fields Too Large",
    503: "Service Unavailable",
}


def _response(status: int, extra: tuple[tuple[str, str], ...] = (), body: str = "") -> bytes:
    payload = body.encode()
    head = [f"HTTP/1.1 {status} {_REASONS[status]}", "Cache-Control: no-store", "Connection: close"]
    head += [f"{k}: {v}" for k, v in extra]
    if body:
        head += ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
    else:
        head += ["Content-Length: 0"]
    return ("\r\n".join(head) + "\r\n\r\n").encode() + payload


_STREAM_HEAD = (
    "HTTP/1.1 200 OK\r\n"
    "Content-Type: text/event-stream; charset=utf-8\r\n"
    "Cache-Control: no-store\r\n"
    "X-Accel-Buffering: no\r\n"
    "Connection: close\r\n\r\n"
).encode()


class SseServer:
    def __init__(self, hub: Hub, ping_interval: float = PING_INTERVAL) -> None:
        self._hub = hub
        self._ping = ping_interval
        self._server: asyncio.Server | None = None

    async def start(self, host: str, port: int) -> int:
        """Comeca a ouvir; devolve a porta (util com `port=0`)."""
        self._server = await asyncio.start_server(self._handle, host, port, limit=MAX_HEADER_BYTES)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            self._hub.close_all()  # sem isso o `wait_closed` esperaria os clientes SSE abertos para sempre
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await self._serve(reader, writer)
        except (ConnectionError, TimeoutError, asyncio.IncompleteReadError):
            pass  # cliente foi embora: o normal de um SSE
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, TimeoutError):
                pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEADER_TIMEOUT)
        except asyncio.LimitOverrunError:
            await self._send(writer, _response(431, body='{"detail":"Request Header Fields Too Large"}'))
            return
        request_line = head.split(b"\r\n", 1)[0].decode("latin-1")
        found = _REQUEST_LINE_RE.fullmatch(request_line)
        if found is None:
            await self._send(writer, _response(400, body='{"detail":"Bad Request"}'))
            return
        method, target = found.groups()
        if method != "GET":
            await self._send(writer, _response(405, (("Allow", "GET"),), '{"detail":"Method Not Allowed"}'))
            return
        match = _PATH_RE.fullmatch(target.partition("?")[0])
        if match is None or not self._hub.servable(int(match.group(1))):
            await self._send(writer, _response(404, body='{"detail":"Not Found"}'))
            return
        if self._hub.at_capacity():
            await self._send(writer, _response(503, (("Retry-After", "10"),), '{"detail":"Service Unavailable"}'))
            return
        await self._stream(reader, writer, int(match.group(1)))

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, data: bytes) -> None:
        writer.write(data)
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT)

    async def _stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, set_id: int) -> None:
        sub = self._hub.subscribe(set_id)
        getter: asyncio.Future[bytes | None] = asyncio.ensure_future(sub.queue.get())
        eof: asyncio.Future[bytes] = asyncio.ensure_future(reader.read(1024))  # fim da conexao do cliente
        try:
            await self._send(writer, _STREAM_HEAD + f"retry: {RETRY_MS}\n\n".encode())
            while True:
                done, _ = await asyncio.wait({getter, eof}, timeout=self._ping, return_when=asyncio.FIRST_COMPLETED)
                if eof in done:
                    if eof.result() == b"":
                        return
                    eof = asyncio.ensure_future(reader.read(1024))  # bytes extras do cliente: ignorados
                if getter in done:
                    item = getter.result()
                    if item is None:
                        return
                    await self._send(writer, item)
                    getter = asyncio.ensure_future(sub.queue.get())
                elif not done:
                    await self._send(writer, b": ping\n\n")
        finally:
            for task in (getter, eof):
                task.cancel()
            await asyncio.gather(getter, eof, return_exceptions=True)
            self._hub.unsubscribe(sub)

