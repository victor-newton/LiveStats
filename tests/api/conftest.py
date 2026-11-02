"""Fixtures da suite da api. Cada teste cria o proprio banco e o proprio estado (independente da ordem)."""

from __future__ import annotations

import secrets
from dataclasses import dataclass

import pytest
from argon2 import PasswordHasher
from nacl.public import PrivateKey

from api_support import running_stack


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str
    password_hash: str
    private_key: PrivateKey
    public_key_bytes: bytes


@pytest.fixture(scope="session")
def creds() -> Credentials:
    """Credenciais e par de chaves ALEATORIOS, gerados por execucao (nada fixo no codigo)."""
    password = "Pw-" + secrets.token_urlsafe(18)
    private = PrivateKey.generate()
    return Credentials(
        username="Adm" + secrets.token_hex(3),  # tem maiuscula: usado em API-AUTH-14
        password=password,
        password_hash=PasswordHasher().hash(password),
        private_key=private,
        public_key_bytes=bytes(private.public_key),
    )


@pytest.fixture
def stack(tmp_path, creds):
    with running_stack(tmp_path, creds) as running:
        yield running
