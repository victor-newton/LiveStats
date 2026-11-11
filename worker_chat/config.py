"""Configuracao do worker-chat, lida SOMENTE de variaveis de ambiente nao secretas (D57).

Nenhum segredo: o worker-chat nao tem Docker secrets. As URLs-base opcionais (D19) valem igualmente em producao e so
aceitam https/wss, ou http/ws para loopback, sem credenciais, query nem fragmento; qualquer outra coisa derruba o worker
na subida (nunca e ignorada em silencio). Nao existe variavel que ative caminho de teste.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

DEFAULT_DB_PATH = "/data/livestats.db"
DEFAULT_BIND = "0.0.0.0"  # noqa: S104 - dentro do container; so a rede interna alcanca a porta (sem `ports:` no compose)
DEFAULT_PORT = 8090
DEFAULT_MAX_CHAT_LIVES_TOTAL = 30  # D57: conexoes (plataforma, ref) ao mesmo tempo
MAX_MAX_CHAT_LIVES_TOTAL = 500
DEFAULT_MAX_CHAT_CLIENTS = 200  # D57: conexoes SSE ao mesmo tempo (acima, 503)
MAX_MAX_CHAT_CLIENTS = 5000

DEFAULT_MAX_YT_CHAT_TABS = 6  # D57: abas do Chromium (uma por live do YouTube) ao mesmo tempo
MAX_MAX_YT_CHAT_TABS = 50

DEFAULT_TWITCH_IRC_URL = "wss://irc-ws.chat.twitch.tv:443"
DEFAULT_KICK_API_BASE_URL = "https://kick.com/api/v2/channels"
DEFAULT_KICK_SITE_BASE_URL = "https://kick.com"  # pagina publica do canal (plano B pelo navegador)
DEFAULT_KICK_WEB_API_BASE_URL = "https://web.kick.com/api/v1"  # credenciais do tempo real (Centrifugo), as mesmas do site
DEFAULT_YOUTUBE_BASE_URL = "https://www.youtube.com"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    """Configuracao invalida. A mensagem nunca contem valores de ambiente alem do nome da variavel."""


@dataclass(frozen=True)
class Config:
    db_path: str = DEFAULT_DB_PATH
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    max_chat_lives_total: int = DEFAULT_MAX_CHAT_LIVES_TOTAL
    max_chat_clients: int = DEFAULT_MAX_CHAT_CLIENTS
    twitch_irc_url: str = DEFAULT_TWITCH_IRC_URL
    kick_api_base_url: str = DEFAULT_KICK_API_BASE_URL
    kick_site_base_url: str = DEFAULT_KICK_SITE_BASE_URL
    kick_web_api_base_url: str = DEFAULT_KICK_WEB_API_BASE_URL
    youtube_base_url: str = DEFAULT_YOUTUBE_BASE_URL
    max_yt_chat_tabs: int = DEFAULT_MAX_YT_CHAT_TABS

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        return cls(
            db_path=_texto(env, "DB_PATH", DEFAULT_DB_PATH),
            bind=_texto(env, "CHAT_BIND", DEFAULT_BIND),
            port=_inteiro(env, "CHAT_PORT", DEFAULT_PORT, 1024, 65535),
            max_chat_lives_total=_inteiro(env, "MAX_CHAT_LIVES_TOTAL", DEFAULT_MAX_CHAT_LIVES_TOTAL, 1, MAX_MAX_CHAT_LIVES_TOTAL),
            max_chat_clients=_inteiro(env, "MAX_CHAT_CLIENTS", DEFAULT_MAX_CHAT_CLIENTS, 1, MAX_MAX_CHAT_CLIENTS),
            twitch_irc_url=validar_base_url("TWITCH_IRC_URL", _texto(env, "TWITCH_IRC_URL", DEFAULT_TWITCH_IRC_URL), ("wss", "ws")),
            kick_api_base_url=validar_base_url(
                "KICK_API_BASE_URL", _texto(env, "KICK_API_BASE_URL", DEFAULT_KICK_API_BASE_URL), ("https", "http")
            ),
            kick_site_base_url=validar_base_url(
                "KICK_SITE_BASE_URL", _texto(env, "KICK_SITE_BASE_URL", DEFAULT_KICK_SITE_BASE_URL), ("https", "http")
            ),
            kick_web_api_base_url=validar_base_url(
                "KICK_WEB_API_BASE_URL", _texto(env, "KICK_WEB_API_BASE_URL", DEFAULT_KICK_WEB_API_BASE_URL), ("https", "http")
            ),
            youtube_base_url=validar_base_url(
                "YOUTUBE_WWW_BASE_URL", _texto(env, "YOUTUBE_WWW_BASE_URL", DEFAULT_YOUTUBE_BASE_URL), ("https", "http")
            ),
            max_yt_chat_tabs=_inteiro(env, "MAX_YT_CHAT_TABS", DEFAULT_MAX_YT_CHAT_TABS, 1, MAX_MAX_YT_CHAT_TABS),
        )


def _texto(env: Mapping[str, str], nome: str, padrao: str) -> str:
    return env.get(nome, "").strip() or padrao


def _inteiro(env: Mapping[str, str], nome: str, padrao: int, minimo: int, maximo: int) -> int:
    bruto = env.get(nome, "").strip()
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfigError(f"{nome} deve ser um inteiro") from None
    if not minimo <= valor <= maximo:
        raise ConfigError(f"{nome} deve estar entre {minimo} e {maximo}")
    return valor


def validar_base_url(nome: str, valor: str, esquemas: tuple[str, str]) -> str:
    """Aceita so o esquema seguro (`https`/`wss`), ou o inseguro para localhost/127.0.0.1/::1. Devolve sem barra final."""
    seguro, inseguro = esquemas
    try:
        partes = urlsplit(valor)
        host = partes.hostname
        partes.port  # noqa: B018 - levanta ValueError se a porta for invalida
    except ValueError:
        raise ConfigError(f"{nome}: URL invalida") from None
    if partes.scheme not in (seguro, inseguro) or not host:
        raise ConfigError(f"{nome}: use uma URL {seguro} (ou {inseguro} para localhost)")
    if partes.scheme == inseguro and host not in _LOOPBACK_HOSTS:
        raise ConfigError(f"{nome}: {inseguro} so e aceito para localhost, 127.0.0.1 ou ::1")
    if partes.username is not None or partes.password is not None or partes.query or partes.fragment:
        raise ConfigError(f"{nome}: a URL nao pode ter credenciais, query nem fragmento")
    return valor.rstrip("/")
