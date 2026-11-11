"""Entrega: junta o que os conectores produzem e leva aos clientes SSE de cada conjunto (D57).

* Implementa `ChatSink`: recebe mensagem normalizada, apagamento e limpeza de um `(plataforma, ref)` e os encaminha aos
  conjuntos que usam essa conexao (tabela de rotas montada pelo gerente a partir das regras de `rules.py`).
* Por conjunto: filtros (`ignored_users`, `hide_commands`) e o limitador de ritmo. Apagamentos nunca esperam nem sao
  descartados: vao na hora e ainda tiram da fila a mensagem que ainda nao saiu.
* Nada e guardado: a mensagem vive na fila do limitador (no maximo 2 x `max_rate`) e nas filas dos clientes, e some.
* Clientes lentos (fila cheia) sao desconectados; o `EventSource` do overlay reconecta sozinho.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from worker_chat.clock import Clock
from worker_chat.limiter import RateLimiter
from worker_chat.model import PLATFORM_CODES, ChatMessage
from worker_chat.rules import CLOSED_GRACE_SECONDS, ConnKey, Snapshot

CLIENT_QUEUE = 500  # eventos pendentes por cliente SSE antes de ele ser considerado lento


def encode_event(event: str, data: object) -> bytes:
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {body}\n\n".encode()


class Subscriber:
    """Um cliente SSE. Recebe eventos ja codificados; `None` na fila significa "encerre a resposta"."""

    def __init__(self, set_id: int, size: int = CLIENT_QUEUE) -> None:
        self.set_id = set_id
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=size)
        self.closed = False

    def push(self, data: bytes) -> bool:
        if self.closed:
            return True
        try:
            self.queue.put_nowait(data)
        except asyncio.QueueFull:
            return False
        return True

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.queue.full():
            self.queue.get_nowait()
        self.queue.put_nowait(None)


@dataclass
class HubStats:
    delivered: int = 0  # mensagens entregues as filas dos clientes (por conjunto)
    filtered: int = 0  # descartadas por ignored_users / hide_commands
    dropped: int = 0  # descartadas pelo limitador (fila cheia)
    deletions: int = 0  # eventos del/clear entregues
    slow_clients: int = 0  # clientes desconectados por fila cheia


@dataclass
class _SetState:
    limiter: RateLimiter
    subs: set[Subscriber] = field(default_factory=set)


class Hub:
    def __init__(self, clock: Clock, max_clients: int, on_activity: Callable[[], None] | None = None) -> None:
        self._clock = clock
        self.max_clients = max_clients
        self._on_activity = on_activity  # chamado quando o 1o cliente de um conjunto chega (o gerente reavalia na hora)
        self._snapshot = Snapshot()
        self._servable: frozenset[int] = frozenset()
        self._routes: dict[ConnKey, frozenset[int]] = {}
        self._state: dict[int, _SetState] = {}
        self._closed_at: dict[int, float] = {}
        self._clients = 0
        self._wake = asyncio.Event()
        self.stats = HubStats()

    # ---------------------------------------------------------------------------------- configuracao
    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    def servable(self, set_id: int) -> bool:
        return set_id in self._servable

    def apply(self, snapshot: Snapshot, routes: dict[ConnKey, frozenset[int]]) -> None:
        """Nova configuracao: `config` para quem mudou, fecha os clientes de conjunto que deixou de ser atendido."""
        antigo = self._snapshot.sets
        servable = snapshot.servable()
        for set_id in list(self._state):
            if set_id not in servable:
                for sub in list(self._state[set_id].subs):
                    sub.close()
                if not self._state[set_id].subs:
                    del self._state[set_id]
        for set_id in servable:
            cfg = snapshot.sets[set_id]
            before = antigo.get(set_id)
            st = self._state.get(set_id)
            if st is not None:
                if st.limiter.max_rate != cfg.max_rate:
                    st.limiter.set_rate(cfg.max_rate, self._clock.monotonic())
                if before is not None and before.version != cfg.version:
                    self._publish(set_id, encode_event("config", {}))
        self._snapshot = snapshot
        self._servable = servable
        self._routes = dict(routes)

    # ---------------------------------------------------------------------------------------- clientes
    @property
    def client_count(self) -> int:
        return self._clients

    def at_capacity(self) -> bool:
        return self._clients >= self.max_clients

    def subscribe(self, set_id: int) -> Subscriber:
        cfg = self._snapshot.sets[set_id]
        st = self._state.get(set_id)
        if st is None:
            st = self._state[set_id] = _SetState(RateLimiter(cfg.max_rate, self._clock.monotonic()))
        first = not st.subs
        sub = Subscriber(set_id)
        st.subs.add(sub)
        self._clients += 1
        self._closed_at.pop(set_id, None)
        if first and self._on_activity is not None:
            self._on_activity()
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        st = self._state.get(sub.set_id)
        if st is None or sub not in st.subs:
            return
        st.subs.discard(sub)
        self._clients -= 1
        if not st.subs:
            self._closed_at[sub.set_id] = self._clock.monotonic()
            if sub.set_id not in self._servable:
                del self._state[sub.set_id]

    def close_all(self) -> None:
        """Encerra a resposta de todos os clientes (parada do worker)."""
        for st in self._state.values():
            for sub in list(st.subs):
                sub.close()

    def active_sets(self, now: float | None = None) -> frozenset[int]:
        """Conjuntos com overlay aberto, ou fechado ha menos de 60 s (D57)."""
        agora = self._clock.monotonic() if now is None else now
        for set_id, since in list(self._closed_at.items()):
            if agora - since >= CLOSED_GRACE_SECONDS:
                del self._closed_at[set_id]
        abertos = {sid for sid, st in self._state.items() if st.subs}
        return frozenset(abertos | set(self._closed_at))

    # ----------------------------------------------------------------------------------- ChatSink
    def message(self, platform: str, ref: str, msg: ChatMessage) -> None:
        for set_id in self._routes.get((platform, ref), ()):
            st = self._state.get(set_id)
            cfg = self._snapshot.sets.get(set_id)
            if st is None or cfg is None or not st.subs:
                continue
            if msg.login in cfg.ignored or msg.name.lower() in cfg.ignored or (cfg.hide_commands and msg.starts_with_command):
                self.stats.filtered += 1
                continue
            before = st.limiter.dropped
            st.limiter.offer(msg)
            self.stats.dropped += st.limiter.dropped - before
        self._wake.set()

    def delete(self, platform: str, ref: str, ids: tuple[str, ...]) -> None:
        for set_id in self._routes.get((platform, ref), ()):
            st = self._state.get(set_id)
            if st is None or not st.subs:
                continue
            st.limiter.purge_ids(set(ids))
            self._publish(set_id, encode_event("del", {"ids": list(ids)}))
            self.stats.deletions += 1

    def clear(self, platform: str, ref: str, user: str | None) -> None:
        code = PLATFORM_CODES[platform]
        for set_id in self._routes.get((platform, ref), ()):
            st = self._state.get(set_id)
            if st is None or not st.subs:
                continue
            st.limiter.purge_user(code, user)
            self._publish(set_id, encode_event("clear", {"p": code, "user": user}))
            self.stats.deletions += 1

    # ---------------------------------------------------------------------------------------- saida
    def flush(self, now: float) -> float | None:
        """Entrega o que o limitador libera agora; devolve em quantos segundos olhar de novo (None = nada na fila)."""
        proximo: float | None = None
        for set_id, st in list(self._state.items()):
            for msg in st.limiter.pop_ready(now):
                self._publish(set_id, encode_event("msg", msg.to_dict()))
                self.stats.delivered += 1
            espera = st.limiter.next_delay(now)
            if espera is not None:
                proximo = espera if proximo is None else min(proximo, espera)
        return proximo

    async def run_pump(self) -> None:
        while True:
            self._wake.clear()
            espera = self.flush(self._clock.monotonic())
            try:
                await asyncio.wait_for(self._wake.wait(), None if espera is None else max(espera, 0.001))
            except TimeoutError:
                continue

    def _publish(self, set_id: int, data: bytes) -> None:
        st = self._state.get(set_id)
        if st is None:
            return
        for sub in list(st.subs):
            if not sub.push(data):
                self.stats.slow_clients += 1
                sub.close()
