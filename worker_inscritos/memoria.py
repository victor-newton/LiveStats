"""Memoria do container (D56): lida do PROPRIO cgroup, sem docker.sock. O worker grava o valor no batimento (`worker_status.mem_bytes`)."""

from __future__ import annotations

import mmap
from pathlib import Path


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
