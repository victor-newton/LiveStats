"""Funções puras compartilhadas pelo worker (formatos do legado: docs/legacy-formats.md §1.3)."""

from __future__ import annotations

import re
from datetime import datetime, timezone

_ID_PATTERNS = (
    re.compile(r"(?:v=|youtu\.be/|/live/)([a-zA-Z0-9_-]{11})"),
    re.compile(r"^([a-zA-Z0-9_-]{11})$"),
)


def extrair_video_id(url: str) -> str | None:
    """Extrai o ID de 11 caracteres de uma URL de vídeo (mesmo padrão do legado).

    Só reconhece `v=`, `youtu.be/`, `/live/` e o ID puro; `/shorts/` e `/embed/` não são suportados
    (Pendência P7 do contrato).
    """
    for pattern in _ID_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def formatar(n: int) -> str:
    """Formata contagens como no legado (`999950` vira `"1000.0K"` de propósito, P7)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def utc_iso(epoch_seconds: float) -> str:
    """UTC no texto dos JSON legados: `isoformat()` + `Z`, com microssegundos (D24)."""
    moment = datetime.fromtimestamp(epoch_seconds, timezone.utc).replace(tzinfo=None)
    return moment.isoformat() + "Z"
