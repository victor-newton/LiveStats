# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""Ajudantes das suites de seguranca da T8."""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from e2e_stack import PUBLIC_PORT, REPO, E2EStack, header

JSON_HEADERS = {"Content-Type": "application/json"}
ORIGIN = "https://qualquer.example"

_FLOOD: dict | None = None


def flood(stack: E2EStack) -> dict:
    """Rajadas a partir de containers com IP proprio na rede edge (uma vez por sessao): produzem respostas 429 reais."""
    global _FLOOD
    if _FLOOD is None:
        burst = [{"path": "/health", "repeat": 120}]
        _FLOOD = {
            "public_8080": stack.client(32, burst, port=8080),
            "public_8081": stack.client(33, burst, port=8081),
            "admin_8081": stack.client(34, [{"path": "/api/admin/session", "repeat": 80}], port=8081),
        }
    return _FLOOD


def first_with_status(responses: list[dict], status: int) -> dict:
    for r in responses:
        if r["status"] == status:
            return r
    raise AssertionError(f"nenhuma resposta {status} na rajada: {sorted({r['status'] for r in responses})}")


def cors_headers(resp: dict) -> list[str]:
    return [k for k, _ in resp["headers"] if k.lower().startswith("access-control-")]


def openapi_routes() -> list[tuple[str, str]]:
    """(metodo, caminho concreto) de TODAS as rotas do openapi.yaml, mais OPTIONS (preflight) em cada uma."""
    spec = yaml.safe_load((REPO / "docs" / "openapi.yaml").read_text(encoding="utf-8"))
    routes = []
    for template, item in spec["paths"].items():
        path = re.sub(r"\{(slot|n)\}", "1", template).replace("{path}", "nao-existe")
        methods = [m.upper() for m in item if m in ("get", "post", "put", "patch", "delete", "options", "head")]
        for method in sorted(set(methods) | {"OPTIONS"}):
            routes.append((method, path))
    return routes


def login_body(username: str, password: str) -> str:
    return json.dumps({"username": username, "password": password})


def admin_flags(set_cookie: str) -> dict:
    """Atributos de um Set-Cookie: {'name', 'value', 'flags': {minusculo: valor|True}}."""
    parts = [p.strip() for p in set_cookie.split(";")]
    name, _, value = parts[0].partition("=")
    flags: dict[str, object] = {}
    for part in parts[1:]:
        key, _, val = part.partition("=")
        flags[key.lower()] = val if val else True
    return {"name": name, "value": value, "flags": flags}


def assert_no_secret(text: str, secrets: list[str], where: str) -> None:
    for secret in secrets:
        assert secret not in text, f"segredo/sentinela encontrado em {where}"


__all__ = [
    "JSON_HEADERS", "ORIGIN", "PUBLIC_PORT", "Path", "admin_flags", "assert_no_secret", "cors_headers", "flood",
    "first_with_status", "header", "login_body", "openapi_routes",
]
