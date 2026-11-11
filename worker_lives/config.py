"""Configuração do worker, lida do ambiente (D17, D39).

O worker-lives não usa mais a YouTube Data API nem segredo algum (D39): só conhece `DB_PATH`
(banco compartilhado), `LIVE_INTERVAL` e, opcionalmente, `KICK_BROWSER` (fallback com Chromium).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

DEFAULT_DB_PATH = "/data/livestats.db"
DEFAULT_INTERVAL = 10  # D42: cada live é lida a cada 10 s, em paralelo
MIN_INTERVAL = 5
DEFAULT_RETENTION_DAYS = 14
MAX_RETENTION_DAYS = 90
MAX_INTERVAL = 15  # acima disso o painel passaria de 15 s sem atualizar (D42)


class ConfigError(Exception):
    """Configuração inválida."""


@dataclass(frozen=True)
class Config:
    db_path: str
    interval: int
    kick_browser: bool = True  # fallback Playwright para o Kick quando o Cloudflare bloquear o httpx
    retention_days: int = 14  # D59: sessões encerradas há mais que isso saem do histórico

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Config:
        return cls(
            db_path=environ.get("DB_PATH") or DEFAULT_DB_PATH,
            interval=parse_interval(environ.get("LIVE_INTERVAL")),
            kick_browser=parse_flag(environ.get("KICK_BROWSER"), default=True),
            retention_days=parse_retention(environ.get("HISTORY_RETENTION_DAYS")),
        )


def parse_interval(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        return DEFAULT_INTERVAL
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError("LIVE_INTERVAL deve ser um número inteiro de segundos") from None
    if value < MIN_INTERVAL or value > MAX_INTERVAL:
        raise ConfigError(
            f"LIVE_INTERVAL={value} é inválido: use de {MIN_INTERVAL} a {MAX_INTERVAL} segundos "
            f"(o limite de {MAX_INTERVAL} s garante que nenhuma live fique mais que isso sem atualizar; padrão {DEFAULT_INTERVAL})"
        )
    return value


def parse_retention(raw: str | None) -> int:
    """D59: `HISTORY_RETENTION_DAYS` (1 a 90 dias, padrão 14); valor inválido derruba o worker na partida."""
    if raw is None or raw.strip() == "":
        return DEFAULT_RETENTION_DAYS
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError("HISTORY_RETENTION_DAYS deve ser um número inteiro de dias") from None
    if value < 1 or value > MAX_RETENTION_DAYS:
        raise ConfigError(f"HISTORY_RETENTION_DAYS={value} é inválido: use de 1 a {MAX_RETENTION_DAYS} dias (padrão {DEFAULT_RETENTION_DAYS})")
    return value


def parse_flag(raw: str | None, *, default: bool) -> bool:
    if raw is None or raw.strip() == "":
        return default
    valor = raw.strip().lower()
    if valor in ("1", "true", "sim", "yes"):
        return True
    if valor in ("0", "false", "nao", "não", "no"):
        return False
    raise ConfigError("KICK_BROWSER deve ser 1 ou 0")
