"""Ciclo do worker-lives (ARCHITECTURE.md D17, D23, D25, D39, D40, D42).

Cada par (plataforma, referência) tem o seu próprio ciclo, independente e em paralelo (D42): um agendador
leve, na thread principal, dispara cada par quando ele vence num pool de threads (uma requisição HTTP por
thread, nunca o mesmo par duas vezes ao mesmo tempo). As threads só LEEM; os resultados voltam por uma fila
e SÓ a thread principal grava no SQLite (uma conexão, com autorizador). A mesma live em vários conjuntos é
lida uma vez e gravada em todas as `live_id`. Em erro, o intervalo do par dobra até 60 s.
"""

from __future__ import annotations

import logging
import queue
import random
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import closing
from dataclasses import dataclass, replace

import httpx

from worker_lives import __version__, db, memoria
from worker_lives.clock import Clock
from worker_lives.config import Config
from worker_lives.leitores import Leitura, LeituraErro, NavegadorKick, ler, novo_cliente
from worker_lives.util import day_sp, nome_live, utc_iso

log = logging.getLogger("worker_lives")

MAX_BACKOFF_SECONDS = 60
MAX_THREADS = 64  # o teto de 200 lives (D40) é lido em poucos segundos mesmo com 1 s por requisição
SCHEMA_RETRY_SECONDS = 5
IDLE_SECONDS = 1.0  # teto da espera do agendador: relê `lives` e vê lives novas em até 1 s
HEARTBEAT_EVERY = 5.0
FIRST_JITTER = (0.0, 2.0)  # só no primeiro disparo de cada par, para espalhar a carga

Leitor = Callable[[httpx.Client, str, str, "NavegadorKick | None"], Leitura]


def backoff_seconds(interval: int, failures: int) -> int:
    """Espera até a próxima leitura do par: `interval` sem falhas; dobra a cada falha até 60 s (D42)."""
    if failures <= 0:
        return interval
    return min(interval * 2 ** min(failures, 20), max(MAX_BACKOFF_SECONDS, interval))


@dataclass
class _Par:
    next_due: float
    failures: int = 0
    started_at: float = 0.0  # início da leitura em curso (ou da última): o próximo disparo conta a partir dele


@dataclass
class _Resultado:
    par: tuple[str, str]
    leitura: Leitura | None
    erro: LeituraErro | None
    latency_ms: int = 0  # medido em volta da requisição, na thread de leitura (D43)
    lido_em: float = 0.0  # instante do fim da leitura (relógio do worker)
    proxima: float = 0.0  # próxima leitura agendada do par (D42), definida ao recolher o resultado


