"""Ciclo do worker-inscritos: troca de cookies, leitura do contador, gravacao e status.

Portado de `main()` do scraper original, com estas diferencas (contrato):
- cookies vem de `cookie_sets` (cifrados) e a troca e detectada por `cookie_sets.id`, nao por `mtime` (F6);
- a leitura vai para `subscriber_reading` (linha unica) e o estado para `worker_status` (D14/D20);
- erros viram codigos de enum fechado + mensagem constante: nunca texto de excecao, URL ou cookie.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Callable

from nacl.public import PrivateKey
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from . import __version__
from .browser import BrowserSession, Timings, is_login_url
from .config import Config
from .cookies import CookieDecryptError, decifrar_cookies, key_id
from .numero import filtrar_numero, valor_aceito
from .store import StatusRow, Store, connect, iso_utc

log = logging.getLogger("worker_inscritos")

MAX_FALHAS = 30  # leituras ruins seguidas ate recarregar a pagina (igual ao original)

# Mensagens constantes (<= 200 caracteres, sem dados variaveis).
MSG_NO_COOKIES = "Nenhum cookie configurado"
MSG_LOGIN = "Login perdido no YouTube Studio: atualize os cookies no painel"
MSG_DECRYPT = "Os cookies nao puderam ser decifrados (chave diferente ou dado corrompido)"
MSG_NO_PERMISSION = (
    "Sem permissao para este canal no YouTube Studio: confira o YOUTUBE_CHANNEL_ID "
    "e se a conta dos cookies tem acesso"
)
MSG_COUNTER = "Contador nao encontrado na pagina"
MSG_INVALID = "Valor invalido lido na pagina"
MSG_BROWSER = "Erro no navegador"
MSG_NETWORK = "Falha de rede ao abrir o YouTube"
MSG_DB = "Erro ao acessar o banco de dados"


class Worker:
    def __init__(
        self,
        config: Config,
        private_key: PrivateKey,
        store: Store,
        session: BrowserSession,
        clock: Callable[[], float] = time.time,
        stop: threading.Event | None = None,
    ):
        self.config = config
        self.private_key = private_key
        self.store = store
        self.session = session
        self.clock = clock
        self.stop = stop or threading.Event()

        self.applied_id: int | None = None  # cookie_sets.id aplicado no navegador
        self.failed_id: int | None = None  # cookie_sets.id que nao pode ser decifrado
        self.falhas = 0  # leituras ruins desde o ultimo sucesso/recarga (dispara a recarga)
        self.consecutive_failures = 0  # idem, mas so zera no sucesso (vai ao worker_status)
        self.last_value: str | None = None
        self.last_success_at: int | None = None

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
            last_error_code=self.error_code,
            last_error_message=self.error_message,
            auth_state=self.auth_state,
            cookies_applied_id=self.applied_id,
            version=__version__,
        )

    def publish(self) -> None:
        """Grava o heartbeat/estado atual. Falha de banco e registrada e nao derruba o worker."""
        try:
            self.store.write_status(self._status_row())
        except sqlite3.Error as exc:
            log.error("[DB] nao foi possivel gravar worker_status (%s)", type(exc).__name__)

    def _set(self, state: str, code: str | None, message: str | None, auth_state: str | None = None) -> None:
        self.state = state
        self.error_code = code
        self.error_message = message
        if auth_state is not None:
            self.auth_state = auth_state

    def _read_failure(self, code: str, message: str, auth_state: str = "ok") -> None:
        self.falhas += 1
        self.consecutive_failures += 1
        self._set("degraded", code, message, auth_state)
        self.publish()

    # ------------------------------------------------------------------ cookies / carga
    def _apply_cookie_set(self, set_id: int) -> bool:
        """Decifra o conjunto `set_id`, reinjeta no navegador e recarrega o Studio. False se nao deu."""
        row = self.store.get_cookie_set(set_id)
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
        self.applied_id = set_id
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
            self._apply_cookie_set(self.applied_id)
        self.falhas = 0  # como no original, zera mesmo se a recarga falhar

    # ------------------------------------------------------------------ um ciclo
    def step(self) -> None:
        try:
            self._step()
        except sqlite3.Error as exc:
            log.error("[DB] erro no ciclo (%s)", type(exc).__name__)
            self.consecutive_failures += 1
            self._set("degraded", "db_error", MSG_DB)
            self.publish()

    def _step(self) -> None:
        set_id = self.store.latest_cookie_set_id()
        if set_id is None:
            self.applied_id = None
            self.failed_id = None
            self._set("error", None, MSG_NO_COOKIES, "no_cookies")
            self.publish()
            log.warning("[COOKIES] nenhum conjunto de cookies no banco")
            return

        if set_id == self.failed_id:
            self.publish()  # mesmo conjunto que ja falhou: o estado de erro segue valendo
            return
        if set_id != self.applied_id:
            log.info("[COOKIES] conjunto novo - recarregando pagina...")
            if not self._apply_cookie_set(set_id):
                return

        self._read_and_store()

        if self.falhas >= MAX_FALHAS:
            self._reload()

    def _read_and_store(self) -> None:
        try:
            url, raw, sem_permissao = self.session.read_page()
        except PlaywrightError as exc:
            log.warning("[!] Erro ao ler a pagina (%s)", type(exc).__name__)
            self._read_failure("browser_error", MSG_BROWSER)
            return

        # A URL vem da mesma pagina de onde o texto foi lido: nada lido numa pagina de login e gravado.
        if is_login_url(url):
            log.error("[AUTH] *** REDIRECIONADO PARA LOGIN - atualize os cookies no painel admin! ***")
            self.falhas += 1
            self.consecutive_failures += 1
            self._set("error", "login_required", MSG_LOGIN, "login_required")
            self.publish()
            return

        if sem_permissao:
            log.warning("[AUTH] Sem permissao para este canal no YouTube Studio (confira YOUTUBE_CHANNEL_ID e a conta dos cookies)")
            self.falhas += 1
            self.consecutive_failures += 1
            self._set("error", "config_error", MSG_NO_PERMISSION, "ok")
            self.publish()
            return

        if not raw:
            log.warning("[!] Contador nao encontrado na pagina")
            self._read_failure("counter_not_found", MSG_COUNTER)
            return

        valor = filtrar_numero(raw)
        if not valor_aceito(valor):
            log.warning("[!] Valor invalido: '%s'", raw[:40])
            self._read_failure("invalid_value", MSG_INVALID)
            return

        agora = self.clock()
        anterior = self.last_success_at
        self.last_success_at = int(agora)
        self.consecutive_failures = 0
        self.falhas = 0
        self._set("ok", None, None, "ok")
        try:
            with self.store.transaction():
                self.store.save_reading(valor, raw, iso_utc(agora))
                self.store.write_status(self._status_row())
        except sqlite3.Error:
            self.last_success_at = anterior
            raise
        if valor != self.last_value:
            log.info("[MUDANCA] %s -> %s", self.last_value, valor)
            self.last_value = valor

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


def _is_network_error(exc: PlaywrightError) -> bool:
    return isinstance(exc, PlaywrightTimeoutError) or "net::" in str(exc)


def criar_worker(
    config: Config,
    private_key: PrivateKey,
    *,
    stop: threading.Event | None = None,
    timings: Timings | None = None,
    clock: Callable[[], float] = time.time,
) -> Worker:
    """Monta o worker (conexao ao banco + navegador ainda nao lancado). Usado por `__main__`.

    `timings` e `clock` sao pontos de injecao (esperas do fluxo de carga e relogio); os padroes sao os de producao.
    """
    stop = stop or threading.Event()
    store = Store(connect(config.db_path))
    session = BrowserSession(
        config.www_base_url,
        config.studio_url,
        timings or Timings(),
        stop=stop,
        debug_dir=config.debug_dir if config.debug_screenshot else None,
    )
    return Worker(config, private_key, store, session, clock=clock, stop=stop)
