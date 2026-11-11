"""Twitch: IRC anonimo por WebSocket (D57).

Um unico socket em `wss://irc-ws.chat.twitch.tv:443`, `NICK justinfan<aleatorio>` (leitura anonima, sem senha),
`CAP REQ :twitch.tv/tags twitch.tv/commands` e `JOIN` de todos os canais pedidos. Trata `PRIVMSG` (tags `id`,
`display-name`, `color`, `badges`, `emotes`), `CLEARMSG`, `CLEARCHAT`, `PING` e `RECONNECT`. Nao ha historico: a Twitch
nao reenvia mensagens antigas ao entrar no canal.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
from collections import deque
from dataclasses import dataclass

import websockets
from websockets.asyncio.client import connect

from worker_chat.backoff import STABLE_SECONDS, Backoff
from worker_chat.clock import Clock
from worker_chat.model import (
    PLATFORM_CODES,
    ChatMessage,
    ChatSink,
    PartsBuilder,
    clean_name,
    normalize_color,
    normalize_uid,
    ordered_roles,
    valid_message_id,
)
from worker_chat.platforms import ConnectorStatus

log = logging.getLogger("worker_chat.twitch")

PLATFORM = "twitch"
CODE = PLATFORM_CODES[PLATFORM]
CHANNEL_RE = re.compile(r"[a-z0-9_]{1,25}")
OPEN_TIMEOUT = 15.0
MAX_FRAME = 256 * 1024
JOIN_BURST = 15  # a Twitch limita os JOIN (20 por 10 s): ficamos abaixo
JOIN_WINDOW = 10.0
RECONNECT_PAUSE = 1.0  # pedido de RECONNECT do servidor nao e falha: so uma pausa curta

_BADGE_ROLES = {
    "broadcaster": "owner",
    "moderator": "mod",
    "vip": "vip",
    "subscriber": "sub",
    "founder": "sub",
    "partner": "verified",
}
_TAG_ESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}


@dataclass(frozen=True)
class IrcLine:
    tags: dict[str, str]
    prefix: str | None
    command: str
    params: tuple[str, ...]
    trailing: str | None


def unescape_tag(value: str) -> str:
    out: list[str] = []
    it = iter(value)
    for ch in it:
        if ch != "\\":
            out.append(ch)
            continue
        nxt = next(it, None)
        if nxt is not None:
            out.append(_TAG_ESCAPES.get(nxt, nxt))
    return "".join(out)


def parse_line(raw: str) -> IrcLine | None:
    """`@tags :prefixo COMANDO params... :final`; `None` para linha vazia ou sem comando."""
    rest = raw.strip("\r\n ")
    if not rest:
        return None
    tags: dict[str, str] = {}
    if rest.startswith("@"):
        raw_tags, _, rest = rest[1:].partition(" ")
        for item in raw_tags.split(";"):
            key, _, value = item.partition("=")
            if key:
                tags[key] = unescape_tag(value)
        rest = rest.lstrip(" ")
    prefix = None
    if rest.startswith(":"):
        prefix, _, rest = rest[1:].partition(" ")
        rest = rest.lstrip(" ")
    trailing = None
    if rest.startswith(":"):
        return None  # sem comando
    head, sep, tail = rest.partition(" :")
    if sep:
        trailing = tail
    words = head.split()
    if not words:
        return None
    return IrcLine(tags, prefix, words[0].upper(), tuple(words[1:]), trailing)


def parse_frame(frame: str) -> list[IrcLine]:
    """Um quadro WebSocket pode trazer varias linhas IRC separadas por CRLF."""
    return [line for line in (parse_line(part) for part in frame.split("\n")) if line is not None]


# --------------------------------------------------------------------------------------------------- eventos


@dataclass(frozen=True)
class Chat:
    ref: str
    msg: ChatMessage


@dataclass(frozen=True)
class Deleted:
    ref: str
    ids: tuple[str, ...]


@dataclass(frozen=True)
class Cleared:
    ref: str
    user: str | None


@dataclass(frozen=True)
class Ping:
    token: str


@dataclass(frozen=True)
class Welcome:
    pass


@dataclass(frozen=True)
class Reconnect:
    pass


TwitchEvent = Chat | Deleted | Cleared | Ping | Welcome | Reconnect


def _channel(line: IrcLine) -> str | None:
    if not line.params or not line.params[0].startswith("#"):
        return None
    ref = line.params[0][1:].lower()
    return ref if CHANNEL_RE.fullmatch(ref) else None


def _roles(badges: str, mod_flag: str) -> tuple[str, ...]:
    roles: set[str] = set()
    for item in badges.split(","):
        name = item.partition("/")[0]
        if name in _BADGE_ROLES:
            roles.add(_BADGE_ROLES[name])
    if mod_flag == "1":
        roles.add("mod")
    return ordered_roles(roles)


def _emote_ranges(tag: str, length: int) -> list[tuple[int, int, str]]:
    """`25:0-4,12-16/1902:6-10` -> [(inicio, fim exclusivo, id)] validos, em ordem e sem sobreposicao."""
    found: list[tuple[int, int, str]] = []
    for group in tag.split("/"):
        emote_id, sep, spans = group.partition(":")
        if not sep:
            continue
        for span in spans.split(","):
            a, dash, b = span.partition("-")
            if not (dash and a.isascii() and a.isdigit() and b.isascii() and b.isdigit()):
                continue
            start, end = int(a), int(b)
            if 0 <= start <= end < length:
                found.append((start, end + 1, emote_id))
    found.sort()
    out: list[tuple[int, int, str]] = []
    for item in found:
        if not out or item[0] >= out[-1][1]:
            out.append(item)
    return out


def normalize_privmsg(line: IrcLine) -> Chat | None:
    ref = _channel(line)
    msg_id = line.tags.get("id", "")
    if ref is None or line.trailing is None or not valid_message_id(msg_id):
        return None
    login = (line.prefix or "").partition("!")[0].lower()
    name = clean_name(line.tags.get("display-name", "")) or clean_name(login)
    if not name:
        return None
    text = line.trailing
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):  # /me
        text = text[len("\x01ACTION ") : -1]
    builder = PartsBuilder()
    pos = 0
    for start, end, emote_id in _emote_ranges(line.tags.get("emotes", ""), len(text)):
        builder.text(text[pos:start])
        builder.emote(emote_id, text[start:end])
        pos = end
    builder.text(text[pos:])
    parts = builder.build()
    if not parts:
        return None
    msg = ChatMessage(
        id=f"{CODE}:{msg_id}",
        p=CODE,
        name=name,
        color=normalize_color(line.tags.get("color")),
        roles=_roles(line.tags.get("badges", ""), line.tags.get("mod", "")),
        parts=parts,
        login=login,
        uid=normalize_uid(login),
    )
    return Chat(ref, msg)


def interpret(line: IrcLine) -> TwitchEvent | None:
    """Traduz uma linha IRC; o que nao interessa (JOIN, ROOMSTATE, USERNOTICE...) vira `None`."""
    cmd = line.command
    if cmd == "PRIVMSG":
        return normalize_privmsg(line)
    if cmd == "PING":
        return Ping(line.trailing if line.trailing is not None else "tmi.twitch.tv")
    if cmd == "001":
        return Welcome()
    if cmd == "RECONNECT":
        return Reconnect()
    ref = _channel(line)
    if cmd == "CLEARMSG" and ref is not None:
        target = line.tags.get("target-msg-id", "")
        return Deleted(ref, (f"{CODE}:{target}",)) if valid_message_id(target) else None
    if cmd == "CLEARCHAT" and ref is not None:
        if not (line.trailing or "").strip():
            return Cleared(ref, None)  # sem usuario: limpa o chat todo
        user = normalize_uid(line.trailing)
        return Cleared(ref, user) if user else None  # usuario fora do padrao: ignora (nunca vira "limpar tudo")
    return None


# -------------------------------------------------------------------------------------------------- conector


class TwitchConnector:
    platform = PLATFORM

    def __init__(self, url: str, sink: ChatSink, clock: Clock) -> None:
        self._url = url
        self._sink = sink
        self._clock = clock
        self._wanted: frozenset[str] = frozenset()
        self._invalid = 0
        self._joined: set[str] = set()
        self._changed = asyncio.Event()
        self._connected = False
        self._failing = False
        self._last_error: str | None = None
        self._connected_at: float | None = None

    # ----------------------------------------------------------------------------------------- interface
    def want(self, refs: frozenset[str]) -> None:
        valid = frozenset(r for r in refs if CHANNEL_RE.fullmatch(r))
        self._invalid = len(refs) - len(valid)
        if valid != self._wanted:
            self._wanted = valid
            self._changed.set()

    def status(self) -> ConnectorStatus:
        wanted = len(self._wanted)
        joined = len(self._joined & self._wanted) if self._connected else 0
        failures = (1 if self._failing else 0) + self._invalid
        return ConnectorStatus(wanted=wanted, connected=joined, failures=failures, last_error=self._last_error)

    async def run(self) -> None:
        backoff = Backoff()
        while True:
            while not self._wanted:  # ocioso: sem canal pedido nao ha conexao
                self._changed.clear()
                if not self._wanted:
                    await self._changed.wait()
            error: str | None = None
            try:
                await self._session()
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                error = "network_error"
                log.warning("Twitch: queda da conexao [%s] (%s)", error, type(exc).__name__)
            finally:
                self._connected = False
                self._joined.clear()
            uptime = self._clock.monotonic() - self._connected_at if self._connected_at is not None else 0.0
            self._connected_at = None
            if uptime >= STABLE_SECONDS:
                backoff.reset()
            if error is None:
                self._failing = False
                await self._clock.sleep(RECONNECT_PAUSE)
            else:
                self._failing = True
                self._last_error = error
                await self._clock.sleep(backoff.next())

    # ----------------------------------------------------------------------------------------- sessao
    async def _session(self) -> bool:
        """Uma conexao. `True` se o servidor pediu RECONNECT; `False` se nao ha mais canal pedido (ocioso). A queda
        (fechamento, erro de rede, handshake recusado) sai como excecao."""
        async with connect(self._url, open_timeout=OPEN_TIMEOUT, max_size=MAX_FRAME) as ws:
            await ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands")
            await ws.send(f"NICK justinfan{secrets.randbelow(10**8)}")
            welcomed = asyncio.Event()
            reader = asyncio.create_task(self._read(ws, welcomed))
            syncer = asyncio.create_task(self._sync(ws, welcomed))
            try:
                await asyncio.wait({reader, syncer}, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in (reader, syncer):
                    task.cancel()
                await asyncio.gather(reader, syncer, return_exceptions=True)
            for task in (reader, syncer):
                if task.done() and not task.cancelled() and task.exception() is not None:
                    raise task.exception()
            if reader.done() and not reader.cancelled():
                if reader.result():
                    return True
                raise ConnectionResetError("conexao fechada pelo servidor")
            return False

    async def _read(self, ws, welcomed: asyncio.Event) -> bool:
        """Le ate o servidor pedir RECONNECT (`True`) ou fechar a conexao (`False`)."""
        async for frame in ws:
            if not isinstance(frame, str):
                continue
            for line in parse_frame(frame):
                event = interpret(line)
                if isinstance(event, Welcome):
                    self._connected = True
                    self._failing = False
                    self._connected_at = self._clock.monotonic()
                    welcomed.set()
                elif isinstance(event, Ping):
                    await ws.send(f"PONG :{event.token}")
                elif isinstance(event, Reconnect):
                    return True
                elif isinstance(event, Chat):
                    if event.ref in self._wanted:
                        self._sink.message(PLATFORM, event.ref, event.msg)
                elif isinstance(event, Deleted):
                    if event.ref in self._wanted:
                        self._sink.delete(PLATFORM, event.ref, event.ids)
                elif isinstance(event, Cleared) and event.ref in self._wanted:
                    self._sink.clear(PLATFORM, event.ref, event.user)
        return False

    async def _sync(self, ws, welcomed: asyncio.Event) -> None:
        """Mantem os canais entrados iguais aos pedidos (JOIN/PART); sem canal pedido, encerra a sessao."""
        await welcomed.wait()
        recent: deque[float] = deque()
        while True:
            self._changed.clear()
            wanted = self._wanted
            if not wanted:
                return
            for ref in sorted(self._joined - wanted):
                await ws.send(f"PART #{ref}")
                self._joined.discard(ref)
            for ref in sorted(wanted - self._joined):
                now = self._clock.monotonic()
                while recent and now - recent[0] >= JOIN_WINDOW:
                    recent.popleft()
                if len(recent) >= JOIN_BURST:
                    await self._clock.sleep(JOIN_WINDOW - (now - recent[0]))
                    now = self._clock.monotonic()
                    while recent and now - recent[0] >= JOIN_WINDOW:
                        recent.popleft()
                await ws.send(f"JOIN #{ref}")
                recent.append(now)
                self._joined.add(ref)
            await self._changed.wait()
