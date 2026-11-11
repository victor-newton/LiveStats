"""YouTube: o chat de cada live lido no Chromium proprio do worker, sem login e sem API (D57).

Uma aba por live em `<base>/live_chat?v=<id>` (a pagina que o proprio YouTube usa para embutir o chat). O worker NAO le o
HTML: depois de trocar o seletor do chat de "Principais mensagens" para "Chat ao vivo", escuta as respostas de
`youtubei/v1/live_chat/get_live_chat` (`page.on('response')`) e traduz as acoes:
  * `addChatItemAction` -> `liveChatTextMessageRenderer` (autor, selos de dono/moderador/membro/verificado, `runs` de texto e
    `emoji`); os demais tipos de item (Super Chat, entrada de membro, avisos) nao entram;
  * `markChatItemAsDeletedAction` e `removeChatItemAction` -> apaga uma mensagem; `markChatItemsByAuthorAsDeletedAction` ->
    apaga as do autor (`clear` com o id do canal dele).
Historico: tudo que a pagina ja trazia (respostas das consultas feitas ate a troca do seletor e a primeira resposta valida
depois dela, que devolve as ultimas ~80 mensagens do "Chat ao vivo") e DESCARTADO; so o que chega depois e entregue. Aba presa (90 s sem nenhuma resposta) e reaberta.
Ref de CANAL (`@handle` ou `UC...`, que acompanha a live ativa do canal; D58): o id do video e resolvido por
`GET <base>/<ref>/live` (httpx, mesmo cookie), lendo `ytInitialPlayerResponse.videoDetails.videoId` com `isLive` verdadeiro;
cache de 60 s por canal; a cada passo da aba o canal e reresolvido e, se o id mudou (live nova), a aba antiga fecha e a nova
abre; sem live ativa o canal simplesmente nao conecta (sem erro). Ref de video continua como antes.
O cookie de consentimento fixo (D39) evita a pagina de consentimento. Imagens/midia/fontes sao bloqueadas no contexto.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import OrderedDict
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

import httpx
from playwright.async_api import BrowserContext, Page, Request, Response

from worker_chat.backoff import STABLE_SECONDS, Backoff
from worker_chat.browser import BrowserError, BrowserTimeout, ChatBrowser
from worker_chat.clock import Clock
from worker_chat.model import (
    PLATFORM_CODES,
    ChatMessage,
    ChatSink,
    PartsBuilder,
    clean_name,
    normalize_uid,
    ordered_roles,
    safe_image_url,
    valid_message_id,
)
from worker_chat.platforms import ConnectorStatus

log = logging.getLogger("worker_chat.youtube")

PLATFORM = "youtube"
CODE = PLATFORM_CODES[PLATFORM]
VIDEO_ID_RE = re.compile(r"[A-Za-z0-9_-]{11}")
CHANNEL_RE = re.compile(r"@[A-Za-z0-9._-]{3,30}|UC[A-Za-z0-9_-]{22}")
RESOLVE_TTL = 60.0  # D58: cache do video ao vivo de cada canal
RESOLVE_TIMEOUT = 15.0
RESOLVE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
GET_LIVE_CHAT = "youtubei/v1/live_chat/get_live_chat"
# Cookie de consentimento fixo (o mesmo de `worker_lives/leitores.py`): evita a pagina de consentimento (D39).
CONSENT_COOKIES = {"SOCS": "CAI", "CONSENT": "YES+cb"}
STUCK_AFTER = 90.0  # D57: sem resposta por 90 s com a live ao vivo, a aba e reaberta
WATCH_STEP = 5.0
NAV_TIMEOUT_MS = 20_000
MENU_TIMEOUT_MS = 20_000
RECONNECT_PAUSE = 1.0
SEEN_MAX = 3000  # ids de mensagem lembrados por aba (anti-duplicata)
PENDING_REQUESTS_MAX = 200

# Seletores do chat do YouTube (confirmados na pagina real em 2026-11-10). O item "Chat ao vivo" e o segundo do menu em
# qualquer idioma ("Principais mensagens" e o primeiro), entao nao depende do texto traduzido.
SEL_MENU_BUTTON = "yt-live-chat-header-renderer tp-yt-paper-button#label"
SEL_MENU_ITEMS = "yt-live-chat-header-renderer tp-yt-paper-listbox#menu a.yt-simple-endpoint"
LIVE_CHAT_ITEM_INDEX = 1

_ICON_ROLES = {"OWNER": "owner", "MODERATOR": "mod", "VERIFIED": "verified", "VERIFIED_ARTIST": "verified"}
_EMOTE_ID_BAD = re.compile(r"[^A-Za-z0-9_-]")


# --------------------------------------------------------------------------------------- normalizacao


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _text_of(node: object) -> str:
    """`{"simpleText": ...}` ou `{"runs": [{"text": ...}]}` -> texto puro."""
    data = _dict(node)
    simple = data.get("simpleText")
    if isinstance(simple, str):
        return simple
    return "".join(r.get("text", "") for r in _list(data.get("runs")) if isinstance(r, dict) and isinstance(r.get("text"), str))


def _roles(badges: object) -> set[str]:
    roles: set[str] = set()
    for badge in _list(badges):
        renderer = _dict(_dict(badge).get("liveChatAuthorBadgeRenderer"))
        role = _ICON_ROLES.get(str(_dict(renderer.get("icon")).get("iconType")))
        if role:
            roles.add(role)
        elif renderer.get("customThumbnail"):  # o selo de membro e uma imagem do canal, sem `icon`
            roles.add("member")
    return roles


def _best_image_url(image: object) -> str | None:
    """Maior miniatura cujo endereco passa na lista de hosts do overlay."""
    thumbs = [t for t in _list(_dict(image).get("thumbnails")) if isinstance(t, dict)]
    thumbs.sort(key=lambda t: t.get("width") if isinstance(t.get("width"), int) else 0, reverse=True)
    for thumb in thumbs:
        url = safe_image_url(thumb.get("url"))
        if url is not None:
            return url
    return None


def _add_emoji(builder: PartsBuilder, emoji: dict) -> None:
    emoji_id = emoji.get("emojiId")
    if not isinstance(emoji_id, str) or not emoji_id:
        return
    if emoji.get("isCustomEmoji") is not True:
        builder.text(emoji_id)  # emoji padrao: o proprio caractere (o `emojiId`), sem imagem
        return
    label = _dict(_dict(_dict(emoji.get("image")).get("accessibility")).get("accessibilityData")).get("label")
    shortcuts = _list(emoji.get("shortcuts"))
    alt = label if isinstance(label, str) and label else (shortcuts[0] if shortcuts and isinstance(shortcuts[0], str) else "emoji")
    builder.image_emote(_EMOTE_ID_BAD.sub("_", emoji_id)[:64], alt, _best_image_url(emoji.get("image")))


def normalize_text_message(renderer: object) -> ChatMessage | None:
    """`liveChatTextMessageRenderer` -> formato normalizado; `None` se faltar id, autor ou texto."""
    data = _dict(renderer)
    msg_id = data.get("id")
    name = clean_name(_text_of(data.get("authorName")))
    if not valid_message_id(msg_id) or not name:
        return None
    builder = PartsBuilder()
    for run in _list(_dict(data.get("message")).get("runs")):
        run = _dict(run)
        if isinstance(run.get("text"), str):
            builder.text(run["text"])
        elif isinstance(run.get("emoji"), dict):
            _add_emoji(builder, run["emoji"])
    parts = builder.build()
    if not parts:
        return None
    uid = normalize_uid(data.get("authorExternalChannelId"))
    return ChatMessage(
        id=f"{CODE}:{msg_id}",
        p=CODE,
        name=name,
        color=None,  # o YouTube nao tem cor de nome
        roles=ordered_roles(_roles(data.get("authorBadges"))),
        parts=parts,
        login=uid,
        uid=uid,
    )


def live_video_id(html: str) -> str | None:
    """Id do video ao vivo na pagina `/live` de um canal: `ytInitialPlayerResponse.videoDetails` com `isLive` verdadeiro."""
    marker = html.find("ytInitialPlayerResponse")
    start = html.find("{", marker) if marker >= 0 else -1
    if start < 0:
        return None
    try:
        player, _ = json.JSONDecoder().raw_decode(html, start)
    except ValueError:
        return None
    details = _dict(_dict(player).get("videoDetails"))
    video_id = details.get("videoId")
    if details.get("isLive") is True and isinstance(video_id, str) and VIDEO_ID_RE.fullmatch(video_id):
        return video_id
    return None


@dataclass(frozen=True)
class Posted:
    msg: ChatMessage


@dataclass(frozen=True)
class Removed:
    ids: tuple[str, ...]


@dataclass(frozen=True)
class Purged:
    """Apagou tudo de um autor (`uid` = id do canal dele, em minusculas)."""

    uid: str


YoutubeEvent = Posted | Removed | Purged


def interpret_response(payload: object) -> list[YoutubeEvent]:
    """Acoes de uma resposta de `get_live_chat` (`continuationContents.liveChatContinuation.actions`). Uma acao pode trazer
    varias chaves (`clickTrackingParams` ao lado da acao de verdade); o que nao interessa e omitido."""
    events: list[YoutubeEvent] = []
    live = _dict(_dict(_dict(payload).get("continuationContents")).get("liveChatContinuation"))
    for action in _list(live.get("actions")):
        action = _dict(action)
        item = _dict(_dict(action.get("addChatItemAction")).get("item"))
        if "liveChatTextMessageRenderer" in item:
            msg = normalize_text_message(item["liveChatTextMessageRenderer"])
            if msg is not None:
                events.append(Posted(msg))
        for key in ("markChatItemAsDeletedAction", "removeChatItemAction"):
            target = _dict(action.get(key)).get("targetItemId")
            if key in action and valid_message_id(target):
                events.append(Removed((f"{CODE}:{target}",)))
        by_author = _dict(action.get("markChatItemsByAuthorAsDeletedAction"))
        uid = normalize_uid(by_author.get("externalChannelId"))
        if uid:
            events.append(Purged(uid))
    return events


# ------------------------------------------------------------------------------------------------ abas


class TabFailed(Exception):
    """A aba nao chegou a ler o chat. `code` ∈ enum de `worker_status.last_error_code`."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Tab:
    def __init__(self, ref: str, now: float) -> None:
        self.ref = ref
        self.last_response = now
        self.requests = 0  # `get_live_chat` iniciados ate agora
        self.order: OrderedDict[Request, int] = OrderedDict()  # pedido -> numero de ordem (ate a resposta)
        self.switch_at: int | None = None  # `requests` no instante em que o seletor foi trocado (None: ainda nao trocou)
        self.history_seen = False  # ja chegou (e foi descartada) a resposta da troca: dai em diante e mensagem nova
        self.ready = False  # = trocou o seletor e `history_seen`
        self.crashed = False
        self.seen: OrderedDict[str, None] = OrderedDict()
        self._tasks: set[asyncio.Task[None]] = set()

    def spawn(self, coro) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def cancel_pending(self) -> None:
        for task in list(self._tasks):
            task.cancel()

    def remember(self, msg_id: str) -> bool:
        """`True` se o id e novo."""
        if msg_id in self.seen:
            return False
        self.seen[msg_id] = None
        while len(self.seen) > SEEN_MAX:
            self.seen.popitem(last=False)
        return True


