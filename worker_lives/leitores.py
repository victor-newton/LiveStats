"""Leitores das páginas públicas de live (D39): YouTube, Twitch e Kick, sem a YouTube Data API.

Segurança: nenhuma exceção, corpo de resposta ou cookie é copiado para mensagens/logs. Cada falha vira
um `LeituraErro` com código do enum fechado de `live_stats.error_code` e mensagem fixa e curta.

Campos confirmados em respostas reais (D39):
* YouTube, HTML de `watch?v=`: `ytInitialPlayerResponse.videoDetails.{videoId,title,isLive,isUpcoming}`;
  espectadores ao vivo em `ytInitialData...videoViewCountRenderer.originalViewCount` (ou o texto de
  `viewCount.runs[0].text`); likes exatos em `ytInitialPlayerResponse.microformat.playerMicroformatRenderer.likeCount`
  (reserva: o texto compacto do botão de like, ex. "20 mil").
* Twitch, `POST gql.twitch.tv/gql`: `data.user.stream.{type,viewersCount,title}`; `stream` nulo = offline.
* Kick, `GET /api/v2/channels/<slug>`: `livestream.{is_live,viewer_count,session_title}`; `livestream` nulo = offline.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

REQUEST_TIMEOUT = 5.0  # D42: conexão e leitura, 5 s cada; um par nunca fica mais de 15 s sem nova tentativa
MAX_CONEXOES = 64  # uma por thread de leitura (worker.MAX_THREADS)
MAX_TITLE_LENGTH = 1000  # CHECK de live_stats.title

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
BROWSER_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
# Cookie de consentimento fixo: evita a página de consentimento do YouTube (D39).
YOUTUBE_COOKIES = {"SOCS": "CAI", "CONSENT": "YES+cb"}


def _base_opcional(nome: str, padrao: str) -> str:
    """URL-base (D19): `padrao`, ou a de uma variável de ambiente OPCIONAL, usada só pela pilha de teste (tests/e2e)
    para apontar o worker a um servidor falso de páginas de live. Só aceita https, ou http para loopback, sem
    credenciais, query nem fragmento; qualquer outra coisa derruba o worker na subida (nunca é ignorada em silêncio)."""
    valor = os.environ.get(nome, "").strip()
    if not valor:
        return padrao
    try:
        partes = urlsplit(valor)
        host = partes.hostname
        partes.port  # noqa: B018 - levanta ValueError se a porta for inválida
    except ValueError:
        raise RuntimeError(f"{nome}: URL inválida") from None
    seguro = partes.scheme == "https" or (partes.scheme == "http" and host in ("localhost", "127.0.0.1", "::1"))
    if not seguro or not host or partes.username or partes.password or partes.query or partes.fragment:
        raise RuntimeError(f"{nome}: use uma URL https (ou http para localhost), sem credenciais, query nem fragmento")
    return valor.rstrip("/")


YOUTUBE_BASE = _base_opcional("YOUTUBE_WWW_BASE_URL", "https://www.youtube.com")

# Client-ID público que o próprio site da Twitch usa no GQL.
TWITCH_GQL_URL = _base_opcional("TWITCH_GQL_URL", "https://gql.twitch.tv/gql")
TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wki5h1ko"
TWITCH_QUERY = "query($login:String!){user(login:$login){stream{type viewersCount title}}}"

KICK_API = _base_opcional("KICK_API_BASE_URL", "https://kick.com/api/v2/channels")

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[0-9A-Za-z_-]{22}$")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{1,100}$")
_LOGIN_RE = re.compile(r"^[a-z0-9_-]{1,100}$")


class LeituraErro(Exception):
    """Falha ao ler uma live. `code` ∈ enum de `live_stats.error_code`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Leitura:
    status: str  # ao_vivo | agendada | encerrada | offline
    title: str
    viewers: int | None
    likes: int | None  # sempre None em Twitch/Kick


class NavegadorKick(Protocol):
    def buscar_json(self, slug: str) -> Any: ...


def novo_cliente() -> httpx.Client:
    """Cliente com cabeçalhos de navegador desktop, `Accept-Language: pt-BR` e o cookie de consentimento."""
    client = httpx.Client(
        timeout=REQUEST_TIMEOUT,
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=MAX_CONEXOES, max_keepalive_connections=MAX_CONEXOES),
    )
    for nome, valor in YOUTUBE_COOKIES.items():
        client.cookies.set(nome, valor, domain=".youtube.com")
    return client


def ler(client: httpx.Client, platform: str, ref: str, kick: NavegadorKick | None = None) -> Leitura:
    if platform == "youtube":
        return ler_youtube(client, ref)
    if platform == "twitch":
        return ler_twitch(client, ref)
    if platform == "kick":
        return ler_kick(client, ref, kick)
    raise LeituraErro("parse_error", "Plataforma desconhecida")


# ---------------------------------------------------------------------------- HTTP comum


