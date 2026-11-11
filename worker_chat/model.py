"""Formato normalizado das mensagens (D57), igual para as tres plataformas, e o contrato de entrega (`ChatSink`).

`{id: "<p>:<id da plataforma>", p: "yt"|"tw"|"kk", name (<= 50), uid (<= 64, minusculas), color: "#RRGGBB"|null,
  roles: [...], parts: [{t: "text", v} | {t: "emote", id, alt, src}]}`

`uid` e o identificador da CONTA do autor (login da Twitch, slug do Kick, id do canal do YouTube), em minusculas: e com
ele que o `clear` de um usuario encontra as mensagens dele (o `name` exibido pode ser diferente do login).

Todo texto vindo da plataforma passa por aqui antes de seguir: caracteres de controle saem, o texto total e limitado a 500
e o id de emote precisa casar `[A-Za-z0-9_-]{1,64}` (senao o emote vira o texto `alt`).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlsplit

PLATFORM_CODES = {"youtube": "yt", "twitch": "tw", "kick": "kk"}

NAME_MAX = 50
TEXT_MAX = 500  # soma dos textos de uma mensagem
ALT_MAX = 64
PARTS_MAX = 150  # defesa: uma mensagem nunca vira milhares de nos no overlay
ID_MAX = 128

EMOTE_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MSG_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}")
UID_RE = re.compile(r"[a-z0-9_.@-]{1,64}")
# Unicos hosts de imagem de emote do YouTube que chegam ao overlay (a CSP do chat repete esta lista).
YT_IMAGE_HOSTS = frozenset({"yt3.ggpht.com", "www.youtube.com"})

ROLE_ORDER = ("owner", "mod", "vip", "sub", "member", "verified")
PRIORITY_ROLES = frozenset({"owner", "mod", "vip", "sub", "member"})  # furam a fila do limitador (D57)


def clean_text(value: str) -> str:
    """Troca controles (inclusive quebras de linha) por espaco e tira caracteres de formatacao invisiveis (ex.: inversao
    de direcao do texto), menos o ZWJ dos emojis compostos."""
    out = []
    for ch in value:
        cat = unicodedata.category(ch)
        if cat == "Cc":
            out.append(" ")
        elif cat == "Cf" and ch != "‍":
            continue
        else:
            out.append(ch)
    return "".join(out)


def clean_name(value: str) -> str:
    return " ".join(clean_text(value).split())[:NAME_MAX]


def normalize_color(value: object) -> str | None:
    if isinstance(value, str) and _COLOR_RE.fullmatch(value):
        return value.upper()
    return None


def normalize_uid(value: object) -> str:
    """Identificador da conta em minusculas (ate 64); `""` se vier fora do padrao (inclusive maior que o limite). Quem emite
    e quem compara (`clear`) passam pela mesma funcao, entao o valor nunca diverge."""
    if not isinstance(value, str):
        return ""
    uid = value.strip().lower()
    return uid if UID_RE.fullmatch(uid) else ""


def safe_image_url(value: object) -> str | None:
    """URL de imagem de emote do YouTube: so `https` nos hosts permitidos, sem credenciais nem porta."""
    if not isinstance(value, str) or len(value) > 500:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or parts.hostname not in YT_IMAGE_HOSTS or port is not None:
        return None
    if parts.username is not None or parts.password is not None or parts.fragment:
        return None
    return value


def valid_message_id(value: object) -> bool:
    return isinstance(value, str) and _MSG_ID_RE.fullmatch(value) is not None


def ordered_roles(roles: set[str]) -> tuple[str, ...]:
    return tuple(r for r in ROLE_ORDER if r in roles)


@dataclass(frozen=True, eq=False)
class ChatMessage:
    id: str
    p: str
    name: str
    color: str | None
    roles: tuple[str, ...]
    parts: tuple[dict[str, str], ...]
    login: str = ""  # identificador da conta em minusculas (so para o filtro de bots; nunca vai ao overlay)
    uid: str = ""  # idem, normalizado (`normalize_uid`); vai ao overlay, que o compara com o `user` do `clear`

    @property
    def priority(self) -> bool:
        return any(r in PRIORITY_ROLES for r in self.roles)

    @property
    def starts_with_command(self) -> bool:
        first = self.parts[0] if self.parts else None
        return first is not None and first["t"] == "text" and first["v"].lstrip().startswith("!")

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "p": self.p,
            "name": self.name,
            "uid": self.uid,
            "color": self.color,
            "roles": list(self.roles),
            "parts": [dict(part) for part in self.parts],
        }


class PartsBuilder:
    """Monta `parts` respeitando o teto de texto (500) e de nos; texto vizinho vira um so no."""

    def __init__(self) -> None:
        self._parts: list[dict[str, str]] = []
        self._text_left = TEXT_MAX

    def text(self, value: str) -> None:
        value = clean_text(value)
        if not value or self._text_left <= 0:
            return
        value = value[: self._text_left]
        self._text_left -= len(value)
        if self._parts and self._parts[-1]["t"] == "text":
            self._parts[-1]["v"] += value
        elif len(self._parts) < PARTS_MAX:
            self._parts.append({"t": "text", "v": value})

    def emote(self, emote_id: str, alt: str) -> None:
        alt = " ".join(clean_text(alt).split())[:ALT_MAX]
        if not EMOTE_ID_RE.fullmatch(emote_id):
            self.text(alt)  # id fora do padrao: nunca vira URL, so o texto
            return
        if len(self._parts) < PARTS_MAX:
            self._parts.append({"t": "emote", "id": emote_id, "alt": alt, "src": emote_id})

    def image_emote(self, emote_id: str, alt: str, url: object) -> None:
        """Emote cuja imagem e uma URL (YouTube): sem URL permitida, ou com id fora do padrao, vira o texto `alt`."""
        src = safe_image_url(url)
        alt = " ".join(clean_text(alt).split())[:ALT_MAX]
        if src is None or not EMOTE_ID_RE.fullmatch(emote_id):
            self.text(alt)
            return
        if len(self._parts) < PARTS_MAX:
            self._parts.append({"t": "emote", "id": emote_id, "alt": alt, "src": src})

    def build(self) -> tuple[dict[str, str], ...]:
        parts = [dict(p) for p in self._parts]
        while parts and parts[0]["t"] == "text" and not parts[0]["v"].strip():
            parts.pop(0)
        while parts and parts[-1]["t"] == "text" and not parts[-1]["v"].strip():
            parts.pop()
        if parts and parts[0]["t"] == "text":
            parts[0]["v"] = parts[0]["v"].lstrip()
        if parts and parts[-1]["t"] == "text":
            parts[-1]["v"] = parts[-1]["v"].rstrip()
        return tuple(parts)


class ChatSink(Protocol):
    """Destino das mensagens normalizadas: quem recebe do conector. `platform` e o nome do banco (`twitch`, `kick`...)."""

    def message(self, platform: str, ref: str, msg: ChatMessage) -> None: ...

    def delete(self, platform: str, ref: str, ids: tuple[str, ...]) -> None: ...

    def clear(self, platform: str, ref: str, user: str | None) -> None: ...
