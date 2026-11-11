"""Modelos de entrada e saneamento dos erros de validacao (422 nunca devolve ``input``)."""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

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
    chat_enabled: StrictBool = None  # type: ignore[assignment]  # D57: interruptor "entra no chat"

    @model_validator(mode="after")
    def _at_least_one(self) -> "LiveUpdate":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo")
        return self


_CHANNEL_ID_FULL = re.compile(r"UC[A-Za-z0-9_-]{22}")
_CHANNEL_IN_PATH = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})(?:/|$)")
_YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com", "studio.youtube.com")


def channel_id_from_text(value: str) -> str | None:
    """D54: ID ``UC...`` de um ID puro ou de uma URL do YouTube/Studio com ``/channel/UC...``; vazio -> None (limpa). Sem rede."""
    text = value.strip()
    if not text:
        return None
    if _CONTROL.search(text):
        raise ValueError("Caracteres de controle nao sao permitidos")
    if _CHANNEL_ID_FULL.fullmatch(text):
        return text
    try:
        parts = urlsplit(text if "://" in text else f"//{text}")
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("Canal invalido: informe o ID (UC...) ou o link do Studio do canal") from None
    found = _CHANNEL_IN_PATH.search(parts.path) if host in _YOUTUBE_HOSTS else None
    if found is None:
        raise ValueError("Canal invalido: informe o ID (UC...) ou o link do Studio do canal")
    return found.group(1)


class CounterWrite(BaseModel):
    """Qualquer subconjunto (nao vazio) de fonte, cor e canal do contador do usuario.

    ``channel_id`` (D54) e o ID ``UC...`` ou um link do Studio/YouTube com ``/channel/UC...``; ``null`` ou vazio volta a
    deteccao automatica pelos cookies. Nada e validado no YouTube.
    """

    model_config = ConfigDict(extra="forbid")

    font: Font = None  # type: ignore[assignment]
    text_color: TextColor = None  # type: ignore[assignment]
    channel_id: Annotated[StrictStr, Field(max_length=2048)] | None = None

    @field_validator("channel_id", mode="after")
    @classmethod
    def _channel(cls, value: str | None) -> str | None:
        return None if value is None else channel_id_from_text(value)

    @model_validator(mode="after")
    def _at_least_one(self) -> "CounterWrite":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo")
        if any(getattr(self, name) is None for name in self.model_fields_set - {"channel_id"}):
            raise ValueError("Valor nulo nao e permitido")
        return self


# ---------------------------------------------------------------------------- v5: chat unificado (D57)
CHAT_THEMES = ("cartoon", "adesivo", "pixel", "postit", "chiclete", "classico", "balao", "vidro", "minimal", "terminal", "rodape")
IGNORED_USERS_MAX = 500
_IGNORED_NAME = re.compile(r"[a-z0-9_.-]{1,40}")
_SEPARATORS = re.compile(r"[,\s]+")


def normalize_ignored_users(value: str | list[str]) -> list[str]:
    """Lista ou texto separado por virgula/espaco -> nomes em minusculas, sem repeticao, cada um ``[a-z0-9_.-]{1,40}``, total <= 500."""
    if isinstance(value, str):
        pieces = _SEPARATORS.split(value)
    else:
        pieces = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError("Cada nome deve ser texto")
            pieces.extend(_SEPARATORS.split(item))
    names: list[str] = []
    for piece in pieces:
        name = piece.lower()
        if not name:
            continue
        if _IGNORED_NAME.fullmatch(name) is None:
            raise ValueError("Nome invalido: use de 1 a 40 caracteres entre letras, digitos, '.', '_' e '-'")
        if name not in names:
            names.append(name)
    if len(",".join(names)) > IGNORED_USERS_MAX:
        raise ValueError(f"A lista de ignorados deve ter no maximo {IGNORED_USERS_MAX} caracteres")
    return names


