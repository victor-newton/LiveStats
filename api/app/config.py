"""Configuracao da api, lida do ambiente na subida.

Segredos chegam por arquivo (variaveis ``*_FILE`` com o CAMINHO). Nao ha valor padrao para nenhum
segredo: arquivo ausente, vazio ou invalido => ``ConfigError`` e o processo nao sobe.
As mensagens de erro nunca incluem o conteudo dos arquivos.
"""

from __future__ import annotations

import base64
import binascii
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

import argon2

MIN_SQLITE = (3, 37, 0)  # tabelas STRICT
_DEFAULT_SCHEMA_DIRS = (Path("/app/db"), Path(__file__).resolve().parents[2] / "db")


class ConfigError(RuntimeError):
    """Configuracao ausente ou invalida; a aplicacao nao deve subir."""


@dataclass(frozen=True)
class Settings:
    db_path: str
    admin_db_path: str
    schema_sql: str
    admin_schema_sql: str
    admin_username: str
    password_hash: str
    cookie_public_key: bytes
    public_base_url: str | None

    def __repr__(self) -> str:  # nunca imprimir segredos
        return "Settings(...)"


def _require(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "")
    if not value or not value.strip():
        raise ConfigError(f"Variavel obrigatoria ausente ou vazia: {name}")
    return value.strip()


def _read_secret_file(env: Mapping[str, str], name: str) -> str:
    path = _require(env, name)
    try:
        content = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"{name}: arquivo de segredo ilegivel ({type(exc).__name__})") from None
    content = content.strip()
    if not content:
        raise ConfigError(f"{name}: arquivo de segredo vazio")
    return content


def _load_password_hash(env: Mapping[str, str]) -> str:
    value = _read_secret_file(env, "ADMIN_PASSWORD_HASH_FILE")
    if not value.startswith("$argon2id$"):
        raise ConfigError("ADMIN_PASSWORD_HASH_FILE: o conteudo nao e um hash argon2id (PHC)")
    try:
        argon2.extract_parameters(value)
    except argon2.exceptions.InvalidHashError:
        raise ConfigError("ADMIN_PASSWORD_HASH_FILE: o conteudo nao e um hash argon2id (PHC)") from None
    return value


def _load_public_key(env: Mapping[str, str]) -> bytes:
    value = _read_secret_file(env, "COOKIE_PUBLIC_KEY_FILE")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError("COOKIE_PUBLIC_KEY_FILE: base64 invalido") from None
    if len(raw) != 32:
        raise ConfigError("COOKIE_PUBLIC_KEY_FILE: a chave publica deve ter 32 bytes")
    return raw


def _load_public_base_url(env: Mapping[str, str]) -> str | None:
    value = env.get("PUBLIC_BASE_URL", "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() or ord(c) < 32 for c in value):
        raise ConfigError("PUBLIC_BASE_URL deve ser uma URL http(s) valida")
    return value.rstrip("/")


def _find_schema(env: Mapping[str, str], var: str, filename: str) -> str:
    explicit = env.get(var, "").strip()
    candidates = [Path(explicit)] if explicit else [d / filename for d in _DEFAULT_SCHEMA_DIRS]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    raise ConfigError(f"Arquivo de esquema SQL nao encontrado (defina {var})")


def _same_file(a: str, b: str) -> bool:
    if os.path.realpath(a) == os.path.realpath(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    if sqlite3.sqlite_version_info < MIN_SQLITE:
        raise ConfigError("SQLite >= 3.37 e necessario (tabelas STRICT)")

    db_path = _require(env, "DB_PATH")
    admin_db_path = _require(env, "ADMIN_DB_PATH")
    if _same_file(db_path, admin_db_path):
        raise ConfigError("ADMIN_DB_PATH deve ser um arquivo diferente de DB_PATH (estado de admin isolado)")

    username = _require(env, "ADMIN_USERNAME")
    if len(username) > 64 or any(ord(c) < 32 for c in username):
        raise ConfigError("ADMIN_USERNAME invalido (1 a 64 caracteres, sem controle)")

    return Settings(
        db_path=db_path,
        admin_db_path=admin_db_path,
        schema_sql=_find_schema(env, "DB_SCHEMA_FILE", "schema.sql"),
        admin_schema_sql=_find_schema(env, "ADMIN_DB_SCHEMA_FILE", "admin_schema.sql"),
        admin_username=username,
        password_hash=_load_password_hash(env),
        cookie_public_key=_load_public_key(env),
        public_base_url=_load_public_base_url(env),
    )
