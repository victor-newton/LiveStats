"""Interface de conector de plataforma (ponto de extensao da E4) e a lista dos conectores ativos.

Um conector sabe falar com UMA plataforma. O gerente (`worker.py`) so faz tres coisas com ele:
  * `want(refs)`: diz QUAIS canais/lives (ref normalizado) devem estar conectados agora (idempotente; vazio = nada);
  * `run()`: tarefa que vive ate ser cancelada, conecta, reconecta com backoff e entrega tudo no `ChatSink`;
  * `status()`: contagens e codigo do ultimo erro, para o batimento e o log (nunca texto de mensagem).

Os tres conectores (Twitch, Kick e YouTube) sao criados aqui. O Chromium (`ChatBrowser`) e um so, criado pelo ponto de
entrada e compartilhado por quem precisa dele: o YouTube (uma aba por live) e o plano B do Kick; sobe so na primeira necessidade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from worker_chat.browser import ChatBrowser
from worker_chat.clock import Clock
from worker_chat.config import Config
from worker_chat.model import ChatSink


@dataclass(frozen=True)
class ConnectorStatus:
    wanted: int = 0  # canais pedidos (validos)
    connected: int = 0  # canais com a entrada no chat confirmada
    failures: int = 0  # canais/socket em falha agora
    last_error: str | None = None  # codigo do enum de `worker_status.last_error_code`

    @property
    def healthy(self) -> bool:
        return self.failures == 0 and self.connected == self.wanted


class Connector(Protocol):
    platform: str  # nome do banco: twitch | kick | youtube

    def want(self, refs: frozenset[str]) -> None: ...

    async def run(self) -> None: ...

    def status(self) -> ConnectorStatus: ...


def build_connectors(config: Config, sink: ChatSink, clock: Clock, http: httpx.AsyncClient, browser: ChatBrowser) -> dict[str, Connector]:
    from worker_chat.kick import KickConnector
    from worker_chat.kick_site import KickSite
    from worker_chat.twitch import TwitchConnector
    from worker_chat.youtube import YoutubeConnector

    kick_site = KickSite(
        http,
        clock,
        api_base=config.kick_api_base_url,
        web_api_base=config.kick_web_api_base_url,
        site_base=config.kick_site_base_url,
        browser=browser,
    )
    return {
        "twitch": TwitchConnector(config.twitch_irc_url, sink, clock),
        "kick": KickConnector(kick_site, sink, clock),
        "youtube": YoutubeConnector(config.youtube_base_url, browser, sink, clock),
    }