class ChatWrite(BaseModel):
    """Qualquer subconjunto (nao vazio) da configuracao do chat do conjunto. Nulo nao e permitido."""

    model_config = ConfigDict(extra="forbid")

    enabled: StrictBool = None  # type: ignore[assignment]
    theme: Literal[CHAT_THEMES] = None  # type: ignore[valid-type,assignment]
    font_size: Annotated[StrictInt, Field(ge=12, le=40)] = None  # type: ignore[assignment]
    logo_style: Literal["cor", "mono", "sigla"] = None  # type: ignore[assignment]
    name_color: Literal["usuario", "plataforma", "branco"] = None  # type: ignore[assignment]
    lifetime_s: Annotated[StrictInt, Field(ge=0, le=300)] = None  # type: ignore[assignment]
    show_roles: StrictBool = None  # type: ignore[assignment]
    hide_commands: StrictBool = None  # type: ignore[assignment]
    ignored_users: Annotated[StrictStr | list[StrictStr], Field(max_length=2000)] = None  # type: ignore[assignment]
    max_rate: Annotated[StrictInt, Field(ge=1, le=50)] = None  # type: ignore[assignment]

    @field_validator("ignored_users", mode="after")
    @classmethod
    def _ignored(cls, value: str | list[str]) -> list[str]:
        return normalize_ignored_users(value)

    @model_validator(mode="after")
    def _at_least_one(self) -> "ChatWrite":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Valor nulo nao e permitido")
        return self


# ---------------------------------------------------------------------------- v4: usuarios (D48, D52)
def _check_username(value: str) -> str:
    text = value.strip().lower()
    if _USERNAME.fullmatch(text) is None:
        raise ValueError("Use de 3 a 32 caracteres: letras minusculas, digitos, '.', '_' ou '-'")
    return text


def _check_display_name(value: str) -> str:
    text = _check_text(value)
    if len(text) > 60:
        raise ValueError("Use no maximo 60 caracteres")
    return text


_USERNAME = re.compile(r"[a-z0-9._-]{3,32}")
Username = Annotated[StrictStr, Field(max_length=64), AfterValidator(_check_username)]
DisplayName = Annotated[StrictStr, Field(max_length=200), AfterValidator(_check_display_name)]
MaxSets = Annotated[StrictInt, Field(ge=0, le=1000)]
MaxLives = Annotated[StrictInt, Field(ge=0, le=10000)]


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current: Annotated[StrictStr, Field(min_length=1, max_length=1024)]
    new: Annotated[StrictStr, Field(min_length=1, max_length=1024)]


class MeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName


class UserCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: Username
    display_name: DisplayName = ""
    max_sets: MaxSets = 5
    max_lives: MaxLives = 20
    has_counter: StrictBool = True


class UserUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: DisplayName = None  # type: ignore[assignment]
    status: Literal["active", "suspended"] = None  # type: ignore[assignment]
    max_sets: MaxSets = None  # type: ignore[assignment]
    max_lives: MaxLives = None  # type: ignore[assignment]
    has_counter: StrictBool = None  # type: ignore[assignment]

    @model_validator(mode="after")
    def _at_least_one(self) -> "UserUpdate":
        if not self.model_fields_set:
            raise ValueError("Informe ao menos um campo")
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("Valor nulo nao e permitido")
        return self


class ConfirmBody(BaseModel):
    """Purge e exclusao de conta: o nome de usuario digitado de novo."""

    model_config = ConfigDict(extra="forbid")

    confirm: Annotated[StrictStr, Field(max_length=64)]


def sanitize_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mantem so ``loc``, ``msg`` e ``type`` (sem ``input``, ``ctx`` nem ``url``)."""
    clean = []
    for err in errors:
        loc = [part if isinstance(part, (str, int)) else str(part) for part in err.get("loc", ())]
        clean.append({"loc": loc, "msg": str(err.get("msg", "")), "type": str(err.get("type", "value_error"))})
    return clean
