"""Pool dos contadores de usuario (D51): varios contadores lidos por um unico Chromium, em rodizio.

Desenho:
- Um Chromium so, numa thread propria com um laco `asyncio`; o contador do mestre (worker.py) tem a sua pagina
  persistente em outro processo do navegador e nunca espera por este pool.
- Cada leitura abre um CONTEXTO NOVO com os cookies do contador, vai a home do Studio, detecta o canal (D38), le o
  numero, exporta a sessao renovada (D37) e FECHA o contexto. Nada de um usuario sobra para o seguinte.
- No maximo `user_pool_size` paginas abertas ao mesmo tempo (semaforo); o excedente espera a vez. Com o do mestre,
  o worker tem no maximo `user_pool_size + 1` paginas abertas.
- O estado de cada contador (tentativas, backoff, proxima leitura) fica em memoria e vai ao `counter_status`.
- Toda SQL leva `counter_id` explicito (store.py); falha de um contador nunca altera o de outro.
"""

from __future__ import annotations

import asyncio
import gc
import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from nacl.public import PrivateKey, PublicKey
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .browser import (
    _JS_CANAL_PRONTO,
    _JS_LER_PAGINA,
    _JS_NOME_CANAL,
    CHROMIUM_ARGS,
    IGNORE_DEFAULT_ARGS,
    INIT_SCRIPT,
    USER_AGENT,
    VIEWPORT,
    Timings,
    is_login_url,
)
from .config import Config, extrair_channel_id
from .cookies import (
    CookieDecryptError,
    cifrar_cookies,
    decifrar_cookies,
    filtrar_sessao,
    impressao,
    key_id,
    normalizar_cookies,
)
from .numero import filtrar_numero, valor_aceito, valor_inteiro
from .saude import Saude
from .store import CounterStatusRow, Store, connect, iso_utc
from .vigia import MSG_PRESO, matar_chromium
from .worker import (
    MAX_COOKIES,
    MSG_BROWSER,
    CODE_NO_ACCESS,
    MSG_COUNTER,
    MSG_DB,
    MSG_DECRYPT,
    MSG_INVALID,
    MSG_LOGIN,
    MSG_NETWORK,
    MSG_NO_ACCESS,
    MSG_NO_COOKIES,
    MSG_NO_PERMISSION,
    RENEW_MIN_INTERVAL,
    _epoch,
)

log = logging.getLogger("worker_inscritos.pool")

BACKOFF_MAX = 600  # D51: o backoff dobra ate 10 min
MSG_PAUSED = "Contador pausado"
MSG_LIMIT = "Limite de contadores ativos atingido"
CODE_LIMIT = "limit_reached"


@dataclass
class _Estado:
    """Estado em memoria de um contador de usuario."""

    state: str = "starting"
    auth_state: str | None = None
    code: str | None = None
    message: str | None = None
    failures: int = 0
    next_read_at: float = 0.0
    last_success_at: int | None = None
    last_attempt_at: int | None = None
    cookies_id: int | None = None  # conjunto de cookies visto/aplicado por ultimo
    loads: deque = field(default_factory=deque)  # instantes das cargas do Studio (loads_last_hour)
    seeded: bool = False
    override: str | None = None  # D54: canal escolhido visto por ultimo (mudou -> le ja, sem esperar o backoff)
    gravado: tuple | None = None  # ultimo estado gravado de pausa/sem cookies (so grava na mudanca)


@dataclass
class _Leitura:
    url: str
    raw: str | None
    sem_permissao: bool
    canal: tuple[str, str] | None
    escolhido: bool  # D54: o canal veio do override do dono (nao do redirect)
    cookies_atuais: list[dict[str, Any]]


