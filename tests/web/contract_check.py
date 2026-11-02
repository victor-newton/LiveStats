"""Validação de requisições e respostas contra docs/openapi.yaml (D26).

Este módulo NÃO conhece o front nem o dublê: dado método, caminho, status,
cabeçalhos e corpo, diz se a troca respeita o contrato. O dublê chama
`check_response` para TODA resposta que produz; qualquer violação é registrada
e a suíte falha (fixture `api` em conftest.py).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "openapi.yaml"
SPEC_URI = "urn:livestats:openapi"
CATCH_ALL = "/api/admin/{path}"
CATCH_ALL_METHODS = {"get", "post", "put", "patch", "delete"}


@dataclass(frozen=True)
class Operation:
    template: str
    method: str
    node: dict[str, Any]
    params: dict[str, str]


class ContractError(AssertionError):
    """A troca HTTP não respeita o openapi.yaml."""


class Contract:
    def __init__(self, path: Path = SPEC_PATH) -> None:
        self.spec: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.patched_defects: list[str] = []
        self._fix_workers_status_defect()
        resource = Resource.from_contents(self.spec, default_specification=DRAFT202012)
        self.registry = Registry().with_resource(SPEC_URI, resource)
        self.format_checker = FormatChecker()
        self._patterns: list[tuple[re.Pattern[str], str]] = []
        templates = sorted(self.spec["paths"], key=lambda t: (-len(re.sub(r"\{[^}]+\}", "", t)), t))
        for template in templates:
            regex = re.sub(r"\{(\w+)\}", self._group, template)
            self._patterns.append((re.compile(f"^{regex}$"), template))

    def _fix_workers_status_defect(self) -> None:
        """Defeito do contrato (T1), corrigido SÓ na cópia em memória.

        `WorkersStatus.workers.inscritos` combina, em `allOf`, o `WorkerState`
        (que tem `additionalProperties: false`) com um objeto que acrescenta
        `auth_state`. Nenhum documento consegue satisfazer os dois ao mesmo
        tempo: o `auth_state` é "propriedade adicional" para o primeiro ramo.
        A intenção é clara: um WorkerState MAIS `auth_state`. Aqui o schema é
        reescrito exatamente assim, derivando tudo do próprio openapi.yaml
        (nada é afrouxado: continua fechado e com todos os campos exigidos).
        Se o contrato for corrigido, o patch deixa de se aplicar.
        """
        schemas = self.spec["components"]["schemas"]
        inscritos = schemas["WorkersStatus"]["properties"]["workers"]["properties"]["inscritos"]
        parts = inscritos.get("allOf")
        if not parts:
            return
        base = self.deref(parts[0])
        extra = parts[1]
        merged = {
            "type": "object",
            "additionalProperties": False,
            "required": [*base["required"], *extra["required"]],
            "properties": {**base["properties"], **extra["properties"]},
        }
        schemas["WorkersStatus"]["properties"]["workers"]["properties"]["inscritos"] = merged
        self.patched_defects.append("WorkersStatus.workers.inscritos: allOf + additionalProperties:false")

    @staticmethod
    def _group(match: re.Match[str]) -> str:
        name = match.group(1)
        return f"(?P<{name}>.+)" if name == "path" else f"(?P<{name}>[^/]+)"

    # ---- resolução ---------------------------------------------------------

    def deref(self, node: Any) -> Any:
        """Segue `$ref: '#/...'` dentro do próprio documento."""
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            assert ref.startswith("#/"), ref
            cur: Any = self.spec
            for part in ref[2:].split("/"):
                cur = cur[part.replace("~1", "/").replace("~0", "~")]
            node = cur
        return node

    def validator(self, schema: dict[str, Any]) -> Draft202012Validator:
        if "$ref" in schema and schema["$ref"].startswith("#/"):
            schema = {"$ref": SPEC_URI + schema["$ref"]}
        return Draft202012Validator(schema, registry=self.registry, format_checker=self.format_checker)

    # ---- localização da operação ------------------------------------------

    def find(self, method: str, path: str) -> Operation | None:
        method = method.lower()
        for pattern, template in self._patterns:
            m = pattern.match(path)
            if not m:
                continue
            item = self.spec["paths"][template]
            if method in item:
                return Operation(template, method, item[method], m.groupdict())
            if template == CATCH_ALL and method in CATCH_ALL_METHODS:
                return Operation(template, method, item["get"], m.groupdict())
        return None

    # ---- requisição --------------------------------------------------------

    def request_errors(self, op: Operation, body: Any) -> list[dict[str, Any]]:
        """Erros de schema do corpo da requisição (lista vazia = válido)."""
        rb = op.node.get("requestBody")
        if not rb:
            return []
        schema = self.deref(rb)["content"]["application/json"]["schema"]
        errors = []
        for err in sorted(self.validator(schema).iter_errors(body), key=lambda e: list(e.path)):
            # Mensagem genérica: o schema nunca devolve o valor enviado (ValidationError).
            errors.append({"loc": ["body", *err.path], "msg": f"Valor inválido ({err.validator})", "type": err.validator})
        return errors

    # ---- resposta ----------------------------------------------------------

    def check_response(
        self,
        method: str,
        path: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        """Levanta ContractError se a resposta não estiver no openapi.yaml."""
        op = self.find(method, path)
        if op is None:
            raise ContractError(f"{method} {path}: operação não existe no openapi.yaml")
        responses = op.node["responses"]
        if str(status) not in responses:
            raise ContractError(
                f"{method} {path}: status {status} não declarado (declarados: {sorted(responses)})"
            )
        resp = self.deref(responses[str(status)])
        lower = {k.lower(): v for k, v in headers.items()}

        for name, spec in (resp.get("headers") or {}).items():
            hschema = self.deref(spec).get("schema", {})
            key = name.lower()
            if "const" in hschema and key != "set-cookie":
                if lower.get(key) != hschema["const"]:
                    raise ContractError(
                        f"{method} {path} -> {status}: cabeçalho {name} deveria ser "
                        f"{hschema['const']!r}, veio {lower.get(key)!r}"
                    )
            if key == "retry-after":
                value = lower.get(key)
                if value is None or not value.isdigit() or int(value) < 1:
                    raise ContractError(f"{method} {path} -> {status}: Retry-After inválido: {value!r}")

        content = resp.get("content") or {}
        ctype = lower.get("content-type", "").split(";")[0].strip()
        if not content:
            if body:
                raise ContractError(f"{method} {path} -> {status}: o contrato não prevê corpo")
            return
        if ctype not in content:
            raise ContractError(
                f"{method} {path} -> {status}: Content-Type {ctype!r} fora do contrato {sorted(content)}"
            )
        if ctype != "application/json":
            return
        try:
            payload = json.loads(body.decode("utf-8"))
        except ValueError as exc:
            raise ContractError(f"{method} {path} -> {status}: corpo não é JSON ({exc})") from exc
        schema = content[ctype]["schema"]
        errors = sorted(self.validator(schema).iter_errors(payload), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            where = "/".join(str(p) for p in first.path) or "(raiz)"
            raise ContractError(
                f"{method} {path} -> {status}: corpo fora do schema em {where}: {first.message}"
            )

    def schema_errors(self, schema_name: str, payload: Any) -> list[str]:
        """Valida um payload contra components/schemas/<schema_name>."""
        v = self.validator({"$ref": f"#/components/schemas/{schema_name}"})
        return [e.message for e in v.iter_errors(payload)]
