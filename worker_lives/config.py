"""Configuração do worker, lida do ambiente (docs/ARCHITECTURE.md §4, D17, D18, D19).

A chave da API vem SOMENTE do arquivo apontado por `YOUTUBE_API_KEY_FILE`: não há valor padrão e a
variável de ambiente `YOUTUBE_API_KEY` nunca é lida. O worker só conhece `DB_PATH` (banco
compartilhado).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

DEFAULT_DB_PATH = "/data/livestats.db"
DEFAULT_API_BASE_URL = "https://www.googleapis.com/youtube/v3"
DEFAULT_INTERVAL = 30
MIN_INTERVAL = 10
MAX_INTERVAL = 3600
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigError(Exception):
    """Configuração ausente ou inválida. A mensagem nunca contém o valor da chave."""


@dataclass(frozen=True)
class Config:
    db_path: str
    api_key: str = field(repr=False)
    interval: int
    api_base_url: str

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> Config:
        return cls(
            db_path=environ.get("DB_PATH") or DEFAULT_DB_PATH,
            api_key=read_api_key(environ.get("YOUTUBE_API_KEY_FILE", "")),
            interval=parse_interval(environ.get("LIVE_INTERVAL")),
            api_base_url=validate_base_url(
                environ.get("YOUTUBE_API_BASE_URL") or DEFAULT_API_BASE_URL
            ),
        )


def read_api_key(path: str) -> str:
    if not path:
        raise ConfigError("YOUTUBE_API_KEY_FILE não definida (a chave só é lida de arquivo)")
    try:
        with open(path, encoding="utf-8") as handle:
            key = handle.read().strip()
    except FileNotFoundError:
        raise ConfigError("o arquivo apontado por YOUTUBE_API_KEY_FILE não existe") from None
    except (OSError, UnicodeDecodeError):
        raise ConfigError("não foi possível ler o arquivo apontado por YOUTUBE_API_KEY_FILE") from None
    if not key:
        raise ConfigError("o arquivo apontado por YOUTUBE_API_KEY_FILE está vazio")
    if not all(33 <= ord(char) <= 126 for char in key):
        raise ConfigError("o arquivo apontado por YOUTUBE_API_KEY_FILE tem caracteres inválidos")
    return key


def parse_interval(raw: str | None) -> int:
    if raw is None or raw.strip() == "":
        return DEFAULT_INTERVAL
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError("LIVE_INTERVAL deve ser um número inteiro de segundos") from None
    if value < MIN_INTERVAL or value > MAX_INTERVAL:
        raise ConfigError(f"LIVE_INTERVAL deve estar entre {MIN_INTERVAL} e {MAX_INTERVAL} segundos")
    return value


def validate_base_url(raw: str) -> str:
    """Só `https`, ou `http` para localhost/127.0.0.1/::1; sem credenciais, query ou fragmento."""
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - valida a porta (levanta ValueError se inválida)
    except ValueError:
        raise ConfigError("YOUTUBE_API_BASE_URL inválida") from None
    host = parts.hostname
    if not host or parts.username is not None or parts.password is not None:
        raise ConfigError("YOUTUBE_API_BASE_URL inválida")
    if parts.query or parts.fragment:
        raise ConfigError("YOUTUBE_API_BASE_URL não pode ter query nem fragmento")
    if parts.scheme == "https":
        return raw.rstrip("/")
    if parts.scheme == "http" and host in LOOPBACK_HOSTS:
        return raw.rstrip("/")
    raise ConfigError(
        "YOUTUBE_API_BASE_URL deve usar https (http só para localhost, 127.0.0.1 ou ::1)"
    )
