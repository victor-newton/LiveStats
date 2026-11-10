"""Memoria do sistema (D56) para a Saude do mestre: o proprio container da api, os batimentos dos workers e a memoria do host.

Nada sensivel: so numeros e nomes de servico. Cada container mede o PROPRIO cgroup (sem docker.sock); o ``web`` (nginx)
nao grava nada no banco e por isso fica "nao medido" e fora do total.
"""

from __future__ import annotations

import mmap
import sqlite3
from pathlib import Path
from typing import Any

HEARTBEAT_MAX_AGE = 60  # segundos: batimento mais velho que isso = servico "nao medido"
NOTE = (
    "Cada serviço mostra o uso do próprio container (inclui o cache de arquivos que o cgroup conta e, no contador, o Chromium). "
    "O web (nginx) não grava a própria memória: fica 'não medido' e fora do total."
)


def container_memory_bytes(cgroup_root: str | Path = "/sys/fs/cgroup", proc_root: str | Path = "/proc") -> int | None:
    """Memoria em uso pelo PROPRIO container (sem docker.sock): cgroup v2 ``memory.current``, senao cgroup v1
    ``memory/memory.usage_in_bytes``, senao a soma do RSS dos processos visiveis em ``/proc``. Inclui os filhos (o Chromium)
    e o cache de arquivos que o cgroup contabiliza. ``None`` se nada for legivel."""
    cg, proc = Path(cgroup_root), Path(proc_root)
    for candidate in (cg / "memory.current", cg / "memory" / "memory.usage_in_bytes"):
        value = _read_int(candidate)
        if value is not None:
            return value
    return _rss_total(proc)


def _read_int(path: Path) -> int | None:
    try:
        value = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    return value if value >= 0 else None


def _rss_total(proc: Path) -> int | None:
    page = mmap.PAGESIZE
    total, found = 0, False
    try:
        entries = list(proc.iterdir())
    except OSError:
        return None
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            fields = (entry / "statm").read_text(encoding="ascii").split()
            total += int(fields[1]) * page
            found = True
        except (OSError, ValueError, IndexError):
            continue
    return total if found else None


def host_memory(proc_root: str | Path = "/proc") -> tuple[int | None, int | None]:
    """(MemTotal, MemAvailable) de ``/proc/meminfo`` em bytes; ``None`` onde nao der para ler."""
    values: dict[str, int] = {}
    try:
        text = (Path(proc_root) / "meminfo").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None, None
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        if key in ("MemTotal", "MemAvailable"):
            parts = rest.split()
            if parts and parts[0].isdigit():
                values[key] = int(parts[0]) * 1024
    return values.get("MemTotal"), values.get("MemAvailable")


def memory_block(
    conn: sqlite3.Connection,
    now: float,
    *,
    own_bytes: int | None = None,
    host: tuple[int | None, int | None] | None = None,
) -> dict[str, Any]:
    """O bloco ``memory`` de ``GET /api/admin/health/detail``. ``own_bytes``/``host`` existem para os testes."""
    own = container_memory_bytes() if own_bytes is None else own_bytes
    total, available = host_memory() if host is None else host
    beats = {
        row[0]: (row[1], row[2])
        for row in conn.execute("SELECT worker, last_heartbeat_at, mem_bytes FROM worker_status")
    }

    def from_heartbeat(worker: str) -> int | None:
        beat = beats.get(worker)
        if beat is None or beat[1] is None or now - beat[0] > HEARTBEAT_MAX_AGE:
            return None
        return int(beat[1])

    measured = [
        ("api", own),
        ("worker-lives", from_heartbeat("lives")),
        ("worker-inscritos", from_heartbeat("inscritos")),
        ("web", None),
    ]
    return {
        "host_total_bytes": total,
        "host_available_bytes": available,
        "total_bytes": sum(b for _, b in measured if b is not None),
        "services": [{"name": name, "bytes": b, "measured": b is not None} for name, b in measured],
        "note": NOTE,
    }
