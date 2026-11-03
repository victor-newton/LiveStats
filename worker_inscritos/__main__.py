"""Entrada do container: `python -m worker_inscritos`."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading

from . import __version__
from .config import ConfigError, ler_chave_privada, ler_chave_publica, load_config
from .store import wait_for_schema
from .worker import criar_worker

log = logging.getLogger("worker_inscritos")


def main() -> int:
    os.umask(0o007)  # D1: o SQLite e compartilhado entre containers do mesmo uid/gid
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        config = load_config()
        private_key = ler_chave_privada(config.private_key_file)
        public_key = ler_chave_publica(config.public_key_file, private_key)
    except ConfigError as exc:
        log.error("Erro de configuracao: %s", exc)
        return 2

    stop = threading.Event()
    for sinal in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sinal, lambda *_: stop.set())

    log.info("worker-inscritos %s iniciando (intervalo %ds)", __version__, config.scrape_interval)
    if not wait_for_schema(config.db_path, stop):
        return 0

    worker = criar_worker(config, private_key, public_key, stop=stop)
    try:
        worker.run()
    finally:
        worker.store.close()
    log.info("Encerrado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
