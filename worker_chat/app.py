"""Ponto de entrada: `python -m worker_chat`."""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys

import httpx

from worker_chat.browser import ChatBrowser
from worker_chat.clock import SystemClock
from worker_chat.config import USER_AGENT, Config, ConfigError
from worker_chat.hub import Hub
from worker_chat.platforms import build_connectors
from worker_chat.sse import SseServer
from worker_chat.worker import ChatWorker, Database

log = logging.getLogger("worker_chat")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # As bibliotecas de rede registram URLs e detalhes de conexao; o worker so registra contagens e codigos (D57).
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    logging.getLogger("websockets").setLevel(logging.CRITICAL)


async def amain(config: Config) -> None:
    clock = SystemClock()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: loop.call_soon_threadsafe(stop.set))
    browser = ChatBrowser()
    try:
        async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=False) as http:
            hub = Hub(clock, config.max_chat_clients, on_activity=lambda: worker.wake())
            connectors = build_connectors(config, hub, clock, http, browser)
            worker = ChatWorker(config, clock, Database(config.db_path), hub, SseServer(hub), connectors)
            await worker.run(stop)
    finally:
        await browser.close()


def main() -> int:
    configure_logging()
    try:
        config = Config.from_env(os.environ)
    except ConfigError as exc:
        log.error("Configuracao invalida: %s", exc)
        return 2
    os.umask(0o007)  # D1: arquivos do SQLite legiveis so por dono e grupo
    asyncio.run(amain(config))
    return 0
