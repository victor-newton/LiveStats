"""Deteccao de plataforma e normalizacao da referencia (``ref``) a partir da URL digitada no painel (D40).

YouTube: ``watch?v=ID``, ``youtu.be/ID``, ``/live/ID``, ``/shorts/ID`` -> ID de 11 caracteres;
``youtube.com/@handle/live`` -> ``@handle``; ``youtube.com/channel/UC.../live`` -> ``UC...`` (o worker resolve
para o video ao vivo atual a cada ciclo). Twitch: ``twitch.tv/<login>`` -> login em minusculas.
Kick: ``kick.com/<slug>`` -> slug em minusculas. Qualquer outra URL e invalida.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlsplit

PLATFORMS = ("youtube", "twitch", "kick")

_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_HANDLE = re.compile(r"@[\w.\-]{1,99}")
_TWITCH_LOGIN = re.compile(r"[A-Za-z0-9_]{1,25}")
_KICK_SLUG = re.compile(r"[A-Za-z0-9_-]{1,50}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\s]")

_YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_TWITCH_HOSTS = {"twitch.tv", "www.twitch.tv", "m.twitch.tv"}
_KICK_HOSTS = {"kick.com", "www.kick.com"}
# Primeiros segmentos do twitch.tv que NAO sao canais.
_TWITCH_RESERVED = {
    "directory", "videos", "downloads", "settings", "p", "search", "jobs", "turbo", "subscriptions", "wallet",
    "friends", "inventory", "drops", "prime", "store", "login", "signup", "popout", "embed",
}
_KICK_RESERVED = {"categories", "category", "video", "videos", "dashboard", "search", "browse", "following"}

_URL_YOUTUBE = re.compile(r"(?:v=|youtu\.be/|/live/)([a-zA-Z0-9_-]{11})")


def extract_youtube_video_id(text: str) -> str | None:
    """Extracao tolerante do formato v1 (so a migracao v4->v5 usa): URL com ``v=``/``youtu.be/``/``/live/`` ou ID puro."""
    text = text.strip()
    match = _URL_YOUTUBE.search(text)
    if match:
        return match.group(1)
    return text if _VIDEO_ID.fullmatch(text) else None


def _segments(path: str) -> list[str]:
    parts = [unquote(p) for p in path.split("/")]
    if parts and parts[-1] == "":  # barra final
        parts.pop()
    return parts[1:] if parts and parts[0] == "" else parts


def detect(url: str) -> tuple[str, str] | None:
    """Devolve ``(platform, ref)`` ou ``None`` quando a URL nao e de YouTube, Twitch ou Kick."""
    text = url.strip()
    if not text or _CONTROL.search(text):
        return None
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or parts.username is not None or port is not None:
        return None
    host = (parts.hostname or "").lower()
    seg = _segments(parts.path)
    if host in _YOUTUBE_HOSTS:
        ref = _youtube(seg, parts.query)
        return ("youtube", ref) if ref else None
    if host == "youtu.be":
        return ("youtube", seg[0]) if len(seg) == 1 and _VIDEO_ID.fullmatch(seg[0]) else None
    if host in _TWITCH_HOSTS:
        if len(seg) == 1 and _TWITCH_LOGIN.fullmatch(seg[0]) and seg[0].lower() not in _TWITCH_RESERVED:
            return "twitch", seg[0].lower()
        return None
    if host in _KICK_HOSTS:
        if len(seg) == 1 and _KICK_SLUG.fullmatch(seg[0]) and seg[0].lower() not in _KICK_RESERVED:
            return "kick", seg[0].lower()
        return None
    return None


def _youtube(seg: list[str], query: str) -> str | None:
    if seg == ["watch"]:
        values = parse_qs(query).get("v", [])
        return values[0] if len(values) == 1 and _VIDEO_ID.fullmatch(values[0]) else None
    if len(seg) == 2 and seg[0] in ("live", "shorts"):
        return seg[1] if _VIDEO_ID.fullmatch(seg[1]) else None
    # Link de canal (com ou sem /live): o leitor sempre abre o /live do canal, que acompanha a live ativa.
    if len(seg) == 1 and _HANDLE.fullmatch(seg[0]):
        return seg[0]
    if len(seg) == 2 and seg[1] == "live" and _HANDLE.fullmatch(seg[0]):
        return seg[0]
    if len(seg) == 2 and seg[0] == "channel" and _CHANNEL_ID.fullmatch(seg[1]):
        return seg[1]
    if len(seg) == 3 and seg[0] == "channel" and seg[2] == "live" and _CHANNEL_ID.fullmatch(seg[1]):
        return seg[1]
    return None
