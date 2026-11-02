"""Ciclo do worker-lives (docs/legacy-formats.md §1.4 e ARCHITECTURE.md D17, D23, D25)."""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import closing
from dataclasses import replace

import httpx

from worker_lives import __version__, db
from worker_lives.clock import Clock
from worker_lives.config import Config
from worker_lives.util import extrair_video_id, formatar, utc_iso
from worker_lives.youtube import REQUEST_TIMEOUT, VideoInfo, YouTubeError, fetch_videos

log = logging.getLogger("worker_lives")

SLOT_COUNT = 4
MAX_BACKOFF_SECONDS = 900
SCHEMA_RETRY_SECONDS = 5
MAX_TITLE_LENGTH = 1000  # CHECK de live_stats.title


def backoff_seconds(interval: int, failures: int) -> int:
    """Espera até o próximo ciclo: `interval` sem falhas; dobra a cada falha até 900 s (D17)."""
    if failures <= 0:
        return interval
    return min(interval * 2 ** min(failures, 20), max(MAX_BACKOFF_SECONDS, interval))


class Worker:
    def __init__(self, config: Config, clock: Clock, stop_event: threading.Event | None = None):
        self.config = config
        self.clock = clock
        self.stop_event = stop_event or threading.Event()
        self.client = httpx.Client(timeout=REQUEST_TIMEOUT)
        self._failures = 0
        self._last_success_at: int | None = None
        self._last_status: db.StatusRow | None = None
        self._schema_seen = False

    # ------------------------------------------------------------------ ciclo de vida

    def run(self) -> None:
        log.info("worker-lives iniciado (intervalo %ss, 1 chamada em lote por ciclo)", self.config.interval)
        try:
            if not self._wait_for_schema():
                return
            self._write_status(self._status("starting", self.clock.now()))
            while not self.stop_event.is_set():
                self.run_cycle()
                self._wait(backoff_seconds(self.config.interval, self._failures))
        finally:
            self._write_stopped()
            self.client.close()

    def stop(self) -> None:
        self.stop_event.set()

    def _wait_for_schema(self) -> bool:
        """Espera a api criar o esquema; o worker nunca cria tabelas (LIV-START-01)."""
        while not self.stop_event.is_set():
            if db.schema_ready(self.config.db_path):
                self._schema_seen = True
                return True
            log.info("Aguardando a api criar o esquema do banco")
            self.clock.sleep(SCHEMA_RETRY_SECONDS)
        return False

    def _wait(self, total_seconds: int) -> None:
        """Espera até o próximo ciclo em fatias de no máximo `interval`, renovando o heartbeat."""
        remaining = float(total_seconds)
        while remaining > 0 and not self.stop_event.is_set():
            chunk = min(remaining, float(self.config.interval))
            self.clock.sleep(chunk)
            remaining -= chunk
            if remaining > 0 and not self.stop_event.is_set():
                self._heartbeat()

    # ------------------------------------------------------------------ um ciclo

    def run_cycle(self) -> None:
        try:
            with closing(db.connect(self.config.db_path)) as conn:
                self._cycle(conn)
        except sqlite3.OperationalError as exc:
            self._on_db_error(exc)

    def _cycle(self, conn: sqlite3.Connection) -> None:
        slot_ids = self._slot_ids(db.read_slots(conn))
        unique_ids = list(dict.fromkeys(slot_ids.values()))

        videos: dict[str, VideoInfo] = {}
        error: YouTubeError | None = None
        if unique_ids:
            try:
                videos = fetch_videos(
                    self.client, self.config.api_base_url, self.config.api_key, unique_ids
                )
            except YouTubeError as exc:
                error = exc
                log.error("Falha ao consultar o YouTube [%s]: %s", exc.code, exc.message)
            else:
                log.info("Lote: %d vídeo(s) consultado(s) em 1 chamada", len(unique_ids))

        now = self.clock.now()
        updated_at = utc_iso(now)
        rows: list[db.LiveRow] = []
        for slot in range(1, SLOT_COUNT + 1):
            if slot not in slot_ids:
                rows.append(_sem_url_row(slot, updated_at))
            elif error is None:
                info = videos.get(slot_ids[slot])
                if info is None:
                    log.warning("Slot %d: sem retorno da API para %s", slot, slot_ids[slot])
                else:
                    rows.append(_video_row(slot, info, updated_at))
                    log.info(
                        "Slot %d [%s] views=%s likes=%s",
                        slot, info.status, formatar(info.views), formatar(info.likes),
                    )

        if error is None:
            self._failures = 0
            self._last_success_at = int(now)
        else:
            self._failures += 1
        status = self._status("ok", now, error)
        db.write_cycle(conn, rows, status)
        self._schema_seen = True
        self._last_status = status

    def _slot_ids(self, urls: dict[int, str]) -> dict[int, str]:
        slot_ids: dict[int, str] = {}
        for slot in range(1, SLOT_COUNT + 1):
            url = urls.get(slot, "").strip()
            if not url:
                continue
            video_id = extrair_video_id(url)
            if video_id:
                slot_ids[slot] = video_id
            else:
                log.warning("Slot %d: URL inválida", slot)
        return slot_ids

    # ------------------------------------------------------------------ status

    def _status(self, state: str, now: float, error: YouTubeError | None = None) -> db.StatusRow:
        code = error.code if error else None
        if error is not None:
            state = "error" if error.code == "config_error" else "degraded"
        wait = backoff_seconds(self.config.interval, self._failures)
        return db.StatusRow(
            state=state,
            last_heartbeat_at=int(now),
            last_success_at=self._last_success_at,
            consecutive_failures=self._failures,
            interval_seconds=self.config.interval,
            next_attempt_at=int(now + wait) if self._failures > 0 else None,
            last_error_code=code,
            last_error_message=error.message if error else None,
            version=__version__,
        )

    def _on_db_error(self, exc: sqlite3.OperationalError) -> None:
        self._failures += 1
        log.error("Banco indisponível [db_error]: %s", exc)
        now = self.clock.now()
        status = replace(
            self._status("degraded", now),
            last_error_code="db_error",
            last_error_message="Banco de dados indisponível",
        )
        self._write_status(status)

    def _heartbeat(self) -> None:
        """Renova só o heartbeat durante a espera (backoff), mantendo o resto do estado."""
        if self._last_status is None:
            return
        self._write_status(replace(self._last_status, last_heartbeat_at=int(self.clock.now())))

    def _write_status(self, status: db.StatusRow) -> None:
        """Grava o status; falha de banco aqui é só registrada (o próximo ciclo tenta de novo)."""
        try:
            with closing(db.connect(self.config.db_path)) as conn:
                db.write_status(conn, status)
        except sqlite3.OperationalError as exc:
            log.warning("Não foi possível gravar o status do worker: %s", exc)
            return
        self._last_status = status

    def _write_stopped(self) -> None:
        if not self._schema_seen:
            return
        base = self._last_status or self._status("stopped", self.clock.now())
        self._write_status(
            replace(base, state="stopped", last_heartbeat_at=int(self.clock.now()))
        )
        log.info("worker-lives encerrado")


def _sem_url_row(slot: int, updated_at: str) -> db.LiveRow:
    return db.LiveRow(
        slot=slot, active=0, video_id="", title="", status="sem_url", views=0, likes=0,
        views_formatted="0", likes_formatted="0", updated_at=updated_at,
    )


def _video_row(slot: int, info: VideoInfo, updated_at: str) -> db.LiveRow:
    return db.LiveRow(
        slot=slot,
        active=1,
        video_id=info.video_id,
        title=info.title[:MAX_TITLE_LENGTH],
        status=info.status,
        views=info.views,
        likes=info.likes,
        views_formatted=formatar(info.views),
        likes_formatted=formatar(info.likes),
        updated_at=updated_at,
    )
