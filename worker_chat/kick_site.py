"""Como falar com o chat do Kick, descoberto pelo PROPRIO SITE (D57): nada de chave fixa no codigo.

Em 2026-11-10 o site do Kick (kick.com) usa **Centrifugo** (nao mais o Pusher): a pagina de um canal pede
  * `POST <web>/realtime/auth/connection` `{"client_id": <uuid>}` -> `{"data": {"token": <JWT de visitante>}}`;
  * `POST <web>/realtime/connection` (ou `/realtime/channels/<id>/chat/connection`) -> o endereco do WebSocket
    (`wss://realtime.<regiao>.platform.kick.com/connection/websocket`, `provider: "centrifugo"`);
  * abre o WebSocket, manda `{"connect": {"token": ..., "name": "js"}, "id": 1}` e inscreve `chatrooms.<id>.v2`.
Aqui o worker repete o que o site faz, por HTTP direto; se o Kick bloquear o HTTP (Cloudflare), o plano B abre a pagina
publica do canal no Chromium do worker e le, do proprio trafego da pagina (`page.on('websocket')`), o endereco do socket, o
token e a sala. Tudo fica em memoria com validade: o token ate o `exp` dele, o id da sala por 6 h.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import uuid
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx

from worker_chat.browser import BrowserError, ChatBrowser
from worker_chat.clock import Clock

log = logging.getLogger("worker_chat.kick")

SLUG_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,99}")
_ROOM_CHANNEL_RE = re.compile(r"chatrooms\.([0-9]{1,12})\.v2")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_.-]{20,4000}")
HTTP_TIMEOUT = 8.0
BROWSE_NAV_TIMEOUT_MS = 20_000
BROWSE_WAIT = 25.0  # a pagina precisa abrir o socket e inscrever a sala neste tempo
ROOM_TTL = 6 * 3600.0  # o id da sala de um canal quase nunca muda
TOKEN_MARGIN = 120.0  # renova o token antes do `exp`
DEFAULT_TOKEN_TTL = 2700.0  # o que o site entrega hoje; so vale se o JWT vier sem `exp` legivel
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


class ResolveError(Exception):
    """Falha ao descobrir sala/credenciais. `code` ∈ enum de `worker_status.last_error_code`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def status_to_code(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status in (401, 403):
        return "blocked"
    if status in (404, 410):
        return "not_found"
    return "http_error"


@dataclass(frozen=True)
class Realtime:
    """Credenciais do tempo real: endereco do WebSocket (Centrifugo) e o token de visitante dele."""

    ws_url: str
    token: str
    expires_at: float  # epoch (segundos): o `exp` do JWT


def valid_ws_url(url: object) -> bool:
    """So `wss` em kick.com e subdominios (ou `ws` em loopback, para o servidor local), sem credenciais, query nem fragmento."""
    if not isinstance(url, str) or len(url) > 300:
        return False
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        parts.port  # noqa: B018 - levanta ValueError se a porta for invalida
    except ValueError:
        return False
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        return False
    if parts.scheme == "wss":
        return host == "kick.com" or host.endswith(".kick.com")
    return parts.scheme == "ws" and host in _LOOPBACK_HOSTS


def token_expiry(token: str, now: float) -> float:
    """`exp` (epoch) do JWT, lido sem verificar assinatura (so para saber quando renovar); sem `exp` valido, `now` + 45 min."""
    try:
        payload = token.split(".")[1]
        exp = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("exp")
    except (IndexError, ValueError):
        return now + DEFAULT_TOKEN_TTL
    if isinstance(exp, bool) or not isinstance(exp, (int, float)) or exp <= 0:
        return now + DEFAULT_TOKEN_TTL
    return float(exp)


def _data(payload: object) -> dict:
    data = payload.get("data") if isinstance(payload, dict) else None
    return data if isinstance(data, dict) else {}


class KickSite:
    """Sala de cada canal e credenciais do tempo real, com cache em memoria e plano B pelo navegador."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        clock: Clock,
        *,
        api_base: str,
        web_api_base: str,
        site_base: str,
        browser: ChatBrowser | None,
    ) -> None:
        self._http = http
        self._clock = clock
        self._api = api_base
        self._web = web_api_base
        self._site = site_base
        self._browser = browser
        self._rooms: dict[str, tuple[int, float]] = {}  # slug -> (id da sala, valido ate)
        self._realtime: Realtime | None = None
        self._lock = asyncio.Lock()  # uma descoberta por vez (o navegador e caro)

    # ------------------------------------------------------------------------------------------ sala
    async def chatroom_id(self, slug: str) -> int:
        if not SLUG_RE.fullmatch(slug):
            raise ResolveError("not_found")
        cached = self._rooms.get(slug)
        if cached is not None and self._clock.monotonic() < cached[1]:
            return cached[0]
        try:
            room = await self._room_by_http(slug)
        except ResolveError as exc:
            if exc.code in ("not_found", "network_error") or self._browser is None:
                raise
            log.warning("Kick: id da sala pelo HTTP indisponivel [%s]; tentando pelo navegador", exc.code)
            room, _ = await self._browse(slug)
            return room
        self._remember_room(slug, room)
        return room

    async def _room_by_http(self, slug: str) -> int:
        try:
            response = await self._http.get(
                f"{self._api}/{quote(slug, safe='')}", headers={"Accept": "application/json"}, timeout=HTTP_TIMEOUT
            )
        except httpx.HTTPError:
            raise ResolveError("network_error") from None
        if response.status_code == 200 and "json" not in response.headers.get("content-type", ""):
            raise ResolveError("blocked")  # pagina de desafio da Cloudflare no lugar do JSON
        if response.status_code != 200:
            raise ResolveError(status_to_code(response.status_code))
        try:
            payload = response.json()
        except ValueError:
            raise ResolveError("invalid_response") from None
        room = payload.get("chatroom") if isinstance(payload, dict) else None
        room_id = room.get("id") if isinstance(room, dict) else None
        if isinstance(room_id, bool) or not isinstance(room_id, int) or not 0 < room_id < 10**12:
            raise ResolveError("invalid_response")
        return room_id

    def _remember_room(self, slug: str, room: int) -> None:
        self._rooms[slug] = (room, self._clock.monotonic() + ROOM_TTL)

    # ------------------------------------------------------------------------------ tempo real
    async def realtime(self, hint_slug: str, *, fresh: bool = False) -> Realtime:
        """Credenciais validas (do cache, se ainda valem). `fresh=True` forca um token novo (renovacao da sessao).
        `hint_slug`: canal cuja pagina o plano B abre se o HTTP estiver bloqueado."""
        async with self._lock:
            cached = self._realtime
            if not fresh and cached is not None and self._clock.now() < cached.expires_at - TOKEN_MARGIN:
                return cached
            try:
                found = await self._realtime_by_http()
            except ResolveError as exc:
                if exc.code == "network_error" or self._browser is None or not SLUG_RE.fullmatch(hint_slug):
                    raise
                log.warning("Kick: credenciais do tempo real pelo HTTP indisponiveis [%s]; tentando pelo navegador", exc.code)
                _, found = await self._browse(hint_slug)
            self._realtime = found
            return found

    def forget_realtime(self) -> None:
        """O Centrifugo recusou o token: o proximo uso busca outro."""
        self._realtime = None

    async def _post_json(self, path: str, body: dict) -> dict:
        try:
            response = await self._http.post(
                f"{self._web}{path}",
                json=body,
                headers={"Accept": "application/json", "x-app-platform": "web", "Referer": f"{self._site}/"},
                timeout=HTTP_TIMEOUT,
            )
        except httpx.HTTPError:
            raise ResolveError("network_error") from None
        if response.status_code == 200 and "json" not in response.headers.get("content-type", ""):
            raise ResolveError("blocked")
        if response.status_code != 200:
            raise ResolveError(status_to_code(response.status_code))
        try:
            return _data(response.json())
        except ValueError:
            raise ResolveError("invalid_response") from None

    async def _realtime_by_http(self) -> Realtime:
        client_id = str(uuid.uuid4())
        auth = await self._post_json("/realtime/auth/connection", {"client_id": client_id})
        token = auth.get("token")
        conn = await self._post_json(
            "/realtime/connection",
            {
                "client": {"id": client_id, "type": "web"},
                "capabilities": {"accepted_providers": [{"provider": "pusher"}, {"provider": "centrifugo"}]},
            },
        )
        url = None
        for item in conn.get("connections") if isinstance(conn.get("connections"), list) else []:
            if isinstance(item, dict) and item.get("provider") == "centrifugo":
                creds = item.get("credentials")
                url = creds.get("url") if isinstance(creds, dict) else None
                break
        if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token) or not valid_ws_url(url):
            raise ResolveError("invalid_response")
        return Realtime(url, token, token_expiry(token, self._clock.now()))

    # ----------------------------------------------------------------------------- plano B
    async def _browse(self, slug: str) -> tuple[int, Realtime]:
        """Abre a pagina publica do canal no Chromium e le, do trafego dela, a sala e as credenciais do socket."""
        if self._browser is None or not SLUG_RE.fullmatch(slug):
            raise ResolveError("blocked")
        found: asyncio.Future[tuple[int, Realtime]] = asyncio.get_running_loop().create_future()
        seen: dict[str, object] = {}

        def on_sent(ws_url: str, payload: object) -> None:
            if not isinstance(payload, str):
                return
            for line in payload.split("\n"):  # o site manda varios comandos no mesmo quadro, um por linha
                try:
                    cmd = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(cmd, dict):
                    continue
                connect = cmd.get("connect")
                token = connect.get("token") if isinstance(connect, dict) else None
                if isinstance(token, str) and _TOKEN_RE.fullmatch(token) and valid_ws_url(ws_url):
                    seen["token"], seen["url"] = token, ws_url
                sub = cmd.get("subscribe")
                channel = sub.get("channel") if isinstance(sub, dict) else None
                room = _ROOM_CHANNEL_RE.fullmatch(channel) if isinstance(channel, str) else None
                if room is not None:
                    seen["room"] = int(room.group(1))
            if not found.done() and {"token", "url", "room"} <= seen.keys():
                token, url, room_id = str(seen["token"]), str(seen["url"]), int(seen["room"])  # type: ignore[call-overload]
                found.set_result((room_id, Realtime(url, token, token_expiry(token, self._clock.now()))))

        def on_websocket(ws) -> None:
            ws.on("framesent", lambda payload: on_sent(ws.url, payload))

        try:
            context = await self._browser.new_context()
        except BrowserError as exc:
            log.warning("Kick: Chromium indisponivel [blocked] (%s)", type(exc).__name__)
            raise ResolveError("blocked") from None
        try:
            page = await context.new_page()
            page.on("websocket", on_websocket)
            await page.goto(f"{self._site}/{quote(slug, safe='')}", wait_until="domcontentloaded", timeout=BROWSE_NAV_TIMEOUT_MS)
            room, creds = await asyncio.wait_for(found, BROWSE_WAIT)
        except (BrowserError, TimeoutError) as exc:
            log.warning("Kick: o navegador nao achou o chat [blocked] (%s)", type(exc).__name__)
            raise ResolveError("blocked") from None
        finally:
            await context.close()
        self._remember_room(slug, room)
        self._realtime = creds
        return room, creds