class YoutubeConnector:
    platform = PLATFORM

    def __init__(self, base_url: str, browser: ChatBrowser, sink: ChatSink, clock: Clock) -> None:
        self._base = base_url
        self._browser = browser
        self._sink = sink
        self._clock = clock
        self._wanted: frozenset[str] = frozenset()
        self._invalid = 0
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._tabs: dict[str, _Tab] = {}
        self._resolved: dict[str, tuple[float, str | None]] = {}  # canal -> (valido ate, id do video ao vivo ou None)
        self._failing: dict[str, str] = {}  # ref -> codigo do ultimo erro da aba
        self._last_error: str | None = None
        self._context: BrowserContext | None = None
        self._context_lock = asyncio.Lock()
        self._changed = asyncio.Event()

    # ------------------------------------------------------------------------------------- interface
    def want(self, refs: frozenset[str]) -> None:
        valid = frozenset(r for r in refs if VIDEO_ID_RE.fullmatch(r) or CHANNEL_RE.fullmatch(r))
        self._invalid = len(refs) - len(valid)
        if valid != self._wanted:
            self._wanted = valid
            self._changed.set()

    def status(self) -> ConnectorStatus:
        wanted = len(self._wanted)
        ready = sum(1 for ref, tab in self._tabs.items() if tab.ready and ref in self._wanted)
        failing = sum(1 for ref in self._failing if ref in self._wanted)
        last = self._last_error if failing or self._invalid else None
        return ConnectorStatus(wanted=wanted, connected=ready, failures=failing + self._invalid, last_error=last)

    async def run(self) -> None:
        try:
            while True:
                self._changed.clear()
                await self._reconcile()
                await self._changed.wait()
        finally:
            await self._stop_tabs(set(self._tasks))
            await self._close_context()

    async def _reconcile(self) -> None:
        """Abre a aba de cada live pedida e fecha a das que ninguem mais quer."""
        await self._stop_tabs({ref for ref in self._tasks if ref not in self._wanted})
        for ref in sorted(self._wanted - self._tasks.keys()):
            self._tasks[ref] = asyncio.create_task(self._tab_loop(ref), name=f"yt-{ref}")
        if not self._tasks:
            await self._close_context()  # sem aba nenhuma, o contexto (e o que ele guarda) nao precisa ficar

    async def _stop_tabs(self, refs: set[str]) -> None:
        tasks = [self._tasks.pop(ref) for ref in refs if ref in self._tasks]
        for ref in refs:
            self._failing.pop(ref, None)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    # ---------------------------------------------------------------------------------- contexto
    def _cookies(self) -> list[dict[str, str]]:
        host = urlsplit(self._base).hostname or ""
        domain = "." + host.removeprefix("www.") if host.endswith("youtube.com") else host
        return [{"name": name, "value": value, "domain": domain, "path": "/"} for name, value in CONSENT_COOKIES.items()]

    async def _get_context(self) -> BrowserContext:
        async with self._context_lock:
            if self._context is None:
                self._context = await self._browser.new_context(self._cookies())
            return self._context

    async def _close_context(self) -> None:
        context, self._context = self._context, None
        if context is not None:
            try:
                await context.close()
            except BrowserError as exc:
                log.warning("YouTube: falha ao fechar o contexto (%s)", type(exc).__name__)

    # ------------------------------------------------------------------------------ canal -> video
    async def _resolve(self, ref: str) -> str | None:
        """Id do video a abrir para `ref`: o proprio id (video) ou a live ativa do canal (cache de 60 s); `None` = sem live."""
        if VIDEO_ID_RE.fullmatch(ref):
            return ref
        now = self._clock.monotonic()
        cached = self._resolved.get(ref)
        if cached is not None and now < cached[0]:
            return cached[1]
        path = f"/channel/{ref}/live" if ref.startswith("UC") else f"/{quote(ref, safe='@')}/live"
        headers = {"User-Agent": RESOLVE_UA, "Cookie": "; ".join(f"{k}={v}" for k, v in CONSENT_COOKIES.items())}
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=RESOLVE_TIMEOUT) as http:
                resp = await http.get(f"{self._base}{path}")
        except httpx.HTTPError as exc:
            raise TabFailed("network_error") from exc
        if resp.status_code == 404:
            video_id = None  # canal inexistente: tratado como sem live
        elif resp.status_code != 200:
            raise TabFailed("http_error")
        else:
            video_id = live_video_id(resp.text)
        self._resolved[ref] = (now + RESOLVE_TTL, video_id)
        return video_id

    # -------------------------------------------------------------------------------------- aba
    async def _tab_loop(self, ref: str) -> None:
        """Mantem a aba da live: reabre se ela cair ou ficar presa; falha de abertura = backoff (dobra ate 2 min)."""
        backoff = Backoff()
        while True:
            opened = self._clock.monotonic()
            error: str | None = None
            try:
                await self._tab_session(ref)
            except TabFailed as exc:
                error = exc.code
                log.warning("YouTube: aba nao leu o chat [%s]", error)
            except (BrowserError, TimeoutError) as exc:
                error = "network_error"
                log.warning("YouTube: queda da aba [%s] (%s)", error, type(exc).__name__)
            if self._clock.monotonic() - opened >= STABLE_SECONDS:
                backoff.reset()
            if error is None:
                await self._clock.sleep(RECONNECT_PAUSE)
            else:
                self._failing[ref] = error
                self._last_error = error
                await self._clock.sleep(backoff.next())

    async def _tab_session(self, ref: str) -> None:
        """Uma abertura da aba. Volta (sem erro) quando ela fica presa, a live do canal mudou ou o canal nao tem live; qualquer
        outra saida e excecao."""
        video = await self._resolve(ref)
        if video is None:
            await self._clock.sleep(WATCH_STEP)  # canal sem live ativa: nao conecta, tenta de novo quando o cache expirar
            return
        context = await self._get_context()
        try:
            page = await context.new_page()
        except BrowserError:
            await self._close_context()  # o navegador/contexto morreu: o proximo uso pede um novo
            raise
        tab = _Tab(ref, self._clock.monotonic())
        self._tabs[ref] = tab
        try:
            page.on("request", lambda request: self._on_request(tab, request))
            page.on("response", lambda response: tab.spawn(self._on_response(tab, response)))
            page.on("crash", lambda *_: setattr(tab, "crashed", True))
            await page.goto(f"{self._base}/live_chat?v={quote(video, safe='')}", wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            host = urlsplit(page.url).hostname or ""
            if host.startswith("consent.") or "/sorry/" in page.url:
                raise TabFailed("blocked")  # pagina de consentimento ou verificacao no lugar do chat
            await self._select_live_chat(page, tab)
            while True:
                await self._clock.sleep(WATCH_STEP)
                if tab.crashed or page.is_closed():
                    raise TabFailed("network_error")
                if not VIDEO_ID_RE.fullmatch(ref):
                    try:
                        current = await self._resolve(ref)
                    except TabFailed:
                        current = video  # falha momentanea ao consultar o canal: mantem a aba
                    if current != video:
                        log.info("YouTube: a live do canal mudou; aba trocada")
                        return
                if self._clock.monotonic() - tab.last_response >= STUCK_AFTER:
                    log.warning("YouTube: aba sem resposta ha %d s; reaberta [network_error]", int(STUCK_AFTER))
                    return
        finally:
            self._tabs.pop(ref, None)
            tab.cancel_pending()
            try:
                await page.close()
            except BrowserError as exc:
                log.warning("YouTube: falha ao fechar a aba (%s)", type(exc).__name__)

    async def _select_live_chat(self, page: Page, tab: _Tab) -> None:
        try:
            await page.click(SEL_MENU_BUTTON, timeout=MENU_TIMEOUT_MS)
            await page.locator(SEL_MENU_ITEMS).nth(LIVE_CHAT_ITEM_INDEX).click(timeout=MENU_TIMEOUT_MS)
            # Marca depois do clique: o que ja estava a caminho (visao antiga) fica de um lado; a primeira resposta valida
            # do que for pedido dai em diante e o historico do "Chat ao vivo" (a pagina pede ~0,5 s apos o clique).
            tab.switch_at = tab.requests
        except BrowserTimeout:
            raise TabFailed("invalid_response") from None  # sem seletor de chat (chat desativado, pagina diferente...)

    # ---------------------------------------------------------------------------------- respostas
    def _on_request(self, tab: _Tab, request: Request) -> None:
        if GET_LIVE_CHAT not in request.url:
            return
        tab.requests += 1
        tab.order[request] = tab.requests
        while len(tab.order) > PENDING_REQUESTS_MAX:
            tab.order.popitem(last=False)

    async def _on_response(self, tab: _Tab, response: Response) -> None:
        if GET_LIVE_CHAT not in response.url:
            return
        order = tab.order.pop(response.request, 0)
        if response.status != 200:
            log.warning("YouTube: resposta do chat com erro HTTP [http_error]")
            return
        try:
            payload = await response.json()
        except (BrowserError, ValueError) as exc:
            log.warning("YouTube: resposta do chat ilegivel [invalid_response] (%s)", type(exc).__name__)
            return
        tab.last_response = self._clock.monotonic()
        # Descartadas: tudo que veio antes da troca do seletor (a pagina ja trazia) e a 1a resposta valida depois dela (o
        # historico recente do "Chat ao vivo"). So o que vem depois disso e mensagem nova.
        before_switch = tab.switch_at is None or order <= tab.switch_at
        history = before_switch or not tab.history_seen
        if not before_switch:
            tab.history_seen = True
        for event in interpret_response(payload):
            if isinstance(event, Posted):
                fresh = tab.remember(event.msg.id)
                if fresh and not history:
                    self._sink.message(PLATFORM, tab.ref, event.msg)
            elif isinstance(event, Removed):
                if not history:
                    self._sink.delete(PLATFORM, tab.ref, event.ids)
            elif isinstance(event, Purged) and not history:
                self._sink.clear(PLATFORM, tab.ref, event.uid)
        if tab.history_seen and not tab.ready:
            tab.ready = True
            self._failing.pop(tab.ref, None)