class UserPool:
    def __init__(
        self,
        config: Config,
        private_key: PrivateKey,
        public_key: PublicKey,
        saude: Saude,
        *,
        clock: Callable[[], float] = time.time,
        timings: Timings | None = None,
        stop: threading.Event | None = None,
        auto: bool = True,
    ):
        self.config = config
        self.private_key = private_key
        self.public_key = public_key
        self.saude = saude
        self.clock = clock
        self.timings = timings or Timings()
        self.stop = stop or threading.Event()
        self.auto = auto
        self.size = config.user_pool_size
        self.hosts_sessao = tuple(h for h in _hosts(config) if h)
        # Observabilidade (testes e painel do mestre): contextos abertos/fechados e pico de paginas simultaneas.
        self.contexts_opened = 0
        self.contexts_closed = 0
        self.pages_open = 0
        self.max_pages_open = 0

        self._estados: dict[int, _Estado] = {}
        self._ativos: set[int] = set()
        self._em_voo: set[int] = set()
        self._tarefas: set[asyncio.Task] = set()
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pronto = threading.Event()
        self._erro_inicio: BaseException | None = None
        # Criados na thread do laco:
        self._store: Store | None = None
        self._sem: asyncio.Semaphore | None = None
        self._trava_navegador: asyncio.Lock | None = None  # leituras simultaneas nao lancam dois navegadores
        self._pw: Playwright | None = None
        self._browser: Browser | None = None

    # ------------------------------------------------------------------ ciclo de vida (chamado pela thread principal)
    @property
    def busy(self) -> int:
        """Leituras em andamento agora."""
        return len(self._em_voo)

    def start(self) -> None:
        """Sobe a thread do pool. Com `size = 0` nao sobe nada (contadores de usuario desligados)."""
        if self.size <= 0 or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._thread_main, name="pool-inscritos", daemon=True)
        self._thread.start()
        self._pronto.wait(30)
        if self._erro_inicio is not None:
            raise self._erro_inicio

    def tick(self) -> None:
        """Uma passada do agendador, esperando as leituras que ela iniciou (modo `auto=False`, usado pelos testes)."""
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self._tick_e_espera(), self._loop).result()

    def close(self) -> None:
        if self._thread is None or self._loop is None:
            return
        self.stop.set()
        try:
            asyncio.run_coroutine_threadsafe(self._encerrar(), self._loop).result(timeout=60)
        except Exception as exc:  # noqa: BLE001 - encerrando: so registra o tipo
            log.warning("[POOL] erro ao encerrar (%s)", type(exc).__name__)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=10)
        self._thread = None

    # ------------------------------------------------------------------ thread do laco
    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            self._store = Store(connect(self.config.db_path))
            self._sem = asyncio.Semaphore(self.size)
            self._trava_navegador = asyncio.Lock()
        except BaseException as exc:  # noqa: BLE001 - devolvido ao `start`
            self._erro_inicio = exc
            self._pronto.set()
            loop.close()
            return
        if self.auto:
            loop.create_task(self._laco())
        self._pronto.set()
        try:
            loop.run_forever()
        finally:
            try:
                self._store.close()
            finally:
                loop.close()

    async def _laco(self) -> None:
        while not self.stop.is_set():
            try:
                await self._tick()
            except sqlite3.Error as exc:
                log.error("[POOL] erro de banco no agendador (%s)", type(exc).__name__)
            await asyncio.sleep(1.0)

    async def _encerrar(self) -> None:
        for tarefa in list(self._tarefas):
            tarefa.cancel()
        if self._tarefas:
            await asyncio.gather(*self._tarefas, return_exceptions=True)
        await self._fechar_navegador()
        await asyncio.sleep(0.25)  # deixa os transportes do subprocesso do Playwright fecharem antes de fechar o laco
        self._pw = self._browser = None
        gc.collect()  # libera o transporte do subprocesso com o laco ainda aberto

    async def _fechar_navegador(self) -> None:
        for alvo in (self._browser,):
            if alvo is not None:
                try:
                    await alvo.close()
                except PlaywrightError as exc:
                    log.debug("[POOL] erro ao fechar o navegador (%s)", type(exc).__name__)
        self._browser = None
        if self._pw is not None:
            try:
                await self._pw.stop()
            except PlaywrightError as exc:
                log.debug("[POOL] erro ao parar o Playwright (%s)", type(exc).__name__)
            self._pw = None

    # ------------------------------------------------------------------ agendador
    async def _tick_e_espera(self) -> None:
        tarefas = await self._tick()
        if tarefas:
            await asyncio.gather(*tarefas)

    async def _tick(self) -> list[asyncio.Task]:
        """Classifica os contadores, grava pausas e inicia as leituras vencidas. Devolve as tarefas iniciadas."""
        store = self._store
        assert store is not None
        agora = self.clock()
        habilitados = []
        for info in store.user_counters():
            st = self._estados.setdefault(info.counter_id, _Estado())
            if not st.seeded:
                st.seeded = True
                st.auth_state = store.get_auth_state(info.counter_id)
            if not info.enabled:
                self._marcar(info.counter_id, st, "paused", MSG_PAUSED, None, None, agora)
            elif info.latest_cookie_id is None:
                st.cookies_id = None
                self._marcar(info.counter_id, st, "error", MSG_NO_COOKIES, "no_cookies", None, agora)
            else:
                habilitados.append((info, st))

        ativos = habilitados[: self.config.max_user_counters]
        for info, st in habilitados[self.config.max_user_counters :]:
            self._marcar(info.counter_id, st, "paused", MSG_LIMIT, st.auth_state, CODE_LIMIT, agora)
        self._ativos = {info.counter_id for info, _ in ativos}

        devidos: list[tuple[float, int]] = []
        for info, st in ativos:
            if st.cookies_id != info.latest_cookie_id:
                # Cookies novos (ou primeira vez): vale ler ja, sem esperar o backoff do conjunto anterior.
                if st.cookies_id is not None or st.state in ("paused", "error"):
                    st.failures, st.next_read_at = 0, 0.0
                st.cookies_id = info.latest_cookie_id
                st.gravado = None
            if st.override != info.channel_override:
                st.override = info.channel_override
                st.failures, st.next_read_at = 0, 0.0
            if st.state == "paused":  # voltou a ficar elegivel: le ja e deixa de aparecer como pausado
                st.state, st.gravado, st.next_read_at = "starting", None, 0.0
            if info.counter_id in self._em_voo:
                continue
            if st.next_read_at <= agora:
                devidos.append((st.next_read_at, info.counter_id))
        iniciadas: list[asyncio.Task] = []
        for _, cid in sorted(devidos):
            self._em_voo.add(cid)
            tarefa = asyncio.get_running_loop().create_task(self._ler(cid))
            self._tarefas.add(tarefa)
            tarefa.add_done_callback(self._tarefas.discard)
            iniciadas.append(tarefa)
        return iniciadas

    def _matar_navegador(self) -> None:
        try:
            matar_chromium()
        except Exception as exc:  # noqa: BLE001
            log.error("[BROWSER] nao foi possivel encerrar o navegador (%s)", type(exc).__name__)

    def _marcar(
        self, cid: int, st: _Estado, state: str, message: str, auth_state: str | None, code: str | None, agora: float
    ) -> None:
        """Grava pausa / sem cookies / limite uma vez (so na mudanca) e nunca abre o navegador."""
        chave = (state, message, auth_state, code)
        if st.gravado == chave:
            return
        st.state, st.message, st.code = state, message, code
        if auth_state is not None or state == "error":
            st.auth_state = auth_state
        row = self._linha(st, agora, proxima=None)
        row.state, row.auth_state = state, st.auth_state
        try:
            assert self._store is not None
            self._store.write_counter_status(cid, row)
            st.gravado = chave
        except sqlite3.Error as exc:
            log.error("[POOL] nao foi possivel gravar o status do contador (%s)", type(exc).__name__)

    def _linha(self, st: _Estado, agora: float, proxima: float | None) -> CounterStatusRow:
        while st.loads and agora - st.loads[0] > 3600:
            st.loads.popleft()
        return CounterStatusRow(
            state=st.state,
            auth_state=st.auth_state,
            last_success_at=st.last_success_at,
            last_attempt_at=st.last_attempt_at,
            next_read_at=None if proxima is None else int(proxima),
            consecutive_failures=st.failures,
            last_error_code=st.code,
            last_error_message=st.message,
            cookies_applied_id=st.cookies_id,
            loads_last_hour=len(st.loads),
        )

    # ------------------------------------------------------------------ uma leitura
    async def _ler(self, cid: int) -> None:
        st = self._estados[cid]
        try:
            assert self._sem is not None
            async with self._sem:
                if cid not in self._ativos:  # ficou inelegivel enquanto esperava a vez
                    return
                inicio = time.monotonic()
                comeco = self.clock()
                st.last_attempt_at = int(comeco)
                try:
                    await asyncio.wait_for(self._ler_e_gravar(cid, st, comeco, inicio), self.config.studio_hard_timeout)
                except asyncio.TimeoutError:  # D60: carga presa; o Chromium e morto e relancado na proxima leitura
                    log.error("[BROWSER] carga presa; navegador reiniciado")
                    self._matar_navegador()
                    self._falha(cid, st, comeco, inicio, "network_error", MSG_PRESO, "degraded")
                except sqlite3.Error as exc:
                    log.error("[POOL] erro de banco na leitura (%s)", type(exc).__name__)
                    self._falha(cid, st, comeco, inicio, "db_error", MSG_DB, "degraded")
                except PlaywrightError as exc:
                    code, msg = ("network_error", MSG_NETWORK) if _rede(exc) else ("browser_error", MSG_BROWSER)
                    log.warning("[POOL] erro do navegador (%s)", type(exc).__name__)
                    self._falha(cid, st, comeco, inicio, code, msg, "degraded")
        finally:
            self._em_voo.discard(cid)

    async def _ler_e_gravar(self, cid: int, st: _Estado, comeco: float, inicio: float) -> None:
        store = self._store
        assert store is not None
        set_id = store.latest_cookie_set_id(cid)
        row = None if set_id is None else store.get_cookie_set(cid, set_id)
        if row is None:
            st.cookies_id = None
            self._marcar(cid, st, "error", MSG_NO_COOKIES, "no_cookies", None, comeco)
            st.next_read_at = comeco + self.config.user_counter_interval
            return
        st.cookies_id = row.id
        try:
            if row.key_id != key_id(self.private_key):
                raise CookieDecryptError("outra chave")
            cookies = decifrar_cookies(row.ciphertext, self.private_key)
        except CookieDecryptError:
            self._falha(cid, st, comeco, inicio, "decrypt_failed", MSG_DECRYPT, "error", auth="decrypt_failed")
            return

        override = store.get_channel_override(cid)  # D54
        leitura = await self._abrir_e_ler(st, cookies, comeco, override)  # o contexto e fechado aqui dentro
        await self._aplicar(cid, st, row, cookies, leitura, comeco, inicio)

    async def _abrir_e_ler(
        self, st: _Estado, cookies: list[dict[str, Any]], comeco: float, override: str | None = None
    ) -> _Leitura:
        browser = await self._garantir_navegador()
        t = self.timings
        st.loads.append(comeco)
        contexto = await browser.new_context(user_agent=USER_AGENT, viewport=VIEWPORT)
        self.contexts_opened += 1
        try:
            contexto.set_default_timeout(t.nav_timeout * 1000)
            await contexto.add_init_script(INIT_SCRIPT)
            await self._injetar(contexto, cookies)
            pagina = await contexto.new_page()
            self.pages_open += 1
            self.max_pages_open = max(self.max_pages_open, self.pages_open)
            try:
                return await self._ler_pagina(contexto, pagina, override)
            finally:
                self.pages_open -= 1
        finally:
            try:
                await asyncio.wait_for(contexto.close(), 15)  # D60: fechar um contexto morto nao pode prender a leitura
            except (PlaywrightError, asyncio.TimeoutError) as exc:
                log.debug("[POOL] erro ao fechar o contexto (%s)", type(exc).__name__)
            self.contexts_closed += 1

    async def _ler_pagina(self, contexto: BrowserContext, pagina: Page, override: str | None = None) -> _Leitura:
        t = self.timings
        timeout_ms = t.nav_timeout * 1000
        canal: tuple[str, str] | None = None
        if override is not None:  # D54: sem o redirect da pagina inicial; abre o canal escolhido (nome) e o analytics dele
            await pagina.goto(f"{self.config.studio_home_url}channel/{override}", timeout=timeout_ms)
            await _esperar_carga(pagina, t)
            if not is_login_url(pagina.url):
                canal = (override, await _ler_nome_canal(pagina, t.ready_timeout * 1000 / 3))
                await pagina.goto(self.config.studio_url_for(override), timeout=timeout_ms)
                await _esperar_carga(pagina, t)
        else:
            await pagina.goto(self.config.studio_home_url, timeout=timeout_ms)
            await _esperar_carga(pagina, t)
            try:
                await pagina.wait_for_function(_JS_CANAL_PRONTO, timeout=t.ready_timeout * 1000)
            except PlaywrightTimeoutError:
                pass
            url = pagina.url
            canal_id = None if is_login_url(url) else extrair_channel_id(url)
            if canal_id is not None:  # D38: o redirect revelou o canal; segue para o analytics dele
                canal = (canal_id, await _ler_nome_canal(pagina, t.ready_timeout * 1000 / 3))
                await pagina.goto(self.config.studio_url_for(canal_id), timeout=timeout_ms)
                await _esperar_carga(pagina, t)
        await asyncio.sleep(t.after_studio)
        url_lida, bruto, sem_permissao = await pagina.evaluate(_JS_LER_PAGINA)
        raw = str(bruto) if bruto else None
        atuais: list[dict[str, Any]] = []
        if canal is not None and raw is not None and not is_login_url(str(url_lida)):
            atuais = filtrar_sessao(normalizar_cookies(await contexto.cookies()), self.hosts_sessao)
        return _Leitura(str(url_lida), raw, bool(sem_permissao) and not raw, canal, override is not None, atuais)

    async def _injetar(self, contexto: BrowserContext, cookies: list[dict[str, Any]]) -> None:
        if not cookies:
            return
        try:
            await contexto.add_cookies(cookies)  # type: ignore[arg-type]
            return
        except PlaywrightError as exc:
            log.warning("[POOL] add_cookies em lote falhou (%s); tentando um a um", type(exc).__name__)
        for cookie in cookies:
            try:
                await contexto.add_cookies([cookie])  # type: ignore[list-item]
            except PlaywrightError as exc:
                log.warning("[POOL] cookie ignorado (%s)", type(exc).__name__)

    async def _garantir_navegador(self) -> Browser:
        assert self._trava_navegador is not None
        async with self._trava_navegador:
            return await self._garantir_navegador_sem_trava()

    async def _garantir_navegador_sem_trava(self) -> Browser:
        if self._browser is not None and not self._browser.is_connected():
            log.warning("[POOL] navegador desconectado; reiniciando")
            await self._fechar_navegador()
        if self._browser is None:
            if self._pw is None:
                self._pw = await async_playwright().start()
            self._browser = await self._pw.chromium.launch(
                headless=True, channel="chromium", args=CHROMIUM_ARGS, ignore_default_args=IGNORE_DEFAULT_ARGS
            )
        return self._browser

    # ------------------------------------------------------------------ resultado
    async def _aplicar(
        self, cid: int, st: _Estado, row, cookies: list[dict[str, Any]], leitura: _Leitura, comeco: float, inicio: float
    ) -> None:
        if leitura.canal is None:
            if is_login_url(leitura.url):
                self._falha(cid, st, comeco, inicio, "login_required", MSG_LOGIN, "error", auth="login_required")
            else:
                self._falha(cid, st, comeco, inicio, "config_error", MSG_NO_PERMISSION, "error", auth="ok")
            return
        if is_login_url(leitura.url):
            self._falha(cid, st, comeco, inicio, "login_required", MSG_LOGIN, "error", auth="login_required")
            return
        if leitura.sem_permissao:
            if leitura.escolhido:  # D54
                self._falha(cid, st, comeco, inicio, CODE_NO_ACCESS, MSG_NO_ACCESS, "error", auth="ok")
            else:
                self._falha(cid, st, comeco, inicio, "config_error", MSG_NO_PERMISSION, "error", auth="ok")
            return
        if not leitura.raw:
            self._falha(cid, st, comeco, inicio, "counter_not_found", MSG_COUNTER, "degraded", auth="ok")
            return
        valor = filtrar_numero(leitura.raw)
        if not valor_aceito(valor):
            self._falha(cid, st, comeco, inicio, "invalid_value", MSG_INVALID, "degraded", auth="ok")
            return

        store = self._store
        assert store is not None
        canal_id, canal_nome = leitura.canal
        atual = store.get_channel_info(cid)
        if atual is None or atual[0] != canal_id:
            store.activate_channel(cid, canal_id, canal_nome, iso_utc(comeco))  # D47: restaura o historico do canal
        elif atual[1] != canal_nome[:200] and canal_nome:
            store.save_channel_info(cid, canal_id, canal_nome, iso_utc(comeco))
        voltou = st.failures > 0 or st.auth_state in ("login_required", "decrypt_failed")
        st.state, st.code, st.message, st.auth_state = "ok", None, None, "ok"
        st.failures = 0
        anterior = st.last_success_at
        st.last_success_at = int(comeco)
        proxima = comeco + self.config.user_counter_interval
        try:
            with store.transaction():
                store.save_reading(cid, valor, leitura.raw, iso_utc(comeco))
                store.write_counter_status(cid, self._linha(st, comeco, proxima))
        except sqlite3.Error:
            st.last_success_at = anterior
            raise
        st.next_read_at = proxima
        st.gravado = None
        inteiro = valor_inteiro(leitura.raw)
        if inteiro is not None:
            try:
                store.save_subscriber_sample(cid, canal_id, int(comeco), inteiro)
            except sqlite3.Error as exc:
                log.warning("[POOL] nao foi possivel gravar a amostra (%s)", type(exc).__name__)
        self._registrar_saude(inicio, True)
        if voltou:
            self._evento("ok", "recovered", f"Contador {cid}: inscritos voltou a ser lido", comeco)
        self._maybe_renew(cid, row, cookies, leitura.cookies_atuais, comeco)

    def _maybe_renew(
        self, cid: int, row, injetados: list[dict[str, Any]], atuais: list[dict[str, Any]], agora: float
    ) -> None:
        """D37 por contador: depois de leitura VALIDA, grava a sessao renovada se mudou e passaram 10 min."""
        store = self._store
        assert store is not None
        ultima = _epoch(row.created_at) if row.source == "worker" else None
        if ultima is not None and agora - ultima < RENEW_MIN_INTERVAL:
            return
        if not atuais or len(atuais) > MAX_COOKIES:
            return
        if impressao(atuais) == impressao(filtrar_sessao(injetados, self.hosts_sessao)):
            return
        try:
            novo = store.renew_cookie_set(
                cid, cifrar_cookies(atuais, self.public_key), key_id(self.private_key), len(atuais), row.id, iso_utc(agora)
            )
        except sqlite3.Error as exc:
            log.error("[POOL] nao foi possivel gravar a sessao renovada (%s)", type(exc).__name__)
            return
        if novo is None:
            return  # o dono colou cookies novos nesse meio-tempo
        st = self._estados[cid]
        st.cookies_id = novo
        self._evento("info", "session_renewed", f"Contador {cid}: sessao do YouTube Studio renovada", agora)

    def _falha(
        self,
        cid: int,
        st: _Estado,
        comeco: float,
        inicio: float,
        code: str,
        message: str,
        state: str,
        auth: str | None = None,
    ) -> None:
        """Falha de UM contador: backoff proprio (dobra ate 10 min); evento so na mudanca de auth_state."""
        st.failures += 1
        if code in ("network_error", "browser_error") and st.failures >= 3:
            code, message = "network_error", MSG_PRESO  # D60: estado honesto no painel
        mudou_auth = auth in ("login_required", "decrypt_failed") and st.auth_state != auth
        if auth is not None:
            st.auth_state = auth
        st.state, st.code, st.message = state, code, message
        base = self.config.user_counter_interval
        espera = max(base, min(BACKOFF_MAX, base * 2**st.failures))
        st.next_read_at = comeco + espera
        st.gravado = None
        try:
            assert self._store is not None
            self._store.write_counter_status(cid, self._linha(st, comeco, st.next_read_at))
        except sqlite3.Error as exc:
            log.error("[POOL] nao foi possivel gravar o status do contador (%s)", type(exc).__name__)
        self._registrar_saude(inicio, False)
        if mudou_auth:
            texto = f"Contador {cid}: login perdido no YouTube Studio" if auth == "login_required" else f"Contador {cid}: cookies nao puderam ser decifrados"
            self._evento("bad", auth, texto, comeco)

    def _registrar_saude(self, inicio: float, ok: bool) -> None:
        assert self._store is not None
        self.saude.registrar(self._store, max(0, round((time.monotonic() - inicio) * 1000)), ok)

    def _evento(self, level: str, code: str, message: str, agora: float) -> None:
        try:
            assert self._store is not None
            self._store.add_event(int(agora), level, code, message)
        except sqlite3.Error as exc:
            log.warning("[POOL] nao foi possivel gravar o evento (%s)", type(exc).__name__)


# ---------------------------------------------------------------------- auxiliares
def _hosts(config: Config) -> list[str | None]:
    from urllib.parse import urlsplit

    return [urlsplit(config.www_base_url).hostname, urlsplit(config.studio_base_url).hostname]


def _rede(exc: PlaywrightError) -> bool:
    return isinstance(exc, PlaywrightTimeoutError) or "net::" in str(exc)


async def _esperar_carga(pagina: Page, t: Timings) -> None:
    try:
        await pagina.wait_for_function("document.readyState === 'complete'", timeout=t.ready_timeout * 1000)
    except PlaywrightTimeoutError:
        log.warning("[POOL] timeout em readyState, continuando...")


async def _ler_nome_canal(pagina: Page, timeout_ms: float) -> str:
    try:
        await pagina.wait_for_function(f"({_JS_NOME_CANAL})() !== null", timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return ""
    nome = await pagina.evaluate(_JS_NOME_CANAL)
    return str(nome).strip()[:200] if nome else ""
