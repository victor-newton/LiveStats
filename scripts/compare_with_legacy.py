# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
#!/usr/bin/env python3
"""Compara, lado a lado, a saida dos apps ATUAIS (legado) com a da pilha nova (INT-04, usado no V2).

Le, SOMENTE por GET, `/live1.json` ... `/live4.json` e `/inscritos.json` das duas URLs-base e compara:
  - o status HTTP;
  - o formato dos bytes (JSON compacto, UTF-8 literal, como o legado);
  - os CAMPOS e a ORDEM das chaves (lista de chaves identica), e o TIPO de cada valor;
  - o formato de `atualizado_em` (ISO 8601 UTC com microssegundos e sufixo Z);
  - com `--values`: tambem os VALORES (exceto `atualizado_em`, que muda a cada leitura).

Nunca envia nada alem de GET. Nao tem credenciais. A URL do legado e sempre informada de forma explicita:
este script nao conhece nenhum host. Sai com 0 se tudo igual, 1 se houve divergencia, 2 se uma URL nao respondeu.

    python scripts/compare_with_legacy.py --legacy-url http://HOST-ANTIGO:PORTA --new-url http://127.0.0.1:8080 --values
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass

PATHS = ["/live1.json", "/live2.json", "/live3.json", "/live4.json", "/inscritos.json"]
TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
VOLATILE = {"atualizado_em"}


@dataclass
class Diff:
    path: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"DIVERGE {self.path} [{self.where}] {self.detail}"


class Unreachable(Exception):
    pass


def fetch(base_url: str, path: str, timeout: float = 15.0) -> tuple[int, bytes]:
    """GET (e somente GET). Devolve (status, corpo)."""
    request = urllib.request.Request(base_url.rstrip("/") + path, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL informada pelo operador
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Unreachable(f"{base_url}{path}: {exc}") from exc


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    return type(value).__name__


class _Obj(list):
    """Objeto JSON como lista de pares, para preservar a ORDEM das chaves."""


def _keys_and_values(pairs: list[tuple[str, object]]) -> tuple[list[str], dict[str, object]]:
    return [k for k, _ in pairs], dict(pairs)


def _object_of(body: bytes) -> list[tuple[str, object]] | None:
    """Objeto do JSON (o 1o elemento da lista do /liveN.json ou o proprio objeto de /inscritos.json), com a ordem."""
    parsed = json.loads(body.decode("utf-8"), object_pairs_hook=_Obj)
    if isinstance(parsed, _Obj):
        return parsed
    if isinstance(parsed, list) and parsed and isinstance(parsed[0], _Obj):
        return parsed[0]
    return None


def compact_form(body: bytes) -> bytes:
    data = json.loads(body.decode("utf-8"))
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def compare_path(path: str, legacy: tuple[int, bytes], new: tuple[int, bytes], values: bool) -> list[Diff]:
    diffs: list[Diff] = []
    if legacy[0] != new[0]:
        diffs.append(Diff(path, "status", f"legado={legacy[0]} novo={new[0]}"))
        return diffs
    for label, (_, body) in (("legado", legacy), ("novo", new)):
        try:
            if compact_form(body) != body:
                diffs.append(Diff(path, "formato", f"{label}: corpo nao e JSON compacto em UTF-8 literal"))
        except (ValueError, UnicodeDecodeError):
            diffs.append(Diff(path, "formato", f"{label}: corpo nao e JSON valido"))
            return diffs

    old_obj, new_obj = _object_of(legacy[1]), _object_of(new[1])
    if old_obj is None or new_obj is None:
        if legacy[1] != new[1]:
            diffs.append(Diff(path, "corpo", "estrutura inesperada e corpos diferentes"))
        return diffs

    old_keys, old_map = _keys_and_values(old_obj)
    new_keys, new_map = _keys_and_values(new_obj)
    for key in old_keys:
        if key not in new_map:
            diffs.append(Diff(path, f"campo {key!r}", "existe no legado e falta no novo"))
    for key in new_keys:
        if key not in old_map:
            diffs.append(Diff(path, f"campo {key!r}", "existe no novo e nao no legado"))
    common_old = [k for k in old_keys if k in new_map]
    common_new = [k for k in new_keys if k in old_map]
    if common_old != common_new:
        diffs.append(Diff(path, "ordem das chaves", f"legado={old_keys} novo={new_keys}"))

    for key in common_old:
        if _type_name(old_map[key]) != _type_name(new_map[key]):
            diffs.append(
                Diff(path, f"tipo de {key!r}", f"legado={_type_name(old_map[key])} novo={_type_name(new_map[key])}")
            )
        if key in VOLATILE:
            for label, value in (("legado", old_map[key]), ("novo", new_map[key])):
                if not (isinstance(value, str) and TIMESTAMP.match(value)):
                    diffs.append(Diff(path, f"formato de {key!r}", f"{label}={value!r}"))
        elif values and old_map[key] != new_map[key]:
            diffs.append(Diff(path, f"valor de {key!r}", f"legado={old_map[key]!r} novo={new_map[key]!r}"))
    return diffs


def compare(legacy_url: str, new_url: str, values: bool = False, timeout: float = 15.0) -> list[Diff]:
    diffs: list[Diff] = []
    for path in PATHS:
        legacy = fetch(legacy_url, path, timeout)
        new = fetch(new_url, path, timeout)
        diffs.extend(compare_path(path, legacy, new, values))
    return diffs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--legacy-url", required=True, help="URL-base dos apps atuais (sem barra final)")
    parser.add_argument("--new-url", required=True, help="URL-base da pilha nova (porta publica)")
    parser.add_argument("--values", action="store_true", help="compara tambem os valores (menos atualizado_em)")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # titulos com acento nao podem quebrar o relatorio no Windows

    try:
        diffs = compare(args.legacy_url, args.new_url, values=args.values, timeout=args.timeout)
    except Unreachable as exc:
        print(f"ERRO: nao foi possivel consultar: {exc}", file=sys.stderr)
        return 2
    print(f"Comparados (somente GET): {', '.join(PATHS)}")
    if not diffs:
        print("OK: campos, ordem das chaves, tipos e formato de bytes identicos" + (" (valores incluidos)" if args.values else ""))
        return 0
    for diff in diffs:
        print(diff)
    print(f"{len(diffs)} divergencia(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
