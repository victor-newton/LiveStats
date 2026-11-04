"""Funções puras compartilhadas pelo worker."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    _SP: tzinfo = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # imagem sem tzdata: São Paulo esta em UTC-3 fixo desde 2019
    _SP = timezone(timedelta(hours=-3))


def utc_iso(epoch_seconds: float) -> str:
    """UTC no texto dos JSON: `isoformat()` + `Z`, com microssegundos (D24)."""
    moment = datetime.fromtimestamp(epoch_seconds, timezone.utc).replace(tzinfo=None)
    return moment.isoformat() + "Z"


def day_sp(epoch_seconds: float) -> str:
    """Dia 'AAAA-MM-DD' no fuso America/Sao_Paulo (D43: dia do `set_peaks`)."""
    return datetime.fromtimestamp(epoch_seconds, _SP).strftime("%Y-%m-%d")


_PLATAFORMA = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}


def nome_live(platform: str, ref: str, label: str, title: str | None = None) -> str:
    """Mesmo nome que a api mostra no painel (D44); usado nas mensagens dos eventos."""
    if label:
        return label
    sufixo = _PLATAFORMA.get(platform, platform)
    if platform != "youtube" or ref.startswith(("@", "UC")):
        return f"{ref} · {sufixo}"
    curto = (title or "").strip()[:40].strip()
    return f"{curto} · {sufixo}" if curto else f"{sufixo} {ref}"