def _erro_http(status: int) -> LeituraErro:
    if status == 429:
        return LeituraErro("rate_limited", "Limite de requisições atingido")
    if status in (401, 403):
        return LeituraErro("blocked", "Acesso bloqueado pelo site")
    if status in (404, 410):
        return LeituraErro("not_found", "Página ou canal não encontrado")
    return LeituraErro("http_error", f"Erro HTTP {status}")


def _requisitar(client: httpx.Client, metodo: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        response = client.request(metodo, url, **kwargs)
    except httpx.TimeoutException:
        raise LeituraErro("network_error", "Tempo esgotado ao consultar o site") from None
    except httpx.HTTPError:
        raise LeituraErro("network_error", "Falha de rede ao consultar o site") from None
    return response


def _inteiro(valor: object) -> int | None:
    """Inteiro >= 0 vindo de número ou de texto só com dígitos; None se não for."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if valor >= 0 else None
    if isinstance(valor, str) and valor.isascii() and valor.isdigit():
        return int(valor)
    return None


def _titulo(valor: object) -> str:
    return valor[:MAX_TITLE_LENGTH] if isinstance(valor, str) else ""


# ---------------------------------------------------------------------------- YouTube


def url_youtube(ref: str) -> str:
    """URL da página a ler: vídeo (ID de 11 caracteres) ou /live de um canal (@handle ou UC…)."""
    if _CHANNEL_ID_RE.match(ref):
        return f"{YOUTUBE_BASE}/channel/{ref}/live"
    if _HANDLE_RE.match(ref):
        return f"{YOUTUBE_BASE}/{ref}/live"
    if _VIDEO_ID_RE.match(ref):
        return f"{YOUTUBE_BASE}/watch?v={ref}"
    raise LeituraErro("not_found", "Referência do YouTube inválida")


def ler_youtube(client: httpx.Client, ref: str) -> Leitura:
    por_canal = ref.startswith("@") or ref.startswith("UC") and len(ref) == 24
    response = _requisitar(client, "GET", url_youtube(ref))
    if response.status_code != 200:
        raise _erro_http(response.status_code)
    host = response.url.host or ""
    if host.startswith("consent.") or "/sorry/" in response.url.path:
        raise LeituraErro("blocked", "Página de consentimento ou verificação do YouTube")
    return interpretar_youtube(response.text, por_canal=por_canal)


def _extrair_json(html: str, nome: str) -> Any | None:
    """Objeto JSON atribuído a `nome` no HTML (`ytInitialData = {...};`), ou None se não houver."""
    achado = re.search(re.escape(nome) + r"\s*=\s*(?=\{)", html)
    if achado is None:
        return None
    try:
        valor, _ = json.JSONDecoder().raw_decode(html, achado.end())
    except ValueError:
        return None
    return valor


def _achar(objeto: Any, chave: str, profundidade: int = 0):
    """Gera os valores de todas as chaves `chave` em dicts/listas aninhados (limite de profundidade)."""
    if profundidade > 40:
        return
    if isinstance(objeto, dict):
        for k, v in objeto.items():
            if k == chave:
                yield v
            yield from _achar(v, chave, profundidade + 1)
    elif isinstance(objeto, list):
        for item in objeto:
            yield from _achar(item, chave, profundidade + 1)


def numero_compacto(texto: str) -> int | None:
    """"20 mil" -> 20000, "1,2 mil" -> 1200, "3.4K" -> 3400, "1.234" -> 1234; None se não for número."""
    achado = re.match(r"^\s*(\d[\d.,]*)\s*([A-Za-zÀ-ÿ]*)", texto)
    if achado is None:
        return None
    numero, sufixo = achado.group(1), achado.group(2).lower()
    multiplicador = {"": 1, "mil": 1_000, "k": 1_000, "mi": 1_000_000, "m": 1_000_000, "bi": 10**9, "b": 10**9}.get(
        sufixo
    )
    if multiplicador is None:
        return None
    if multiplicador == 1:  # sem sufixo: ponto/vírgula são separadores de milhar
        digitos = re.sub(r"[.,]", "", numero)
        return int(digitos) if digitos.isdigit() else None
    try:
        return round(float(numero.replace(".", "").replace(",", ".") if "," in numero else numero) * multiplicador)
    except ValueError:
        return None


def _espectadores(dados: Any) -> int | None:
    for renderer in _achar(dados, "videoViewCountRenderer"):
        if not isinstance(renderer, dict):
            continue
        exato = _inteiro(renderer.get("originalViewCount"))
        if exato is not None:
            return exato
        view = renderer.get("viewCount")
        if isinstance(view, dict):
            runs = view.get("runs")
            texto = runs[0].get("text") if isinstance(runs, list) and runs and isinstance(runs[0], dict) else None
            texto = texto if isinstance(texto, str) else view.get("simpleText")
            if isinstance(texto, str):
                return numero_compacto(texto)
    return None


def _likes(player: Any, dados: Any) -> int | None:
    microformat = player.get("microformat") if isinstance(player, dict) else None
    renderer = microformat.get("playerMicroformatRenderer") if isinstance(microformat, dict) else None
    exato = _inteiro(renderer.get("likeCount")) if isinstance(renderer, dict) else None
    if exato is not None:
        return exato
    for botao in _achar(dados, "likeButtonViewModel"):
        for titulo in _achar(botao, "title"):
            if isinstance(titulo, str):
                valor = numero_compacto(titulo)
                if valor is not None:
                    return valor
    return None


def interpretar_youtube(html: str, *, por_canal: bool = False) -> Leitura:
    """Lê o HTML de uma página do YouTube. `por_canal`: a URL era `/live` de um canal."""
    player = _extrair_json(html, "ytInitialPlayerResponse")
    if not isinstance(player, dict):
        if por_canal:  # canal sem live no momento: a página /live é a do próprio canal
            return Leitura("encerrada", "", 0, None)
        raise LeituraErro("parse_error", "Página do YouTube sem dados do player")
    estado = player.get("playabilityStatus")
    if isinstance(estado, dict) and estado.get("status") in ("ERROR", "LOGIN_REQUIRED") and "videoDetails" not in player:
        raise LeituraErro("not_found", "Vídeo indisponível no YouTube")
    detalhes = player.get("videoDetails")
    if not isinstance(detalhes, dict):
        raise LeituraErro("parse_error", "Página do YouTube sem detalhes do vídeo")
    titulo = _titulo(detalhes.get("title"))
    dados = _extrair_json(html, "ytInitialData")
    likes = _likes(player, dados)
    if detalhes.get("isLive") is True:
        return Leitura("ao_vivo", titulo, _espectadores(dados), likes)
    if detalhes.get("isUpcoming") is True:
        return Leitura("agendada", titulo, 0, likes)
    return Leitura("encerrada", titulo, 0, likes)


# ---------------------------------------------------------------------------- Twitch


def ler_twitch(client: httpx.Client, ref: str) -> Leitura:
    login = ref.lower()
    if not _LOGIN_RE.match(login):
        raise LeituraErro("not_found", "Login da Twitch inválido")
    response = _requisitar(
        client,
        "POST",
        TWITCH_GQL_URL,
        headers={"Client-ID": TWITCH_CLIENT_ID, "Accept": "application/json"},
        json={"query": TWITCH_QUERY, "variables": {"login": login}},
    )
    if response.status_code != 200:
        raise _erro_http(response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise LeituraErro("parse_error", "Resposta da Twitch não é JSON") from None
    return interpretar_twitch(payload)


def interpretar_twitch(payload: Any) -> Leitura:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise LeituraErro("parse_error", "Resposta inesperada da Twitch")
    if "user" not in payload["data"]:
        raise LeituraErro("parse_error", "Resposta inesperada da Twitch")
    usuario = payload["data"]["user"]
    if usuario is None:
        raise LeituraErro("not_found", "Canal da Twitch não encontrado")
    if not isinstance(usuario, dict):
        raise LeituraErro("parse_error", "Resposta inesperada da Twitch")
    stream = usuario.get("stream")
    if stream is None:
        return Leitura("offline", "", 0, None)
    if not isinstance(stream, dict):
        raise LeituraErro("parse_error", "Resposta inesperada da Twitch")
    return Leitura("ao_vivo", _titulo(stream.get("title")), _inteiro(stream.get("viewersCount")) or 0, None)


# ---------------------------------------------------------------------------- Kick


def _bloqueio_kick(response: httpx.Response) -> bool:
    """403, ou HTML de desafio do Cloudflare no lugar do JSON."""
    if response.status_code == 403:
        return True
    tipo = response.headers.get("content-type", "")
    return response.status_code == 200 and "json" not in tipo and response.text.lstrip().startswith("<")


def ler_kick(client: httpx.Client, ref: str, navegador: NavegadorKick | None = None) -> Leitura:
    slug = ref.lower()
    if not _LOGIN_RE.match(slug):
        raise LeituraErro("not_found", "Canal do Kick inválido")
    response = _requisitar(client, "GET", f"{KICK_API}/{slug}", headers={"Accept": "application/json"})
    if _bloqueio_kick(response):
        if navegador is None:
            raise LeituraErro("blocked", "Kick bloqueou a consulta (Cloudflare)")
        return interpretar_kick(navegador.buscar_json(slug))
    if response.status_code != 200:
        raise _erro_http(response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise LeituraErro("parse_error", "Resposta do Kick não é JSON") from None
    return interpretar_kick(payload)


def interpretar_kick(payload: Any) -> Leitura:
    if not isinstance(payload, dict) or "livestream" not in payload:
        raise LeituraErro("parse_error", "Resposta inesperada do Kick")
    live = payload["livestream"]
    if live is None:
        return Leitura("offline", "", 0, None)
    if not isinstance(live, dict):
        raise LeituraErro("parse_error", "Resposta inesperada do Kick")
    if live.get("is_live") is False:
        return Leitura("offline", "", 0, None)
    return Leitura("ao_vivo", _titulo(live.get("session_title")), _inteiro(live.get("viewer_count")) or 0, None)
