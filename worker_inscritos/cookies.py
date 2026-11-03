"""Decifragem e normalizacao dos cookies (D13).

Os cookies chegam em `cookie_sets.ciphertext` (PyNaCl SealedBox da chave publica). Aqui so existe a
chave privada. Nenhuma funcao deste modulo escreve nome ou valor de cookie em log, excecao ou banco.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox

DEFAULT_DOMAIN = ".youtube.com"
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)]+\)")


class CookieDecryptError(Exception):
    """Cookies que nao puderam ser decifrados/interpretados. A mensagem nunca traz conteudo."""


def key_id(private_key: PrivateKey) -> str:
    """16 primeiros hex do SHA-256 da chave publica (mesma regra da api para `cookie_sets.key_id`)."""
    return hashlib.sha256(bytes(private_key.public_key)).hexdigest()[:16]


def normalizar_cookies(bruto: Any) -> list[dict[str, Any]]:
    """Reduz cada cookie aos 5 campos que o scraper original usava (name, value, domain, path, secure).

    - `domain` com markdown (`.[youtube.com](http://...)`) vira o texto do link; ausente = `.youtube.com`;
    - `path` ausente = `/`; `secure` ausente = False;
    - qualquer outro atributo (httpOnly, sameSite, expirationDate...) e descartado, como no Selenium legado:
      o cookie entra como cookie de sessao;
    - itens sem `name`/`value` textuais sao ignorados.
    """
    if not isinstance(bruto, list):
        raise CookieDecryptError("conteudo dos cookies nao e uma lista")
    cookies: list[dict[str, Any]] = []
    for item in bruto:
        if not isinstance(item, dict):
            continue
        nome, valor = item.get("name"), item.get("value")
        if not isinstance(nome, str) or not isinstance(valor, str) or not nome:
            continue
        dominio = item.get("domain", DEFAULT_DOMAIN)
        if not isinstance(dominio, str) or not dominio:
            dominio = DEFAULT_DOMAIN
        caminho = item.get("path", "/")
        if not isinstance(caminho, str) or not caminho:
            caminho = "/"
        cookies.append(
            {
                "name": nome,
                "value": valor,
                "domain": _MARKDOWN_LINK.sub(r"\1", dominio),
                "path": caminho,
                "secure": bool(item.get("secure", False)),
            }
        )
    return cookies


def decifrar_cookies(ciphertext: bytes, private_key: PrivateKey) -> list[dict[str, Any]]:
    """Decifra o SealedBox, interpreta o JSON e normaliza. Qualquer falha vira CookieDecryptError."""
    try:
        claro = SealedBox(private_key).decrypt(bytes(ciphertext))
    except CryptoError:
        raise CookieDecryptError("nao foi possivel decifrar os cookies (chave incorreta ou dado corrompido)") from None
    try:
        bruto = json.loads(claro.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise CookieDecryptError("conteudo dos cookies nao e JSON valido") from None
    return normalizar_cookies(bruto)


# ---------------------------------------------------------------------- renovacao (D37)
DOMINIOS_SESSAO = ("youtube.com", "google.com")


def dominio_da_sessao(domain: str, hosts_extras: Iterable[str] = ()) -> bool:
    """True para youtube.com, google.com e subdominios (com ou sem ponto inicial) e para os hosts-base configurados."""
    d = domain.strip().lstrip(".").lower()
    if any(d == base or d.endswith("." + base) for base in DOMINIOS_SESSAO):
        return True
    return d in {h.lower() for h in hosts_extras}


def filtrar_sessao(cookies: list[dict[str, Any]], hosts_extras: Iterable[str] = ()) -> list[dict[str, Any]]:
    extras = tuple(hosts_extras)
    return [c for c in cookies if dominio_da_sessao(c["domain"], extras)]


def impressao(cookies: list[dict[str, Any]]) -> str:
    """Hash (so em memoria) do conjunto normalizado, independente da ordem. Nunca vai a disco nem a log."""
    itens = sorted((c["name"], c["domain"], c["path"], c["value"], bool(c["secure"])) for c in cookies)
    return hashlib.sha256(json.dumps(itens, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def cifrar_cookies(cookies: list[dict[str, Any]], public_key: PublicKey) -> bytes:
    """SealedBox da chave publica sobre o mesmo JSON compacto que a api grava."""
    claro = json.dumps(cookies, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return SealedBox(public_key).encrypt(claro)
