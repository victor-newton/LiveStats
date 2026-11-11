"""Limitador de ritmo por conjunto (D57): balde de fichas de `max_rate` mensagens por segundo, com fila de espera curta.

* O balde repoe `max_rate` fichas por segundo e guarda no maximo `BURST` (1): a saida e espacada e em qualquer janela de
  1 s nunca passam mais de `max_rate` mensagens.
* A fila guarda ate 2 x `max_rate` mensagens. Dono, moderador, VIP, inscrito e membro entram na frente das comuns.
  Quando a fila enche, sai a mensagem COMUM mais antiga (so sai uma prioritaria se nao houver nenhuma comum).
* Apagamentos (`del`/`clear`) nunca passam por aqui: o `Hub` os entrega na hora (e tira da fila o que ainda nao saiu).
"""

from __future__ import annotations

from collections import deque

from worker_chat.model import ChatMessage, normalize_uid

BURST = 1.0
_EPS = 1e-9


class RateLimiter:
    def __init__(self, max_rate: int, now: float, burst: float = BURST) -> None:
        if max_rate < 1:
            raise ValueError("max_rate deve ser >= 1")
        self._rate = float(max_rate)
        self._burst = burst
        self._tokens = burst
        self._last = now
        self._priority: deque[ChatMessage] = deque()
        self._common: deque[ChatMessage] = deque()
        self.dropped = 0  # mensagens descartadas por fila cheia (contagem para o log)

    @property
    def max_rate(self) -> int:
        return int(self._rate)

    @property
    def capacity(self) -> int:
        return 2 * int(self._rate)

    def __len__(self) -> int:
        return len(self._priority) + len(self._common)

    def set_rate(self, max_rate: int, now: float) -> None:
        """Nova taxa (a configuracao mudou); a fila que passar do novo tamanho perde as comuns mais antigas."""
        if max_rate < 1:
            raise ValueError("max_rate deve ser >= 1")
        self._refill(now)
        self._rate = float(max_rate)
        self._trim()

    def offer(self, msg: ChatMessage) -> None:
        (self._priority if msg.priority else self._common).append(msg)
        self._trim()

    def _trim(self) -> None:
        while len(self) > self.capacity:
            if self._common:
                self._common.popleft()
            else:
                self._priority.popleft()
            self.dropped += 1

    def pop_ready(self, now: float) -> list[ChatMessage]:
        """Mensagens que podem sair agora, prioritarias primeiro (cada uma gasta uma ficha)."""
        self._refill(now)
        out: list[ChatMessage] = []
        while self._tokens >= 1.0 - _EPS and len(self):
            queue = self._priority or self._common
            out.append(queue.popleft())
            self._tokens -= 1.0
        return out

    def next_delay(self, now: float) -> float | None:
        """Segundos ate a proxima saida; 0 se ja pode sair; None se a fila esta vazia."""
        if not len(self):
            return None
        self._refill(now)
        if self._tokens >= 1.0 - _EPS:
            return 0.0
        return (1.0 - self._tokens) / self._rate

    def purge_ids(self, ids: set[str]) -> int:
        """Tira da fila as mensagens apagadas na plataforma antes de aparecerem."""
        return self._purge(lambda m: m.id in ids)

    def purge_user(self, platform_code: str, user: str | None) -> int:
        """`clear` de um usuario (ou de tudo, com `user=None`) da plataforma."""
        if user is None:
            return self._purge(lambda m: m.p == platform_code)
        alvo = normalize_uid(user)
        return self._purge(lambda m: m.p == platform_code and bool(alvo) and m.uid == alvo)

    def _purge(self, match) -> int:
        antes = len(self)
        self._priority = deque(m for m in self._priority if not match(m))
        self._common = deque(m for m in self._common if not match(m))
        return antes - len(self)

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self._last)
        self._last = max(self._last, now)
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
