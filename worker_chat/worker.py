"""Gerente do worker-chat: le a configuracao a cada 10 s, decide as conexoes, grava o batimento e orquestra as tarefas.

Tarefas (todas asyncio, no mesmo processo): servidor SSE, bomba do limitador (`Hub.run_pump`), um `run()` por conector de
plataforma e o laco do gerente. Logs so com contagens e codigos de erro; nunca nome de usuario nem texto de mensagem.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

from worker_chat import __version__, db, memoria
from worker_chat.clock import Clock
from worker_chat.config import Config
from worker_chat.hub import Hub
from worker_chat.platforms import Connector
from worker_chat.rules import ConnKey, Snapshot, wanted_connections
from worker_chat.sse import SseServer

log = logging.getLogger("worker_chat")

CONFIG_INTERVAL = 10  # D57: relê a configuracao a cada 10 s (tambem e o `interval_seconds` do batimento)
LOG_INTERVAL = 60.0

T = TypeVar("T")


class Database:
    """Uma conexao SQLite usada sempre pela MESMA thread (executor de 1), para o laco asyncio nunca esperar o banco."""

    def __init__(self, path: str) -> None:
        self._path = path
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="chat-db")
        self._conn: sqlite3.Connection | None = None

    async def run(self, fn) -> T:
        return await asyncio.get_running_loop().run_in_executor(self._executor, self._call, fn)

    def _call(self, fn):
        if self._conn is None:
            self._conn = db.connect(self._path)
        return fn(self._conn)

    def close(self) -> None:
        def _close() -> None:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

        self._executor.submit(_close).result()
        self._executor.shutdown()


class ChatWorker:
    def __init__(
        self,
        config: Config,
        clock: Clock,
        database: Database,
        hub: Hub,
        server: SseServer,
        connectors: dict[str, Connector],
        config_interval: float = CONFIG_INTERVAL,
    ) -> None:
        self.config = config
        self.config_interval = config_interval
        self.port: int | None = None  # porta do SSE depois de `ready`
        self.ready = asyncio.Event()
        self.clock = clock
        self.database = database
        self.hub = hub
        self.server = server
        self.connectors = connectors
        self._snapshot = Snapshot()
        self._current: set[ConnKey] = set()
        self._skipped = 0
        self._db_failing = False
        self._last_success_at: int | None = None
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Reavalia as conexoes ja (o primeiro cliente de um conjunto chegou)."""
        self._wake.set()

    async def run(self, stop: asyncio.Event) -> None:
        await self._heartbeat("starting")
        await self._reload()
        self._reconcile()
        self.port = await self.server.start(self.config.bind, self.config.port)
        self.ready.set()
        log.info("worker-chat no ar: porta %d, conectores %s", self.port, ",".join(sorted(self.connectors)) or "nenhum")
        tasks = [
            asyncio.create_task(self.hub.run_pump(), name="pump"),
            asyncio.create_task(self._loop(), name="gerente"),
            *(asyncio.create_task(c.run(), name=f"conector-{name}") for name, c in self.connectors.items()),
        ]
        stopper = asyncio.create_task(stop.wait(), name="parada")
        try:
            done, _ = await asyncio.wait({stopper, *tasks}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task is not stopper and task.exception() is not None:
                    raise task.exception()
        finally:
            for task in (stopper, *tasks):
                task.cancel()
            await asyncio.gather(stopper, *tasks, return_exceptions=True)
            await self.server.stop()
            await self._heartbeat("stopped")
            self.database.close()

    # ------------------------------------------------------------------------------------- laco
    async def _loop(self) -> None:
        proxima_leitura = 0.0
        proximo_log = self.clock.monotonic() + LOG_INTERVAL
        while True:
            agora = self.clock.monotonic()
            if agora >= proxima_leitura:
                await self._reload()
                await self._heartbeat("ok")
                proxima_leitura = agora + self.config_interval
            self._reconcile()
            if agora >= proximo_log:
                self._log_counts()
                proximo_log = agora + LOG_INTERVAL
            espera = max(0.05, proxima_leitura - self.clock.monotonic())
            try:
                await asyncio.wait_for(self._wake.wait(), espera)
            except TimeoutError:
                pass
            self._wake.clear()

    async def _reload(self) -> None:
        try:
            self._snapshot = await self.database.run(db.read_snapshot)
        except sqlite3.Error as exc:
            self._db_failing = True
            log.error("Banco indisponivel [db_error] (%s)", type(exc).__name__)
            return
        self._db_failing = False

    def _reconcile(self) -> None:
        routes, skipped = wanted_connections(
            self._snapshot,
            self.hub.active_sets(),
            self.connectors.keys(),
            self.config.max_chat_lives_total,
            self._current,
            {"youtube": self.config.max_yt_chat_tabs},
        )
        if skipped and skipped != self._skipped:
            log.warning(
                "Teto de conexoes (%d; abas do YouTube: %d) atingido: %d fora [config_error]",
                self.config.max_chat_lives_total,
                self.config.max_yt_chat_tabs,
                skipped,
            )
        self._skipped = skipped
        self._current = set(routes)
        self.hub.apply(self._snapshot, routes)
        for name, connector in self.connectors.items():
            connector.want(frozenset(ref for platform, ref in routes if platform == name))

    def _log_counts(self) -> None:
        s = self.hub.stats
        statuses = [c.status() for c in self.connectors.values()]
        log.info(
            "chat: conexoes=%d/%d clientes=%d entregues=%d filtradas=%d descartadas=%d apagamentos=%d lentos=%d",
            sum(st.connected for st in statuses),
            sum(st.wanted for st in statuses),
            self.hub.client_count,
            s.delivered,
            s.filtered,
            s.dropped,
            s.deletions,
            s.slow_clients,
        )
        for field in ("delivered", "filtered", "dropped", "deletions", "slow_clients"):
            setattr(s, field, 0)

    # ---------------------------------------------------------------------------------- batimento
    def status_row(self, state: str, now: int) -> db.StatusRow:
        statuses = {name: c.status() for name, c in self.connectors.items()}
        failing = {name: st for name, st in statuses.items() if st.failures}
        failures = sum(st.failures for st in failing.values()) + (1 if self._db_failing else 0)
        code: str | None = None
        message: str | None = None
        if self._db_failing:
            code, message = "db_error", "banco indisponivel"
        elif failing:
            name, st = next(iter(failing.items()))
            code = st.last_error or "network_error"
            message = f"{name}: {code}"
        if state == "ok" and failures:
            state = "degraded"
        if state == "ok" and not failures:
            self._last_success_at = now
        return db.StatusRow(
            state=state,
            last_heartbeat_at=now,
            last_success_at=self._last_success_at,
            consecutive_failures=failures,
            interval_seconds=max(1, round(self.config_interval)),
            last_error_code=code,
            last_error_message=message,
            version=__version__,
            mem_bytes=memoria.container_memory_bytes(),
        )

    async def _heartbeat(self, state: str) -> None:
        row = self.status_row(state, int(self.clock.now()))
        try:
            await self.database.run(lambda conn: db.write_status(conn, row))
        except sqlite3.Error as exc:
            self._db_failing = True
            log.error("Batimento nao gravado [db_error] (%s)", type(exc).__name__)
