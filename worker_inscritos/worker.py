"""Ciclo do worker-inscritos: troca de cookies, leitura do contador, gravacao e status.

Portado de `main()` do scraper original, com estas diferencas (contrato):
- cookies vem de `cookie_sets` (cifrados) e a troca e detectada por `cookie_sets.id`, nao por `mtime` (F6);
- a leitura vai para `subscriber_reading` (uma linha por contador) e o estado para `worker_status` e `counter_status` (D14/D20/D51);
- este modulo e o contador do MESTRE (pagina persistente); os contadores de usuario ficam em `pool.py` (D51);
- erros viram codigos de enum fechado + mensagem constante: nunca texto de excecao, URL ou cookie.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime, timezone
from urllib.parse import urlsplit

from nacl.public import PrivateKey, PublicKey
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import __version__, memoria
from .browser import BrowserSession, Timings, is_login_url
from .config import Config
from .cookies import (
    CookieDecryptError,
    cifrar_cookies,
    decifrar_cookies,
    filtrar_sessao,
    impressao,
    key_id,
)
from .numero import filtrar_numero, valor_aceito, valor_inteiro
from .saude import Saude
from .store import CounterStatusRow, StatusRow, Store, connect, iso_utc

log = logging.getLogger("worker_inscritos")

RENEW_MIN_INTERVAL = 600  # D37: no minimo 10 min entre renovacoes gravadas
MAX_COOKIES = 200  # cookie_sets.cookie_count BETWEEN 1 AND 200
MAX_FALHAS = 30  # leituras ruins seguidas ate recarregar a pagina (igual ao original)

# Mensagens constantes (<= 200 caracteres, sem dados variaveis).
MSG_NO_COOKIES = "Nenhum cookie configurado"
MSG_LOGIN = "Login perdido no YouTube Studio: atualize os cookies no painel"
MSG_DECRYPT = "Os cookies nao puderam ser decifrados (chave diferente ou dado corrompido)"
MSG_NO_PERMISSION = (
    "Sem permissao para este canal no YouTube Studio: confira se a conta dos cookies tem acesso"
)
CODE_NO_ACCESS = "channel_no_access"  # D54: so em counter_status (worker_status guarda o equivalente fechado, config_error)
MSG_NO_ACCESS = "A conta dos cookies nao tem acesso a este canal"
MSG_COUNTER = "Contador nao encontrado na pagina"
MSG_INVALID = "Valor invalido lido na pagina"
MSG_BROWSER = "Erro no navegador"
MSG_NETWORK = "Falha de rede ao abrir o YouTube"
MSG_DB = "Erro ao acessar o banco de dados"
MSG_EV_LOGIN = "Login perdido no YouTube Studio: atualize os cookies"
MSG_EV_VOLTOU = "Contador de inscritos voltou a ser lido"
MSG_EV_RENOVADA = "Sessao do YouTube Studio renovada"


class Worker:
    def __init__(
        self,
        config: Config,
        private_key: PrivateKey,
        public_key: PublicKey,
        store: Store,
        session: BrowserSession,
        clock: Callable[[], float] = time.time,
        stop: threading.Event | None = None,
        saude: Saude | None = None,
        counter_id: int | None = None,
    ):
        self.config = config
        self.private_key = private_key
        self.public_key = public_key
        self.store = store
        self.session = session
        self.clock = clock
        self.stop = stop or threading.Event()
        self.saude = saude or Saude(clock)
        if counter_id is None:  # D51: o contador do dono com papel master (id 1)
            try:
                counter_id = store.master_counter_id()
            except sqlite3.Error:
                counter_id = 1
        self.counter_id = counter_id
        self._cargas: deque[float] = deque()  # D51: instantes das aberturas do Studio (loads_last_hour)

        self.channel: tuple[str, str] | None = None  # D38: (id, nome) ja gravados em channel_info
        self._channel_reset_pending = False
        self.applied_id: int | None = None  # cookie_sets.id aplicado no navegador
        self.failed_id: int | None = None  # cookie_sets.id que nao pode ser decifrado
        self.applied_hash: str | None = None  # D37: hash (so em memoria) do conjunto aplicado/renovado
        self.last_renewal_at: float | None = None  # D37: instante da ultima renovacao gravada
        self.studio_stale = False  # D37: reabrir o Studio sem mexer nos cookies
        # Hosts-base configurados (D19) tambem contam como dominio da sessao; em producao ja sao do youtube.com.
        self.hosts_sessao = tuple(
            h for h in (urlsplit(config.www_base_url).hostname, urlsplit(config.studio_base_url).hostname) if h
        )
        self.falhas = 0  # leituras ruins desde o ultimo sucesso/recarga (dispara a recarga)
        self.consecutive_failures = 0  # idem, mas so zera no sucesso (vai ao worker_status)
        self.last_value: str | None = None
        self.last_success_at: int | None = None
        self.last_open_at: float | None = None  # D47: ultima abertura do Studio (carga ou reabertura)

        self._saude: dict[int, list[int]] = {}  # D43: minuto -> [leituras, soma_ms, max_ms, ok, erros]
        self.state = "starting"
        self.auth_state = "unknown"
        self.error_code: str | None = None
        self.error_message: str | None = None

    # ------------------------------------------------------------------ status
    def _status_row(self) -> StatusRow:
        return StatusRow(
            state=self.state,
            last_heartbeat_at=int(self.clock()),
            last_success_at=self.last_success_at,
            consecutive_failures=self.consecutive_failures,
            interval_seconds=self.config.scrape_interval,
            last_error_code="config_error" if self.error_code == CODE_NO_ACCESS else self.error_code,
            last_error_message=self.error_message,
            auth_state=self.auth_state,
            cookies_applied_id=self.applied_id,
            version=__version__,
            mem_bytes=memoria.container_memory_bytes(),
        )

    def _counter_row(self) -> CounterStatusRow:
        agora = self.clock()
        while self._cargas and agora - self._cargas[0] > 3600:
            self._cargas.popleft()
        return CounterStatusRow(
            state="paused" if self.state == "stopped" else self.state,
            auth_state=self.auth_state,
            last_success_at=self.last_success_at,
            last_attempt_at=int(agora),
            next_read_at=int(agora) + self.config.scrape_interval,
            consecutive_failures=self.consecutive_failures,
            last_error_code=self.error_code,
            last_error_message=self.error_message,
            cookies_applied_id=self.applied_id,
            loads_last_hour=len(self._cargas),
        )

    def _write_statuses(self) -> None:
        self.store.write_status(self._status_row())
        self.store.write_counter_status(self.counter_id, self._counter_row())

    def publish(self) -> None:
        """Grava o heartbeat geral e o estado do contador do mestre. Falha de banco e registrada e nao derruba o worker."""
        try:
            with self.store.transaction():
                self._write_statuses()
        except sqlite3.Error as exc:
            log.error("[DB] nao foi possivel gravar o status (%s)", type(exc).__name__)

    def _set(self, state: str, code: str | None, message: str | None, auth_state: str | None = None) -> None:
        self.state = state
        self.error_code = code
        self.error_message = message
        if auth_state is not None:
            self.auth_state = auth_state

    # ------------------------------------------------------------------ historico, saude e eventos (D43; melhor esforco)
    def _evento(self, level: str, code: str, message: str) -> None:
        """Evento so de mudanca. Falha de banco aqui nunca derruba nem invalida a leitura."""
        try:
            self.store.add_event(int(self.clock()), level, code, message)
        except sqlite3.Error as exc:
            log.warning("[DB] nao foi possivel gravar o evento (%s)", type(exc).__name__)

    def _saude_leitura(self, inicio: float, ok: bool) -> None:
        """Soma a leitura no agregado do minuto do worker inteiro (compartilhado com o pool)."""
        self.saude.registrar(self.store, max(0, round((time.monotonic() - inicio) * 1000)), ok)

    def _amostra(self, valor: str, raw: str, agora: float) -> None:
        """Persiste o valor inteiro por canal, inclusive contagens formatadas com separadores."""
        inteiro = valor_inteiro(raw)
        if inteiro is None or self.channel is None:
            return
        try:
            self.store.save_subscriber_sample(self.counter_id, self.channel[0], int(agora), inteiro)
        except sqlite3.Error as exc:
            log.warning("[DB] nao foi possivel gravar a amostra de inscritos (%s)", type(exc).__name__)

    def _read_failure(self, code: str, message: str, auth_state: str = "ok") -> None:
        self.falhas += 1
        self.consecutive_failures += 1
        self._set("degraded", code, message, auth_state)
        self.publish()

    # ------------------------------------------------------------------ cookies / carga
    def _apply_cookie_set(self, set_id: int) -> bool:
        """Decifra o conjunto `set_id`, reinjeta no navegador e recarrega o Studio. False se nao deu."""
        row = self.store.get_cookie_set(self.counter_id, set_id)
        if row is None:  # apagado por um upload novo entre a consulta do id e a leitura
            self.applied_id = None
            return False

        if row.key_id != key_id(self.private_key):
            log.error("[COOKIES] conjunto cifrado com outra chave; ignorando")
            self._decrypt_failed(set_id)
            return False
        try:
            cookies = decifrar_cookies(row.ciphertext, self.private_key)
        except CookieDecryptError as exc:
            log.error("[COOKIES] falha ao decifrar: %s", exc)
            self._decrypt_failed(set_id)
            return False
        self.failed_id = None
        log.info("[COOKIES] %d cookies carregados.", len(cookies))

        self.publish()  # heartbeat antes da carga, que pode levar dezenas de segundos
        self._channel_reset_pending = True
        try:
            if not self.session.started:
                self.session.start()
            self.session.load_page(cookies)
        except PlaywrightError as exc:
            self.applied_id = None  # tenta de novo no proximo ciclo
            code, msg = (
                ("network_error", MSG_NETWORK) if _is_network_error(exc) else ("browser_error", MSG_BROWSER)
            )
            log.error("[AUTH] erro ao carregar a pagina (%s)", type(exc).__name__)
            self.consecutive_failures += 1
            self._set("degraded", code, msg)
            self.publish()
            return False
        self._gravar_canal()  # sem identidade, o ciclo abaixo reporta login/permissao sem ler dado antigo
        self.last_open_at = self.clock()
        self._cargas.append(self.last_open_at)
        self.applied_id = set_id
        self.applied_hash = impressao(filtrar_sessao(cookies, self.hosts_sessao))
        self.last_renewal_at = _epoch(row.created_at) if row.source == "worker" else None
        self.studio_stale = False
        self.falhas = 0
        return True

    def _decrypt_failed(self, set_id: int) -> None:
        self.failed_id = set_id
        self.consecutive_failures += 1
        self._set("error", "decrypt_failed", MSG_DECRYPT, "decrypt_failed")
        self.publish()

    def _reload(self) -> None:
        log.info("[RELOAD] %d falhas - recarregando...", self.falhas)
        if self.applied_id is not None:
            self._reopen_studio(self.applied_id)
        self.falhas = 0  # como no original, zera mesmo se a recarga falhar

    def _recarga_periodica(self, set_id: int) -> None:
        """D47: o `yta-smooth-counter` do Studio so anima para cima; uma queda de inscritos (ou a troca para um canal
        com menos inscritos) nao aparece numa pagina ja aberta. Reabre o Studio a cada `reload_interval` s (cookies
        vivos, D37) e a leitura seguinte pega o numero real."""
        intervalo = self.config.reload_interval
        agora = self.clock()
        if self.last_open_at is None:
            self.last_open_at = agora
            return
        if intervalo <= 0 or agora - self.last_open_at < intervalo:
            return
        log.info("[RELOAD] %d s desde a ultima abertura - reabrindo o Studio para ler o valor real", intervalo)
        self._reopen_studio(set_id)

    def _reopen_studio(self, set_id: int) -> None:
        """D37: reabre o Studio mantendo os cookies VIVOS do contexto (nunca reinjeta o conjunto colado).

        So se o navegador caiu (contexto vazio) o conjunto mais recente do banco, que ja inclui as renovacoes,
        e injetado de novo.
        """
        self.publish()  # heartbeat antes da carga, que pode levar dezenas de segundos
        self.last_open_at = self.clock()
        self._cargas.append(self.last_open_at)
        try:
            if self.session.reload_studio():
                self.studio_stale = False
                self._gravar_canal()
                return
        except PlaywrightError as exc:
            log.error("[AUTH] erro ao recarregar a pagina (%s)", type(exc).__name__)
            self.consecutive_failures += 1
            self._set("degraded", *(("network_error", MSG_NETWORK) if _is_network_error(exc) else ("browser_error", MSG_BROWSER)))
            self.publish()
            return
        log.warning("[BROWSER] contexto recriado - reinjetando os cookies do banco")
        latest = self.store.latest_cookie_set_id(self.counter_id)
        self._apply_cookie_set(latest if latest is not None else set_id)

    # ------------------------------------------------------------------ um ciclo
    def step(self) -> None:
        try:
            self._step()
        except sqlite3.Error as exc:
            log.error("[DB] erro no ciclo (%s)", type(exc).__name__)
            self.consecutive_failures += 1
            self._set("degraded", "db_error", MSG_DB)
            self.publish()

    def _gravar_canal(self) -> bool:
        """Identifica primeiro o canal; só então limpa o estado anterior e restaura o histórico correspondente."""
        detectado = self.session.channel
        if detectado is None:
            return False
        mudou = detectado[0] != (self.channel[0] if self.channel else None)
        if not mudou and not self._channel_reset_pending:
            return True
        try:
            restaurado = self.store.activate_channel(self.counter_id, detectado[0], detectado[1], iso_utc(self.clock()))
        except sqlite3.Error as exc:
            log.error("[DB] nao foi possivel gravar o canal detectado (%s)", type(exc).__name__)
            return False
        self.channel = detectado
        self.last_value = None if restaurado is None else str(restaurado[1])
        self.last_success_at = None if restaurado is None else restaurado[0]
        self._channel_reset_pending = False
        log.info("[CANAL] %s; estado anterior limpo e historico do canal restaurado", "canal atualizado" if mudou else "cookies reaplicados")
        return True

    def _step(self) -> None:
        set_id = self.store.latest_cookie_set_id(self.counter_id)
        if set_id is None:
            self.applied_id = None
            self.failed_id = None
            self._set("error", None, MSG_NO_COOKIES, "no_cookies")
            self.publish()
            log.warning("[COOKIES] nenhum conjunto de cookies no banco")
            return

        override = self.store.get_channel_override(self.counter_id)
        if override != self.session.channel_override:  # D54: o dono escolheu (ou soltou) o canal: reabre o Studio nele
            self.session.channel_override = override
            if self.applied_id is not None:
                self.studio_stale = True

        if set_id == self.failed_id:
            self.publish()  # mesmo conjunto que ja falhou: o estado de erro segue valendo
            return
        if set_id != self.applied_id:
            log.info("[COOKIES] conjunto novo - recarregando pagina...")
            if not self._apply_cookie_set(set_id):
                return

        if self.studio_stale and self.applied_id is not None:
            self._reopen_studio(self.applied_id)
            if self.studio_stale or self.applied_id != set_id:  # falhou ou reinjetou outro conjunto: proximo ciclo
                return

        self._recarga_periodica(set_id)
        if self.studio_stale or self.applied_id != set_id:
            return

        if self.session.channel is None and is_login_url(self.session.current_url()):
            self._read_and_store()  # preserva o estado login_required e nunca grava a pagina de login
            return
        if self.session.channel is None or self.channel is None:
            self._set("degraded", "config_error", MSG_NO_PERMISSION, "ok")
            self.publish()
            return
        self._read_and_store()

        if self.falhas >= MAX_FALHAS:
            self._reload()

    def _read_and_store(self) -> None:
        inicio = time.monotonic()
        try:
            url, raw, sem_permissao = self.session.read_page()
        except PlaywrightError as exc:
            log.warning("[!] Erro ao ler a pagina (%s)", type(exc).__name__)
            self._saude_leitura(inicio, ok=False)
            self._read_failure("browser_error", MSG_BROWSER)
            return

        # A URL vem da mesma pagina de onde o texto foi lido: nada lido numa pagina de login e gravado.
        if is_login_url(url):
            log.error("[AUTH] *** REDIRECIONADO PARA LOGIN - atualize os cookies no painel admin! ***")
            self.falhas += 1
            self.consecutive_failures += 1
            self._saude_leitura(inicio, ok=False)
            if self.auth_state != "login_required":
                self._evento("bad", "login_required", MSG_EV_LOGIN)
            self._set("error", "login_required", MSG_LOGIN, "login_required")
            self.publish()
            return

        if sem_permissao:
            log.warning("[AUTH] Sem permissao para este canal no YouTube Studio (confira a conta dos cookies)")
            self.falhas += 1
            self.consecutive_failures += 1
            self._saude_leitura(inicio, ok=False)
            if self.session.channel_override is not None:  # D54: o canal foi escolhido pelo dono e a conta nao tem acesso a ele
                self._set("error", CODE_NO_ACCESS, MSG_NO_ACCESS, "ok")
            else:
                self._set("error", "config_error", MSG_NO_PERMISSION, "ok")
            self.publish()
            return

        if not raw:
            log.warning("[!] Contador nao encontrado na pagina")
            self._saude_leitura(inicio, ok=False)
            self._read_failure("counter_not_found", MSG_COUNTER)
            return

        valor = filtrar_numero(raw)
        if not valor_aceito(valor):
            log.warning("[!] Valor invalido: '%s'", raw[:40])
            self._saude_leitura(inicio, ok=False)
            self._read_failure("invalid_value", MSG_INVALID)
            return

        agora = self.clock()
        anterior = self.last_success_at
        voltou = self.consecutive_failures > 0
        self.last_success_at = int(agora)
        self.consecutive_failures = 0
        self.falhas = 0
        self._set("ok", None, None, "ok")
        try:
            with self.store.transaction():
                self.store.save_reading(self.counter_id, valor, raw, iso_utc(agora))
                self._write_statuses()
        except sqlite3.Error:
            self.last_success_at = anterior
            raise
        if valor != self.last_value:
            log.info("[MUDANCA] %s -> %s", self.last_value, valor)
            self.last_value = valor
        self._saude_leitura(inicio, ok=True)
        self._amostra(valor, raw, agora)
        if voltou:
            self._evento("ok", "recovered", MSG_EV_VOLTOU)
        self._maybe_renew()

    # ------------------------------------------------------------------ renovacao da sessao (D37)
    def _maybe_renew(self) -> None:
        """Depois de uma leitura VALIDA: se a sessao do Chromium mudou, grava a versao renovada (cifrada).

        Nunca chamada de pagina de login ou "sem permissao" (so `_read_and_store` apos valor aceito chega aqui).
        Falhas aqui nao invalidam a leitura ja gravada.
        """
        if self.applied_id is None or self.applied_hash is None:
            return
        agora = self.clock()
        if self.last_renewal_at is not None and agora - self.last_renewal_at < RENEW_MIN_INTERVAL:
            return
        try:
            atuais = self.session.export_cookies(self.hosts_sessao)
        except PlaywrightError as exc:
            log.warning("[COOKIES] nao foi possivel exportar a sessao (%s)", type(exc).__name__)
            return
        if not atuais or len(atuais) > MAX_COOKIES:
            return
        novo_hash = impressao(atuais)
        if novo_hash == self.applied_hash:
            return
        try:
            novo_id = self.store.renew_cookie_set(
                self.counter_id,
                cifrar_cookies(atuais, self.public_key),
                key_id(self.private_key),
                len(atuais),
                self.applied_id,
                iso_utc(agora),
            )
        except sqlite3.Error as exc:
            log.error("[DB] nao foi possivel gravar a sessao renovada (%s)", type(exc).__name__)
            return
        if novo_id is None:
            log.info("[COOKIES] renovacao descartada: ha um conjunto mais novo")
            return
        self.applied_id = novo_id
        self.applied_hash = novo_hash
        self.last_renewal_at = agora
        log.info("[COOKIES] sessao renovada: %d cookies", len(atuais))
        self._evento("info", "session_renewed", MSG_EV_RENOVADA)
        self.publish()

    # ------------------------------------------------------------------ laco
    def run(self) -> None:
        self.publish()
        try:
            while not self.stop.is_set():
                self.step()
                self.stop.wait(self.config.scrape_interval)
        finally:
            self.state = "stopped"
            self.publish()
            self.session.close()


def _epoch(iso: str) -> float | None:
    """Instante de `created_at` ('...Z', UTC); None se nao for legivel."""
    try:
        return datetime.fromisoformat(iso.removesuffix("Z")).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def _is_network_error(exc: PlaywrightError) -> bool:
    return isinstance(exc, PlaywrightTimeoutError) or "net::" in str(exc)


def criar_worker(
    config: Config,
    private_key: PrivateKey,
    public_key: PublicKey,
    *,
    stop: threading.Event | None = None,
    timings: Timings | None = None,
    clock: Callable[[], float] = time.time,
    saude: Saude | None = None,
) -> Worker:
    """Monta o worker (conexao ao banco + navegador ainda nao lancado). Usado por `__main__`.

    `timings` e `clock` sao pontos de injecao (esperas do fluxo de carga e relogio); os padroes sao os de producao.
    """
    stop = stop or threading.Event()
    store = Store(connect(config.db_path))
    session = BrowserSession(
        config.www_base_url,
        config.studio_home_url,
        config.studio_url_for,
        timings or Timings(),
        stop=stop,
        debug_dir=config.debug_dir if config.debug_screenshot else None,
    )
    return Worker(config, private_key, public_key, store, session, clock=clock, stop=stop, saude=saude)
