"""Formato publico dos JSON (OBS/vMix): serializacao byte a byte do JSON de inscritos e utilitarios de JSON compacto.

Os JSON publicos sao compactos, em UTF-8 literal, sem espaco apos ``:``/``,`` e com a ordem de chaves fixa.
"""

from __future__ import annotations

import json
import sqlite3



def dumps(obj) -> bytes:
    """Serializacao dos JSON publicos (identica a do ``JSONResponse`` do Starlette)."""
    return json.dumps(obj, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":")).encode("utf-8")


def subscribers_object(row: sqlite3.Row) -> dict:
    return {"inscritos": row["value"], "raw": row["raw"], "atualizado_em": row["read_at"]}