class Worker:
    def __init__(
        self,
        config: Config,
        clock: Clock,
        stop_event: threading.Event | None = None,
        *,
        client: httpx.Client | None = None,
        kick: NavegadorKick | None = None,
        rng: random.Random | None = None,
        leitor: Leitor = ler,
        timer: Callable[[], float] = time.monotonic,
    ):
        self.timer = timer  # mede a latência da requisição (D43); injetável nos testes
        self.config = config
        self.clock = clock
        self.stop_event = stop_event or threading.Event()
        self.client = client or novo_cliente()
        self.rng = rng or random.Random()
        self.leitor = leitor
        self.kick = kick
        if self.kick is None and config.kick_browser:
            from worker_lives.kick_browser import NavegadorKickPlaywright

            self.kick = NavegadorKickPlaywright()
        self._pares: dict[tuple[str, str], _Par] = {}
        self._pool = ThreadPoolExecutor(max_workers=MAX_THREADS, thread_name_prefix="leitor")
        self._em_voo: dict[tuple[str, str], Future] = {}
        self._fila: queue.Queue[_Resultado] = queue.Queue()  # threads -> thread principal
        self._pendentes: list[_Resultado] = []  # lidos, ainda não gravados (banco indisponível)
        self._acordar = threading.Event()
        self._failures = 0  # leituras ruins seguidas (qualquer par); zera em qualquer sucesso
        self._last_success_at: int | None = None
        self._last_error: LeituraErro | None = None
        self._last_status: db.StatusRow | None = None
        self._last_heartbeat = 0.0
        self._schema_seen = False
        self._ao_vivo_em: dict[int, int] = {}  # D59: conjunto -> última leitura com live ao vivo (só em memória)
        self._primeira_limpeza = True
        self._metas: dict[int, db.LiveRow] = {}  # lives do passo atual (id -> linha), para nomes e conjuntos
        self._saude: dict[int, list[int]] = {}  # minuto -> [leituras, soma_ms, max_ms, ok, erros] (D43)
        self._ultima_limpeza = 0.0
        self._tocados: set[int] = set()  # minutos de saúde a regravar neste passo

    # ------------------------------------------------------------------ ciclo de vida

    def run(self) -> None:
        log.info(
            "worker-lives iniciado (cada live a cada %ss, em paralelo, até %s leituras simultâneas)",
            self.config.interval,
            MAX_THREADS,
        )
        try:
            if not self._wait_for_schema():
                return
            self._write_status(self._status("starting", self.clock.now()))
            while not self.stop_event.is_set():
                self._acordar.clear()
                espera = self.run_cycle()
                self._acordar.wait(espera)  # uma leitura pronta acorda o agendador na hora
        finally:
            self.close()
            self._write_stopped()

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)
        self.client.close()
        fechar = getattr(self.kick, "fechar", None)
        if fechar is not None:
            fechar()

    def stop(self) -> None:
        self.stop_event.set()
        self._acordar.set()

    def _wait_for_schema(self) -> bool:
        """Espera a api criar o esquema; o worker nunca cria tabelas (LIV-START-01)."""
        while not self.stop_event.is_set():
            if db.schema_ready(self.config.db_path):
                self._schema_seen = True
                return True
            log.info("Aguardando a api criar o esquema do banco")
            self.clock.sleep(SCHEMA_RETRY_SECONDS)
        return False

    def aguardar_leituras(self, timeout: float = 10.0) -> None:
        """Bloqueia até as leituras em curso terminarem (usado pelos testes; a produção não precisa)."""
        wait(list(self._em_voo.values()), timeout=timeout)

    # ------------------------------------------------------------------ um passo

    def run_cycle(self) -> float:
        """Grava o que as threads já leram, dispara os pares vencidos e devolve quanto esperar (s)."""
        try:
            with closing(db.connect(self.config.db_path)) as conn:
                return self._step(conn)
        except sqlite3.OperationalError as exc:
            self._on_db_error(exc)
            return IDLE_SECONDS

    def _step(self, conn: sqlite3.Connection) -> float:
        grupos: dict[tuple[str, str], list[int]] = {}
        self._metas = {}
        for live in db.read_lives(conn):
            grupos.setdefault((live.platform, live.ref), []).append(live.id)
            self._metas[live.id] = live
        agora = self.clock.now()
        for par in list(self._pares):
            if par not in grupos:
                del self._pares[par]
        for par in grupos:
            if par not in self._pares:
                self._pares[par] = _Par(next_due=agora + self.rng.uniform(*FIRST_JITTER))

        self._recolher(agora)
        gravou = self._gravar(conn, grupos)

        for par, estado in self._pares.items():
            if estado.next_due <= agora and par not in self._em_voo and not self.stop_event.is_set():
                estado.started_at = agora
                self._em_voo[par] = self._pool.submit(self._ler_em_thread, par)

        if not gravou:
            self._heartbeat(conn, agora)
        proximos = [e.next_due - agora for p, e in self._pares.items() if p not in self._em_voo]
        return max(0.01, min([IDLE_SECONDS, *proximos]))

    def _ler_em_thread(self, par: tuple[str, str]) -> None:
        """Roda numa thread do pool: só lê (sem banco) e devolve o resultado pela fila."""
        platform, ref = par
        leitura: Leitura | None = None
        erro: LeituraErro | None = None
        inicio = self.timer()
        try:
            leitura = self.leitor(self.client, platform, ref, self.kick)
        except LeituraErro as exc:
            erro = exc
        except Exception as exc:  # noqa: BLE001 - a thread nunca pode morrer sem devolver o resultado
            log.warning("Falha inesperada ao ler %s (%s)", platform, type(exc).__name__)
            erro = LeituraErro("parse_error", "Falha inesperada ao ler a página")
        latencia = max(0, round((self.timer() - inicio) * 1000))
        self._fila.put(_Resultado(par, leitura, erro, latency_ms=latencia, lido_em=self.clock.now()))
        self._acordar.set()

    def _recolher(self, agora: float) -> None:
        """Traz os resultados prontos e atualiza o agendamento de cada par."""
        while True:
            try:
                res = self._fila.get_nowait()
            except queue.Empty:
                return
            self._em_voo.pop(res.par, None)
            estado = self._pares.get(res.par)
            if estado is None:  # a live saiu da tabela durante a leitura
                continue
            if res.erro is None:
                estado.failures = 0
                estado.next_due = estado.started_at + self.config.interval
                self._failures = 0
                self._last_error = None
                self._last_success_at = int(agora)
                log.info("%s: %s viewers=%s likes=%s", res.par[0], res.leitura.status, res.leitura.viewers, res.leitura.likes)
            else:
                estado.failures += 1
                estado.next_due = estado.started_at + backoff_seconds(self.config.interval, estado.failures)
                self._failures += 1
                self._last_error = res.erro
                log.warning("Falha ao ler %s [%s]: %s", res.par[0], res.erro.code, res.erro.message)
            res.proxima = estado.next_due
            self._pendentes.append(res)

    def _gravar(self, conn: sqlite3.Connection, grupos: dict[tuple[str, str], list[int]]) -> bool:
        """Grava todos os resultados pendentes (e o status) numa só transação; devolve se gravou algo."""
        if not self._pendentes:
            return False
        agora = self.clock.now()
        status = self._status("ok", agora)
        saude = {minuto: list(valores) for minuto, valores in self._saude.items()}
        self._tocados = set()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for res in self._pendentes:
                live_ids = grupos.get(res.par)
                if not live_ids:
                    continue
                anteriores = db.previous_states(conn, live_ids)
                row = db.Reading(status="erro", error_code=res.erro.code) if res.erro else _row(res.leitura)
                db.write_readings(conn, live_ids, row, utc_iso(agora))
                self._gravar_historico(conn, live_ids, res, row, anteriores, saude)
            self._gravar_saude(conn, saude)
            db.update_peaks(conn, day_sp(agora), int(agora))
            self._limpar(conn, agora)
            db.write_status_na_transacao(conn, status)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        self._saude = {m: v for m, v in saude.items() if m >= int(agora) // 60 * 60 - 60}  # guarda só o minuto atual e o anterior
        self._pendentes.clear()
        self._schema_seen = True
        self._last_status = status
        self._last_heartbeat = agora
        return True

    # ------------------------------------------------------------------ histórico, saúde e eventos (D43)

    def _gravar_historico(
        self,
        conn: sqlite3.Connection,
        live_ids: list[int],
        res: _Resultado,
        row: db.Reading,
        anteriores: dict[int, tuple[str, str | None]],
        saude: dict[int, list[int]],
    ) -> None:
        """Amostra (só leitura ok), `live_reads` (toda leitura), saúde do minuto e eventos (só mudanças)."""
        at = int(res.lido_em)
        minuto = at // 60 * 60
        agregado = saude.setdefault(minuto, [0, 0, 0, 0, 0])
        agregado[0] += 1
        agregado[1] += res.latency_ms
        agregado[2] = max(agregado[2], res.latency_ms)
        agregado[4 if res.erro else 3] += 1
        self._tocados.add(minuto)
        codigo_erro = res.erro.code if res.erro else None
        for live_id in live_ids:
            if res.erro is None and self._sessao_ativa(conn, live_id, at):
                db.write_sample(conn, live_id, at, row.viewers, row.likes)
            db.write_read(conn, live_id, at, int(res.proxima), res.latency_ms, codigo_erro)
            self._evento_de_mudanca(conn, live_id, row, anteriores.get(live_id), at)

    def _sessao_ativa(self, conn: sqlite3.Connection, live_id: int, at: int) -> bool:
        """D59: só grava amostra enquanto o conjunto tem live ao vivo (ou até 10 min depois da última); com tudo off o gráfico para."""
        meta = self._metas.get(live_id)
        if meta is None:
            return False
        if db.set_has_live(conn, meta.set_id):
            self._ao_vivo_em[meta.set_id] = at
            return True
        visto = self._ao_vivo_em.get(meta.set_id)
        return visto is not None and at - visto <= db.GRACE_SECONDS

    def _evento_de_mudanca(
        self, conn: sqlite3.Connection, live_id: int, row: db.Reading, antes: tuple[str, str | None] | None, at: int
    ) -> None:
        """Só mudança vira evento: estado da live mudou, começou a dar erro ou voltou a ser lida."""
        if antes is None or antes[0] == row.status:
            return  # primeira leitura da live, ou igual à anterior
        meta = self._metas.get(live_id)
        nome = nome_live(meta.platform, meta.ref, meta.label, row.title) if meta else f"Live {live_id}"
        if row.status == "erro":
            db.write_event(conn, at, "warn", "read_failed", f"{nome}: falha na leitura ({row.error_code})", live_id)
        elif antes[0] == "erro":
            db.write_event(conn, at, "ok", "read_recovered", f"{nome}: voltou a ser lida", live_id)
        elif row.status == "ao_vivo":
            db.write_event(conn, at, "ok", "live_started", f"{nome}: entrou ao vivo", live_id)
        else:
            db.write_event(conn, at, "info", "status_changed", f"{nome}: {antes[0]} para {row.status}", live_id)

    def _gravar_saude(self, conn: sqlite3.Connection, saude: dict[int, list[int]]) -> None:
        for minuto in sorted(self._tocados):
            leituras, soma, maximo, ok, erros = saude[minuto]
            db.write_health(conn, minuto, round(soma / max(1, leituras)), maximo, ok, erros)

    def _limpar(self, conn: sqlite3.Connection, agora: float) -> None:
        """Uma vez por minuto: compacta > 24 h, apaga sessões além da retenção, saúde > 2 h e picos > 30 dias."""
        if agora - self._ultima_limpeza < 60:
            return
        db.cleanup(
            conn, int(agora), day_sp(agora - db.PEAKS_KEEP_DAYS * 86400), self.config.retention_days, full=self._primeira_limpeza
        )
        self._primeira_limpeza = False
        self._ultima_limpeza = agora

    # ------------------------------------------------------------------ status

    def _status(self, state: str, now: float) -> db.StatusRow:
        erro = self._last_error
        if erro is not None:
            state = "degraded"
        return db.StatusRow(
            state=state,
            last_heartbeat_at=int(now),
            last_success_at=self._last_success_at,
            consecutive_failures=self._failures,
            interval_seconds=self.config.interval,
            next_attempt_at=None,
            last_error_code=erro.code if erro else None,
            last_error_message=erro.message if erro else None,
            version=__version__,
            mem_bytes=memoria.container_memory_bytes(),
        )

    def _on_db_error(self, exc: sqlite3.OperationalError) -> None:
        self._failures += 1
        log.error("Banco indisponível [db_error]: %s", exc)
        status = replace(
            self._status("degraded", self.clock.now()),
            last_error_code="db_error",
            last_error_message="Banco de dados indisponível",
        )
        self._write_status(status)

    def _heartbeat(self, conn: sqlite3.Connection, agora: float) -> None:
        """Sem gravação no passo: renova só o heartbeat (no máximo a cada 5 s), mantendo o resto do estado."""
        if agora - self._last_heartbeat < HEARTBEAT_EVERY:
            return
        base = self._last_status or self._status("ok", agora)
        status = replace(base, last_heartbeat_at=int(agora))
        db.write_status(conn, status)
        self._last_status = status
        self._last_heartbeat = agora

    def _write_status(self, status: db.StatusRow) -> None:
        """Grava o status; falha de banco aqui é só registrada (o próximo passo tenta de novo)."""
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
        self._write_status(replace(base, state="stopped", last_heartbeat_at=int(self.clock.now())))
        log.info("worker-lives encerrado")


def _row(leitura: Leitura) -> db.Reading:
    return db.Reading(
        status=leitura.status, title=leitura.title, viewers=leitura.viewers, likes=leitura.likes
    )
