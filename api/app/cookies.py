"""Validacao, normalizacao e cifragem dos cookies do YouTube Studio (D13).

A api so tem a chave PUBLICA: ela cifra com ``SealedBox`` e grava o resultado; nao consegue ler de volta.
Nenhum nome, valor, corpo ou texto puro de cookie vai para log, resposta de erro ou disco.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from nacl.public import PublicKey, SealedBox

from . import db
from .clock import legacy_timestamp

MAX_COOKIES = 200
MAX_DOMAIN_RAW = 1024
DEFAULT_DOMAIN = ".youtube.com"
DEFAULT_PATH = "/"
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_DOMAIN_OK = re.compile(r"\.?[A-Za-z0-9.-]+")
_MAX_ERRORS = 50


class CookieBadRequest(Exception):
    """400 com o ``detail`` herdado do legado."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CookieInvalid(Exception):
    """422: tipos, tamanhos ou charset invalidos. ``errors`` nunca contem valores enviados."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        super().__init__("cookies invalidos")
        self.errors = errors


def _error(index: int, field: str, msg: str, kind: str) -> dict[str, Any]:
    return {"loc": ["body", index, field], "msg": msg, "type": kind}


def normalize_cookies(payload: Any) -> list[dict[str, Any]]:
    """Valida na ordem do contrato (400 antes de 422) e devolve o array de 5 campos por cookie."""
    if not isinstance(payload, list) or len(payload) == 0:
        raise CookieBadRequest("Esperado um array JSON com pelo menos 1 cookie")
    for i, item in enumerate(payload):
        if not isinstance(item, dict) or "name" not in item or "value" not in item:
            raise CookieBadRequest(f"Cookie #{i} sem campo 'name' ou 'value'")

    if len(payload) > MAX_COOKIES:
        raise CookieInvalid([{"loc": ["body"], "msg": f"No maximo {MAX_COOKIES} cookies", "type": "too_long"}])

    errors: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    for i, item in enumerate(payload):
        name, value = item["name"], item["value"]
        if not isinstance(name, str) or not 1 <= len(name) <= 256:
            errors.append(_error(i, "name", "Texto de 1 a 256 caracteres", "string_invalid"))
        if not isinstance(value, str) or len(value) > 8192:
            errors.append(_error(i, "value", "Texto de ate 8192 caracteres", "string_invalid"))

        domain: Any = DEFAULT_DOMAIN
        if "domain" in item:
            raw = item["domain"]
            if not isinstance(raw, str) or len(raw) > MAX_DOMAIN_RAW:
                errors.append(_error(i, "domain", "Texto invalido", "string_invalid"))
            else:
                # Corrige dominios colados com markdown: ".[youtube.com](http://youtube.com)" -> ".youtube.com"
                domain = _MARKDOWN_LINK.sub(r"\1", raw)
                if len(domain) > 255 or not _DOMAIN_OK.fullmatch(domain):
                    errors.append(_error(i, "domain", "Dominio invalido", "string_pattern_mismatch"))

        path: Any = DEFAULT_PATH
        if "path" in item:
            raw_path = item["path"]
            if not isinstance(raw_path, str) or len(raw_path) > 1024:
                errors.append(_error(i, "path", "Texto de ate 1024 caracteres", "string_invalid"))
            else:
                path = raw_path or DEFAULT_PATH

        secure: Any = False
        if "secure" in item:
            if not isinstance(item["secure"], bool):
                errors.append(_error(i, "secure", "Esperado booleano", "bool_type"))
            else:
                secure = item["secure"]

        normalized.append({"name": name, "value": value, "domain": domain, "path": path, "secure": secure})
        if len(errors) >= _MAX_ERRORS:
            break

    if errors:
        raise CookieInvalid(errors[:_MAX_ERRORS])
    return normalized


class CookieSealer:
    """Cifra com a chave publica. Nao existe caminho de decifragem neste processo."""

    def __init__(self, public_key: bytes) -> None:
        self._box = SealedBox(PublicKey(public_key))
        self.key_id = hashlib.sha256(public_key).hexdigest()[:16]

    def seal(self, cookies: list[dict[str, Any]]) -> bytes:
        plain = json.dumps(cookies, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._box.encrypt(plain)


def store_cookie_set(conn: sqlite3.Connection, ciphertext: bytes, key_id: str, count: int, uploaded_by: str, now: float) -> int:
    """INSERT da nova linha e DELETE das demais na mesma transacao (so a ultima e mantida)."""
    with db.transaction(conn, immediate=True):
        cur = conn.execute(
            "INSERT INTO cookie_sets (ciphertext, key_id, cookie_count, uploaded_by, created_at, source) "
            "VALUES (?, ?, ?, ?, ?, 'admin')",
            (ciphertext, key_id, count, uploaded_by[:64], legacy_timestamp(now)),
        )
        new_id = int(cur.lastrowid)
        conn.execute("DELETE FROM cookie_sets WHERE id <> ?", (new_id,))
    return new_id
