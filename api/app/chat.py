"""Configuracao do chat unificado por conjunto (D57). Nenhuma mensagem de chat passa por aqui nem e gravada.

Sem linha em ``set_chat`` = padroes (chat desligado). A api e a unica que escreve; o worker-chat so le.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .clock import iso_seconds

OVERLAY_PATH = "/overlay/chat.html?conjunto={set_id}"

# Padroes do esquema (db/schema.sql, tabela set_chat). Valem para conjunto sem linha; mantenha-os iguais aos DEFAULTs.
DEFAULT_IGNORED = ("nightbot", "streamelements", "moobot", "streamlabs", "botrix")
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "theme": "classico",
    "font_size": 18,
    "logo_style": "cor",
    "name_color": "usuario",
    "lifetime_s": 0,
    "show_roles": True,
    "hide_commands": True,
    "ignored_users": list(DEFAULT_IGNORED),
    "max_rate": 8,
}
_BOOL_FIELDS = ("enabled", "show_roles", "hide_commands")
APPEARANCE_FIELDS = ("enabled", "theme", "font_size", "logo_style", "name_color", "lifetime_s", "show_roles")
_COLUMNS = (
    "enabled", "theme", "font_size", "logo_style", "name_color", "lifetime_s", "show_roles", "hide_commands",
    "ignored_users", "max_rate", "updated_at",
)


def overlay_path(set_id: int) -> str:
    """Caminho publico relativo do overlay (o painel antepoe ``PUBLIC_BASE_URL``, como nos outros enderecos publicos)."""
    return OVERLAY_PATH.format(set_id=set_id)


def read_config(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    """A configuracao completa do conjunto (padroes se nao ha linha). ``updated_at`` e ISO ou ``None``."""
    row = conn.execute(f"SELECT {', '.join(_COLUMNS)} FROM set_chat WHERE set_id = ?", (set_id,)).fetchone()  # noqa: S608 (constante)
    if row is None:
        return {**DEFAULTS, "ignored_users": list(DEFAULT_IGNORED), "updated_at": None}
    config: dict[str, Any] = {key: row[key] for key in _COLUMNS if key not in ("ignored_users", "updated_at")}
    for key in _BOOL_FIELDS:
        config[key] = bool(config[key])
    config["ignored_users"] = [name for name in row["ignored_users"].split(",") if name]
    config["updated_at"] = iso_seconds(row["updated_at"])
    return config


def write_config(conn: sqlite3.Connection, set_id: int, changes: dict[str, Any], now: int) -> None:
    """UPSERT so das colunas enviadas (as demais mantem o valor atual ou o padrao do esquema). Dentro de uma transacao."""
    values: dict[str, Any] = {}
    for key, value in changes.items():
        if key in _BOOL_FIELDS:
            value = int(value)
        elif key == "ignored_users":
            value = ",".join(value)
        values[key] = value
    names = list(values)
    columns = ", ".join(["set_id", *names, "updated_at"])
    marks = ", ".join("?" for _ in range(len(names) + 2))
    update = ", ".join([*(f"{name} = excluded.{name}" for name in names), "updated_at = excluded.updated_at"])
    conn.execute(  # noqa: S608 (nomes de colunas vem de uma lista fixa, valores por parametro)
        f"INSERT INTO set_chat ({columns}) VALUES ({marks}) ON CONFLICT(set_id) DO UPDATE SET {update}",
        (set_id, *values.values(), int(now)),
    )


def public_appearance(conn: sqlite3.Connection, set_id: int) -> dict[str, Any]:
    """So a aparencia lida pelo overlay (nada do dono, nada de filtros)."""
    config = read_config(conn, set_id)
    return {key: config[key] for key in APPEARANCE_FIELDS}
