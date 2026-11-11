"""Regras puras de "quando conecta" (D57): sem rede, sem banco, sem relogio; recebem tudo por argumento.

Uma conexao por `(plataforma, ref)`, compartilhada entre conjuntos. Conecta so se, para algum conjunto:
  * `set_chat.enabled = 1`,
  * a live tem `chat_enabled = 1` e `live_stats.status = 'ao_vivo'`,
  * o conjunto esta ativo: tem overlay aberto ou o ultimo fechou ha menos de 60 s.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, field

CLOSED_GRACE_SECONDS = 60.0

ConnKey = tuple[str, str]  # (plataforma do banco, ref normalizado)


@dataclass(frozen=True)
class LiveInfo:
    id: int
    set_id: int
    platform: str  # youtube | twitch | kick
    ref: str
    chat_enabled: bool
    status: str  # '' quando ainda nao ha linha em live_stats


@dataclass(frozen=True)
class SetChat:
    """Linha de `set_chat` (so os campos que o worker usa) + `version`, que muda quando QUALQUER campo muda (evento `config`)."""

    set_id: int
    enabled: bool
    hide_commands: bool
    ignored: frozenset[str]
    max_rate: int
    version: tuple[object, ...]


@dataclass(frozen=True)
class Snapshot:
    sets: Mapping[int, SetChat] = field(default_factory=dict)  # so conjuntos com linha em `set_chat`
    lives: tuple[LiveInfo, ...] = ()

    def servable(self) -> frozenset[int]:
        """Conjuntos que o SSE atende: chat ligado e ao menos uma live no chat (ao vivo ou nao)."""
        com_live = {live.set_id for live in self.lives if live.chat_enabled}
        return frozenset(sid for sid, cfg in self.sets.items() if cfg.enabled and sid in com_live)


def normalize_ref(platform: str, ref: str) -> str:
    return ref.strip().lower() if platform in ("twitch", "kick") else ref.strip()


def wanted_connections(
    snapshot: Snapshot,
    active_sets: Collection[int],
    supported: Collection[str],
    max_total: int,
    current: Collection[ConnKey] = (),
    limits: Mapping[str, int] | None = None,
) -> tuple[dict[ConnKey, frozenset[int]], int]:
    """(conexoes desejadas -> conjuntos que as usam, quantas ficaram de fora pelos tetos).

    Tetos: `max_total` (todas as plataformas) e, opcionalmente, `limits[plataforma]` (ex.: abas do Chromium do YouTube).
    Acima de um teto, ficam as conexoes que ja existem e depois as de live mais antiga (menor id): estavel entre rodadas.
    """
    ativos = set(active_sets)
    users: dict[ConnKey, set[int]] = {}
    first_live: dict[ConnKey, int] = {}
    for live in snapshot.lives:
        cfg = snapshot.sets.get(live.set_id)
        if cfg is None or not cfg.enabled or live.set_id not in ativos:
            continue
        if not live.chat_enabled or live.status != "ao_vivo" or live.platform not in supported:
            continue
        key = (live.platform, normalize_ref(live.platform, live.ref))
        users.setdefault(key, set()).add(live.set_id)
        first_live[key] = min(first_live.get(key, live.id), live.id)
    ja = set(current)
    ordem = sorted(users, key=lambda k: (k not in ja, first_live[k], k))
    por_plataforma: dict[str, int] = {}
    elegiveis: list[ConnKey] = []
    for key in ordem:
        teto = (limits or {}).get(key[0])
        usadas = por_plataforma.get(key[0], 0)
        if teto is None or usadas < teto:
            por_plataforma[key[0]] = usadas + 1
            elegiveis.append(key)
    mantidas = elegiveis[:max_total]
    return {k: frozenset(users[k]) for k in mantidas}, len(ordem) - len(mantidas)
