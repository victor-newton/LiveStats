"""Healthcheck do container: ``python -m app.healthcheck`` (exit 0 = saudavel).

Consulta ``GET /health`` do proprio servidor. O alvo e FIXO em loopback (``127.0.0.1``): nao existe
configuracao de host, portanto este modulo nao consegue falar com nada fora do container.
Somente a porta pode ser ajustada (``PORT``, padrao 8000).
"""

from __future__ import annotations

import http.client
import json
import os
import sys

LOOPBACK = "127.0.0.1"


def check(port: int, timeout: float = 4.0) -> bool:
    conn = http.client.HTTPConnection(LOOPBACK, port, timeout=timeout)
    try:
        conn.request("GET", "/health")
        response = conn.getresponse()
        body = response.read(65536)
    except (OSError, http.client.HTTPException):
        return False
    finally:
        conn.close()
    if response.status != 200:
        return False
    try:
        return json.loads(body).get("status") == "ok"
    except (ValueError, AttributeError):
        return False


def main() -> int:
    try:
        port = int(os.environ.get("PORT", "8000"))
    except ValueError:
        print("PORT invalida", file=sys.stderr)
        return 1
    return 0 if check(port) else 1


if __name__ == "__main__":
    sys.exit(main())
