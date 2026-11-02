"""Ponto de entrada: `python -m worker_lives`."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from worker_lives.clock import SystemClock
from worker_lives.config import Config, ConfigError
from worker_lives.worker import Worker

log = logging.getLogger("worker_lives")


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    # Os registros de requisição das bibliotecas HTTP trazem URL; não precisamos deles.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def main() -> int:
    configure_logging()
    try:
        config = Config.from_env(os.environ)
    except ConfigError as exc:
        log.error("Configuração inválida: %s", exc)
        return 2

    os.umask(0o007)  # D1: arquivos do SQLite legíveis só por dono e grupo
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    Worker(config, SystemClock(stop), stop).run()
    return 0
