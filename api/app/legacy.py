"""Formatos legados (OBS/vMix): extracao de ID de video e serializacao byte a byte.

Referencia: ``docs/legacy-formats.md``. Os JSON legados sao compactos, em UTF-8 literal, sem espaco apos
``:``/``,`` e com a ordem de chaves do codigo original.
"""

from __future__ import annotations

import json
import re
import sqlite3

_URL_PATTERN = re.compile(r"(?:v=|youtu\.be/|/live/)([a-zA-Z0-9_-]{11})")
_ID_PATTERN = re.compile(r"[a-zA-Z0-9_-]{11}")


def extract_video_id(url: str) -> str | None:
    """Mesmo padrao do worker (``extrair_video_id`` do original): usa a URL apos ``strip()``."""
    text = url.strip()
    match = _URL_PATTERN.search(text)
    if match:
        return match.group(1)
    if _ID_PATTERN.fullmatch(text):
        return text
    return None


def dumps(obj) -> bytes:
    """Serializacao dos JSON legados (identica a do ``JSONResponse`` do Starlette)."""
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")


def live_object(row: sqlite3.Row) -> dict:
    """Linha de ``live_stats`` -> objeto do JSON legado, com a ordem de chaves de ``legacy-formats.md`` 1.2."""
    common = {
        "video_id": row["video_id"],
        "titulo": row["title"],
        "status": row["status"],
        "views": row["views"],
        "likes": row["likes"],
        "views_formatado": row["views_formatted"],
        "likes_formatado": row["likes_formatted"],
        "atualizado_em": row["updated_at"],
    }
    if row["active"]:
        # slot com video: slot/ativo acrescentados no fim
        return {**common, "slot": row["slot"], "ativo": "true"}
    # slot sem URL: montado ja com slot/ativo na frente
    return {"slot": row["slot"], "ativo": "false", **common}


def subscribers_object(row: sqlite3.Row) -> dict:
    return {"inscritos": row["value"], "raw": row["raw"], "atualizado_em": row["read_at"]}
