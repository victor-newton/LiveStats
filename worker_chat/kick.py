"""Kick: sala por HTTP (ou navegador) + chat em tempo real por **Centrifugo** em WebSocket (D57).

Protocolo que o site do Kick usa de fato (confirmado em 2026-11-10; o Pusher foi aposentado): o cliente abre
`wss://realtime.<regiao>.platform.kick.com/connection/websocket`, manda `{"connect": {"token", "name": "js"}, "id": 1}` e depois
`{"subscribe": {"channel": "chatrooms.<id>.v2"}, "id": n}` por sala. O servidor responde `{"id": n, ...}`, manda `{}` a cada
`ping` segundos (o cliente devolve `{}`) e entrega `{"push": {"channel": ..., "pub": {"data": {"event": ..., "data": "<json>"}}}}`.
Os eventos de dentro sao os mesmos de sempre: `App\\Events\\ChatMessageEvent` (emotes no texto como `[emote:<id>:<nome>]`),
`MessageDeletedEvent`, `UserBannedEvent` e `ChatroomClearEvent`. O token (JWT de visitante, ~45 min) e renovado na propria
conexao (`refresh`). Quadros trazem varias linhas JSON. Nao ha historico: so o que acontece depois da inscricao.
Credenciais e id da sala vem de `kick_site.KickSite` (descobertos pelo site; nada fixo no codigo).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass

import websockets
from websockets.asyncio.client import connect

from worker_chat.backoff import BACKOFF_MAX, BACKOFF_START, STABLE_SECONDS, Backoff
from worker_chat.clock import Clock
from worker_chat.kick_site import SLUG_RE, KickSite, ResolveError
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

log = logging.getLogger("worker_chat.kick")

PLATFORM = "kick"
CODE = PLATFORM_CODES[PLATFORM]
_CHANNEL_RE = re.compile(r"chatrooms\.([0-9]{1,12})\.v2")
_EMOTE_RE = re.compile(r"\[emote:([^:\]\s]{1,64}):([^\]]{0,64})\]")
OPEN_TIMEOUT = 15.0
MAX_FRAME = 256 * 1024
DEFAULT_PING = 25.0  # segundos entre os `{}` do servidor (o `connect` informa o valor real)
PING_GRACE = 10.0  # folga (no maximo isto, e nunca mais que o proprio `ping`) antes de dar a conexao por morta
RECONNECT_PAUSE = 1.0
REFRESH_BEFORE = 120.0  # renova o token tantos segundos antes de a conexao expirar
REFRESH_STEP = 30.0  # passo de espera do renovador (acompanha o relogio, sem um sleep gigante)

EV_MESSAGE = "App\\Events\\ChatMessageEvent"
EV_DELETED = "App\\Events\\MessageDeletedEvent"
EV_BANNED = "App\\Events\\UserBannedEvent"
EV_CLEAR = "App\\Events\\ChatroomClearEvent"

_BADGE_ROLES = {
    "broadcaster": "owner",
    "moderator": "mod",
    "vip": "vip",
    "subscriber": "sub",
    "founder": "sub",
    "verified": "verified",
}


# ----------------------------------------------------------------------------------------------- eventos


@dataclass(frozen=True)
class RoomMessage:
    room_id: int
    msg: ChatMessage


@dataclass(frozen=True)
class RoomDeleted:
    room_id: int
    ids: tuple[str, ...]


@dataclass(frozen=True)
class RoomCleared:
    room_id: int
    user: str | None


@dataclass(frozen=True)
class Connected:
    ping: float
    ttl: float | None  # segundos ate o token expirar (None: o token nao expira)


@dataclass(frozen=True)
class Subscribed:
    cmd_id: int


@dataclass(frozen=True)
class Refreshed:
    ttl: float | None


@dataclass(frozen=True)
class ServerPing:
    pass


@dataclass(frozen=True)
class CommandError:
    cmd_id: int
    code: int


@dataclass(frozen=True)
class Invalid:
    """Linha que nao e JSON valido (contada; nada do conteudo e guardado ou logado)."""


KickEvent = RoomMessage | RoomDeleted | RoomCleared | Connected | Subscribed | Refreshed | ServerPing | CommandError | Invalid


def _room_of(channel: object) -> int | None:
    found = _CHANNEL_RE.fullmatch(channel) if isinstance(channel, str) else None
    return int(found.group(1)) if found else None


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _number(value: object, minimo: float, maximo: float) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and minimo <= value <= maximo
    return float(value) if ok else None  # type: ignore[arg-type]


def normalize_message(data: dict) -> ChatMessage | None:
    kind = data.get("type")
    msg_id = data.get("id")
    content = data.get("content")
    sender = _dict(data.get("sender"))
    username = sender.get("username")
    if kind not in ("message", "reply") or not valid_message_id(msg_id) or not isinstance(content, str):
        return None
    if not isinstance(username, str):
        return None
    name = clean_name(username)
    if not name:
        return None
    identity = _dict(sender.get("identity"))
    roles: set[str] = set()
    badges = identity.get("badges")
    for badge in badges if isinstance(badges, list) else []:
        role = _BADGE_ROLES.get(str(_dict(badge).get("type")))
        if role:
            roles.add(role)
    builder = PartsBuilder()
    pos = 0
    for found in _EMOTE_RE.finditer(content):
        builder.text(content[pos : found.start()])
        builder.emote(found.group(1), found.group(2))
        pos = found.end()
    builder.text(content[pos:])
    parts = builder.build()
    if not parts:
        return None
    slug = sender.get("slug")
    login = (slug if isinstance(slug, str) and slug else username).lower()
    return ChatMessage(
        id=f"{CODE}:{msg_id}",
        p=CODE,
        name=name,
        color=normalize_color(identity.get("color")),
        roles=ordered_roles(roles),
        parts=parts,
        login=login,
        uid=normalize_uid(login),
    )


def translate(channel: object, event: object, raw: object) -> RoomMessage | RoomDeleted | RoomCleared | Invalid | None:
    """Traduz um evento do chat (o mesmo formato de antes: nome do evento + dados em JSON); o que nao interessa vira `None`."""
    room = _room_of(channel)
    if room is None or not isinstance(event, str):
        return None
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError:
            return Invalid()
    else:
        data = raw
    data = _dict(data)
    if event == EV_MESSAGE:
        msg = normalize_message(data)
        return RoomMessage(room, msg) if msg is not None else None
    if event == EV_DELETED:
        target = _dict(data.get("message")).get("id")
        return RoomDeleted(room, (f"{CODE}:{target}",)) if valid_message_id(target) else None
    if event == EV_BANNED:
        user = _dict(data.get("user"))
        who = normalize_uid(user.get("slug") or user.get("username"))
        return RoomCleared(room, who) if who else None
    if event == EV_CLEAR:
        return RoomCleared(room, None)
    return None


def interpret(frame: str) -> list[KickEvent]:
    """Traduz um quadro do Centrifugo (uma ou mais linhas JSON); o que nao interessa e omitido."""
    out: list[KickEvent] = []
    for line in frame.split("\n"):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            out.append(Invalid())
            continue
        if not isinstance(obj, dict):
            out.append(Invalid())
            continue
        if not obj:
            out.append(ServerPing())
            continue
        cmd_id = obj.get("id")
        err = obj.get("error")
        if isinstance(err, dict) and isinstance(cmd_id, int):
            code = err.get("code")
            out.append(CommandError(cmd_id, code if isinstance(code, int) else 0))
        elif "connect" in obj:
            info = _dict(obj.get("connect"))
            ping = _number(info.get("ping"), 1, 600) or DEFAULT_PING
            ttl = _number(info.get("ttl"), 1, 7 * 24 * 3600) if info.get("expires") is True else None
            out.append(Connected(ping, ttl))
        elif "refresh" in obj:
            info = _dict(obj.get("refresh"))
            out.append(Refreshed(_number(info.get("ttl"), 1, 7 * 24 * 3600) if info.get("expires") is True else None))
        elif "subscribe" in obj and isinstance(cmd_id, int):
            out.append(Subscribed(cmd_id))
        elif "push" in obj:
            push = _dict(obj.get("push"))
            inner = _dict(_dict(push.get("pub")).get("data"))
            event = translate(push.get("channel"), inner.get("event"), inner.get("data"))
            if event is not None:
                out.append(event)
    return out


# --------------------------------------------------------------------------------------------- conector


class RealtimeRefused(Exception):
    """O Centrifugo respondeu erro a um comando (token vencido, canal inexistente...)."""

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.centrifugo_code = code
        # 100-199 e 3000-3499: temporarios (reconectar); o resto (ex.: 102 canal desconhecido, 103 sem permissao): configuracao.
        self.code = "network_error" if code in (100, 107, 109, 110) or 3000 <= code < 3500 else "config_error"


class KickConnector:
    platform = PLATFORM

    def __init__(self, site: KickSite, sink: ChatSink, clock: Clock) -> None:
        self._site = site
        self._sink = sink
        self._clock = clock
        self._wanted: frozenset[str] = frozenset()
        self._invalid = 0
        self._resolve_fail: dict[str, tuple[str, float, float]] = {}  # slug -> (codigo, proxima tentativa, espera)
        self._subscribed: dict[int, str] = {}  # id da sala -> slug (inscritos nesta sessao)
        self._pending: dict[int, tuple[int, str]] = {}  # id do comando de inscricao -> (sala, slug)
        self._next_cmd = 1
        self._ttl_deadline: float | None = None  # quando o token da conexao expira (epoch)
        self._changed = asyncio.Event()
        self._connected = False
        self._failing = False
        self._last_error: str | None = None
        self._connected_at: float | None = None

    # ------------------------------------------------------------------------------------- interface
    def want(self, refs: frozenset[str]) -> None:
        valid = frozenset(r for r in refs if SLUG_RE.fullmatch(r))
        self._invalid = len(refs) - len(valid)
        if valid != self._wanted:
            self._wanted = valid
            self._changed.set()

    def status(self) -> ConnectorStatus:
        wanted = len(self._wanted)
        joined = sum(1 for slug in self._subscribed.values() if slug in self._wanted) if self._connected else 0
        failing_rooms = sum(1 for slug in self._resolve_fail if slug in self._wanted)
        failures = (1 if self._failing else 0) + failing_rooms + self._invalid
        last = self._last_error
        if last is None and self._resolve_fail:
            last = next(iter(self._resolve_fail.values()))[0]
        return ConnectorStatus(wanted=wanted, connected=joined, failures=failures, last_error=last)

    async def run(self) -> None:
        backoff = Backoff()
        while True:
            while not self._wanted:
                self._changed.clear()
                if not self._wanted:
                    await self._changed.wait()
            error: str | None = None
            try:
                await self._session()
            except ResolveError as exc:
                error = exc.code
                log.warning("Kick: credenciais do tempo real indisponiveis [%s]", error)
            except RealtimeRefused as exc:
                error = exc.code
                if exc.centrifugo_code in (109, 110, 3500, 3501):  # token vencido ou recusado: o proximo uso busca outro
                    self._site.forget_realtime()
                log.warning("Kick: o servidor recusou a conexao [%s] (codigo %d)", error, exc.centrifugo_code)
            except (OSError, TimeoutError, websockets.WebSocketException) as exc:
                error = "network_error"
                log.warning("Kick: queda da conexao [%s] (%s)", error, type(exc).__name__)
            finally:
                self._connected = False
                self._subscribed.clear()
                self._pending.clear()
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

    # --------------------------------------------------------------------------------------- sessao
    def _cmd(self) -> int:
        self._next_cmd += 1
        return self._next_cmd

    async def _session(self) -> None:
        hint = min(self._wanted, default=None)
        if hint is None:
            return
        creds = await self._site.realtime(hint)
        async with connect(creds.ws_url, open_timeout=OPEN_TIMEOUT, max_size=MAX_FRAME) as ws:
            self._next_cmd = 1
            self._ttl_deadline = None
            await ws.send(json.dumps({"connect": {"token": creds.token, "name": "js"}, "id": 1}))
            established = asyncio.Event()
            reader = asyncio.create_task(self._read(ws, established))
            syncer = asyncio.create_task(self._sync(ws, established))
            renewer = asyncio.create_task(self._renew(ws, established))
            tasks = (reader, syncer, renewer)
            try:
                await asyncio.wait(set(tasks), return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            for task in tasks:
                if task.done() and not task.cancelled() and task.exception() is not None:
                    raise task.exception()  # a leitura so termina por excecao (queda ou erro de comando)

    async def _read(self, ws, established: asyncio.Event) -> None:
        timeout = DEFAULT_PING + PING_GRACE
        while True:
            frame = await asyncio.wait_for(ws.recv(), timeout)  # TimeoutError: nem o ping do servidor chegou
            if not isinstance(frame, str):
                continue
            for event in interpret(frame):
                if isinstance(event, ServerPing):
                    await ws.send("{}")
                elif isinstance(event, Connected):
                    timeout = event.ping + min(PING_GRACE, event.ping)
                    self._ttl_deadline = None if event.ttl is None else self._clock.now() + event.ttl
                    self._connected = True
                    self._failing = False
                    self._connected_at = self._clock.monotonic()
                    established.set()
                elif isinstance(event, Refreshed):
                    self._ttl_deadline = None if event.ttl is None else self._clock.now() + event.ttl
                elif isinstance(event, Subscribed):
                    pending = self._pending.pop(event.cmd_id, None)
                    if pending is not None:
                        self._subscribed[pending[0]] = pending[1]
                elif isinstance(event, CommandError):
                    raise RealtimeRefused(event.code)
                elif isinstance(event, Invalid):
                    log.warning("Kick: quadro invalido descartado [invalid_response]")
                elif isinstance(event, RoomMessage):
                    slug = self._subscribed.get(event.room_id)
                    if slug in self._wanted:
                        self._sink.message(PLATFORM, slug, event.msg)
                elif isinstance(event, RoomDeleted):
                    slug = self._subscribed.get(event.room_id)
                    if slug in self._wanted:
                        self._sink.delete(PLATFORM, slug, event.ids)
                elif isinstance(event, RoomCleared):
                    slug = self._subscribed.get(event.room_id)
                    if slug in self._wanted:
                        self._sink.clear(PLATFORM, slug, event.user)

    async def _renew(self, ws, established: asyncio.Event) -> None:
        """Renova o token na propria conexao (`refresh`) antes de ela expirar; sem token novo, a sessao cai e reconecta."""
        await established.wait()
        while True:
            deadline = self._ttl_deadline
            now = self._clock.now()
            if deadline is None or now < deadline - REFRESH_BEFORE:
                espera = REFRESH_STEP if deadline is None else min(REFRESH_STEP, deadline - REFRESH_BEFORE - now)
                await self._clock.sleep(espera)
                continue
            hint = min(self._wanted, default=None)
            if hint is None:
                return
            creds = await self._site.realtime(hint, fresh=True)
            self._ttl_deadline = None  # volta a valer quando a resposta (`Refreshed`) chegar
            await ws.send(json.dumps({"refresh": {"token": creds.token}, "id": self._cmd()}))

    async def _sync(self, ws, established: asyncio.Event) -> None:
        """Mantem as salas inscritas iguais as pedidas; consulta o id de cada sala nova (com backoff por sala)."""
        await established.wait()
        while True:
            self._changed.clear()
            wanted = self._wanted
            if not wanted:
                return
            for room_id, slug in list(self._subscribed.items()):
                if slug not in wanted:
                    await ws.send(json.dumps({"unsubscribe": {"channel": f"chatrooms.{room_id}.v2"}, "id": self._cmd()}))
                    del self._subscribed[room_id]
            for slug in list(self._resolve_fail):
                if slug not in wanted:
                    del self._resolve_fail[slug]
            retry_in: float | None = None
            inscritos = set(self._subscribed.values()) | {slug for _, slug in self._pending.values()}
            for slug in sorted(wanted - inscritos):
                room_id, wait = await self._resolve(slug)
                if room_id is None:
                    retry_in = wait if retry_in is None else min(retry_in, wait)
                    continue
                cmd = self._cmd()
                self._pending[cmd] = (room_id, slug)
                await ws.send(json.dumps({"subscribe": {"channel": f"chatrooms.{room_id}.v2"}, "id": cmd}))
            if retry_in is None:
                await self._changed.wait()
            else:  # sala sem id ainda: tenta de novo quando o backoff dela vencer (ou antes, se a lista mudar)
                timer = asyncio.ensure_future(self._clock.sleep(retry_in))
                changed = asyncio.ensure_future(self._changed.wait())
                try:
                    await asyncio.wait({timer, changed}, return_when=asyncio.FIRST_COMPLETED)
                finally:
                    timer.cancel()
                    changed.cancel()
                    await asyncio.gather(timer, changed, return_exceptions=True)

    async def _resolve(self, slug: str) -> tuple[int | None, float]:
        """(id, 0) ou (None, segundos ate poder tentar de novo). O backoff dobra por sala, ate 2 min."""
        now = self._clock.monotonic()
        failed = self._resolve_fail.get(slug)
        if failed is not None and now < failed[1]:
            return None, failed[1] - now
        try:
            room_id = await self._site.chatroom_id(slug)
        except ResolveError as exc:
            espera = BACKOFF_START if failed is None else min(BACKOFF_MAX, failed[2] * 2)
            self._resolve_fail[slug] = (exc.code, now + espera, espera)
            self._last_error = exc.code
            log.warning("Kick: id da sala indisponivel [%s]", exc.code)
            return None, espera
        self._resolve_fail.pop(slug, None)
        return room_id, 0.0
