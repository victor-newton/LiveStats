"""Modelos de entrada e saneamento dos erros de validacao (422 nunca devolve ``input``)."""

from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from .fonts import FONTS
from .platforms import detect

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Annotated[StrictStr, Field(min_length=1, max_length=64)]
    password: Annotated[StrictStr, Field(min_length=1, max_length=1024)]


# D34: meta de likes (inteiro estrito 1..1e9 ou null) e cor da barra (#RRGGBB, normalizada em maiusculas).
GOAL_MAX = 1_000_000_000
_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


def _check_color(value: str) -> str:
    if _COLOR.fullmatch(value) is None:  # fullmatch: um "$" aceitaria quebra de linha no fim
        raise ValueError("Cor invalida: use #RRGGBB")
    return value.upper()


Goal = Annotated[StrictInt, Field(ge=1, le=GOAL_MAX)] | None
Color = Annotated[StrictStr, Field(max_length=7), AfterValidator(_check_color)]


def _check_font(value: str) -> str:
    if value not in FONTS:  # comparacao exata com a lista fechada: nada de CSS vindo do cliente
        raise ValueError("Fonte fora da lista permitida")
    return value


Font = Annotated[StrictStr, Field(max_length=40), AfterValidator(_check_font)]
TextColor = Color  # D35: mesma regra #RRGGBB da D34


def _check_text(value: str) -> str:
    if _CONTROL.search(value):
        raise ValueError("Caracteres de controle nao sao permitidos")
    return value.strip()


def _check_name(value: str) -> str:
    text = _check_text(value)
    if not 1 <= len(text) <= 60:
        raise ValueError("Informe de 1 a 60 caracteres")
    return text


def _check_label(value: str) -> str:
    text = _check_text(value)
    if len(text) > 60:
        raise ValueError("Use no maximo 60 caracteres")
    return text


def _check_url(value: str) -> str:
    text = _check_text(value)
    if detect(text) is None:
        raise ValueError("URL invalida: use um link de live do YouTube, da Twitch (twitch.tv/canal) ou da Kick (kick.com/canal)")
    return text


Name = Annotated[StrictStr, Field(max_length=200), AfterValidator(_check_name)]
Label = Annotated[StrictStr, Field(max_length=200), AfterValidator(_check_label)]
LiveUrl = Annotated[StrictStr, Field(max_length=2048), AfterValidator(_check_url)]


class _Partial(BaseModel):
    """Campos opcionais: ausente = nao muda / usa o padrao. Um ``null`` explicito so vale para ``goal``.

    (default None nao e validado; um null explicito em outro campo e 422.)
    """

    model_config = ConfigDict(extra="forbid")

    goal: Goal = None
    color: Color = None  # type: ignore[assignment]
    font: Font = None  # type: ignore[assignment]
    text_color: TextColor = None  # type: ignore[assignment]


class SetCreate(_Partial):
    name: Name


class SetUpdate(_Partial):
    name: Name = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _at_least_one(self) -> "SetUpdate":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo")
        return self


class LiveCreate(_Partial):
    url: LiveUrl
    label: Label = None  # type: ignore[assignment]


class LiveUpdate(_Partial):
    url: LiveUrl = None  # type: ignore[assignment]
    label: Label = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _at_least_one(self) -> "LiveUpdate":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo")
        return self


class SettingsWrite(BaseModel):
    """Qualquer subconjunto (nao vazio) de fonte e cor do contador de inscritos. O canal e detectado (D38)."""

    model_config = ConfigDict(extra="forbid")

    subs_font: Font = None  # type: ignore[assignment]
    subs_text_color: TextColor = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _at_least_one(self) -> "SettingsWrite":
        if all(getattr(self, name) is None for name in ("subs_font", "subs_text_color")):
            raise ValueError("Informe ao menos um campo")
        return self


def sanitize_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mantem so ``loc``, ``msg`` e ``type`` (sem ``input``, ``ctx`` nem ``url``)."""
    clean = []
    for err in errors:
        loc = [part if isinstance(part, (str, int)) else str(part) for part in err.get("loc", ())]
        clean.append({"loc": loc, "msg": str(err.get("msg", "")), "type": str(err.get("type", "value_error"))})
    return clean
