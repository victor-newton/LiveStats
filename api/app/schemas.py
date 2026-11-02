"""Modelos de entrada e saneamento dos erros de validacao (422 nunca devolve ``input``)."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictStr

from .legacy import extract_video_id

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    password: Annotated[StrictStr, Field(min_length=1, max_length=1024)]


def _check_slot(item: str) -> str:
    if _CONTROL.search(item):
        raise ValueError("Caracteres de controle nao sao permitidos")
    text = item.strip()
    if text and extract_video_id(text) is None:
        raise ValueError("URL ou ID de video do YouTube invalido")
    return text


SlotText = Annotated[StrictStr, Field(max_length=2048), AfterValidator(_check_slot)]


class SlotsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slots: Annotated[list[SlotText], Field(min_length=4, max_length=4)]


def sanitize_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mantem so ``loc``, ``msg`` e ``type`` (sem ``input``, ``ctx`` nem ``url``)."""
    clean = []
    for err in errors:
        loc = [part if isinstance(part, (str, int)) else str(part) for part in err.get("loc", ())]
        clean.append({"loc": loc, "msg": str(err.get("msg", "")), "type": str(err.get("type", "value_error"))})
    return clean
