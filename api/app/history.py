"""Historico, saude e eventos do painel v3 (D43, D44, D45): nome e cor das lives, eventos da api e as consultas das rotas novas.

Tudo aqui so LE as tabelas que os workers gravam (``live_samples``, ``set_peaks``, ``subscriber_samples``,
``live_reads``, ``health_minutes``, ``events``); a unica escrita da api e ``events`` com ``source = 'api'``.
Datas sao epoch em segundos (UTC); o dia do pico e dos inscritos usa o fuso America/Sao_Paulo.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    SP: tzinfo = ZoneInfo("America/Sao_Paulo")
except ZoneInfoNotFoundError:  # imagem sem tzdata: Sao Paulo esta em UTC-3 fixo desde 2019
    SP = timezone(timedelta(hours=-3))

# ---------------------------------------------------------------------------- D44: cor automatica e nome
PALETTES: dict[str, tuple[str, ...]] = {
    "youtube": ("#FF4141", "#FFA48F", "#B3123A", "#FF7AA2"),
    "twitch": ("#A970FF", "#D6B8FF", "#6E3BD1"),
    "kick": ("#53FC18", "#B4FF8A", "#2E9E0B"),
}
_PLATFORM_LABEL = {"youtube": "YouTube", "twitch": "Twitch", "kick": "Kick"}
_TITLE_MAX = 40


def next_color(platform: str, used: list[str]) -> str:
    """Primeiro tom da paleta da plataforma ainda nao usado no conjunto; se todos estao usados, recomeca do primeiro."""
    palette = PALETTES[platform]
    taken = {color.upper() for color in used}
    for tone in palette:
        if tone not in taken:
            return tone
    return palette[sum(1 for color in used if color.upper() in palette) % len(palette)]


def display_name(platform: str, ref: str, label: str, title: str | None) -> str:
    """Nome mostrado no painel (calculado, nunca gravado): ``label``; senao pela plataforma, ref e ultimo titulo lido."""
    if label:
        return label
    suffix = _PLATFORM_LABEL[platform]
    if platform != "youtube":
        return f"{ref} · {suffix}"
    if ref.startswith("@") or ref.startswith("UC"):
        return f"{ref} · {suffix}"
    short = (title or "").strip()[:_TITLE_MAX].strip()
    return f"{short} · {suffix}" if short else f"{suffix} {ref}"


# ---------------------------------------------------------------------------- D43: eventos da api
API_EVENTS_MAX = 500


def record_event(conn: sqlite3.Connection, now: float, level: str, code: str, message: str, live_id: int | None = None) -> None:
    """Acrescenta um evento ``source = 'api'`` e mantem no maximo 500 deles. Chamar DENTRO da transacao da acao."""
    conn.execute(
        "INSERT INTO events (at, source, level, code, message, live_id) VALUES (?, 'api', ?, ?, ?, ?)",
        (int(now), level, code[:40], message[:200], live_id),
    )
    conn.execute(
        "DELETE FROM events WHERE source = 'api' AND id <= "
        "(SELECT id FROM events WHERE source = 'api' ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (API_EVENTS_MAX,),
    )


# ---------------------------------------------------------------------------- D45: historico do conjunto
RANGES = {5: 10, 30: 30, 60: 60, 120: 60}  # range_min -> step_s
STALE_S = 45  # uma live sem amostra nova continua somando por ate 45 s (uma leitura perdida nao abre um buraco)
CHANGE_WINDOW_S = 15 * 60


def day_of(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, SP).strftime("%Y-%m-%d")


def _day_start(epoch: float) -> int:
    local = datetime.fromtimestamp(epoch, SP).replace(hour=0, minute=0, second=0, microsecond=0)
    return int(local.timestamp())


def _total_at(samples: dict[int, list[tuple[int, int | None, int | None]]], t: int) -> int | None:
    """Soma, no instante ``t``, o ultimo valor de cada live (amostra dos ``STALE_S`` s anteriores); None sem nenhuma."""
    total, seen = 0, False
    for rows in samples.values():
        last = None
        for row in rows:
            if row[0] <= t:
                last = row
            else:
                break
        if last is not None and t - last[0] <= STALE_S:
            total += last[1] or 0
            seen = True
    return total if seen else None


def set_history(conn: sqlite3.Connection, set_id: int, range_min: int, now: float) -> dict[str, Any]:
    step = RANGES[range_min]
    now_i = int(now)
    last_bucket = now_i - now_i % step
    count = range_min * 60 // step
    first_bucket = last_bucket - (count - 1) * step
    live_ids = [r[0] for r in conn.execute("SELECT id FROM lives WHERE set_id = ?", (set_id,))]

    samples: dict[int, list[tuple[int, int | None, int | None]]] = {live_id: [] for live_id in live_ids}
    since = min(first_bucket, now_i - CHANGE_WINDOW_S - STALE_S) - STALE_S
    if live_ids:
        marks = ",".join("?" * len(live_ids))
        for row in conn.execute(  # noqa: S608 (so "?" montados)
            f"SELECT live_id, at, viewers, likes FROM live_samples WHERE live_id IN ({marks}) AND at >= ? AND at <= ? "
            "ORDER BY live_id, at",
            (*live_ids, since, now_i),
        ):
            samples[row[0]].append((row[1], row[2], row[3]))

    points: list[dict[str, Any]] = []
    cursor = {live_id: -1 for live_id in live_ids}  # indice da ultima amostra com at < fim do balde
    previous: dict[str, Any] | None = None
    for i in range(count):
        start = first_bucket + i * step
        end = start + step
        viewers, likes, seen = 0, 0, False
        has_likes = False
        for live_id, rows in samples.items():
            idx = cursor[live_id]
            while idx + 1 < len(rows) and rows[idx + 1][0] < end:
                idx += 1
            cursor[live_id] = idx
            if idx < 0:
                continue
            at, v, lk = rows[idx]
            if end - at > step + STALE_S:  # amostra velha demais: a live nao entra neste balde
                continue
            seen = True
            viewers += v or 0
            if lk is not None:
                likes += lk
                has_likes = True
        if seen:
            previous = {"at": start, "viewers": viewers, "likes": likes if has_likes else None}
            points.append(previous)
        elif previous is not None:  # balde sem amostra repete o anterior
            previous = {**previous, "at": start}
            points.append(previous)

    peak_row = conn.execute(
        "SELECT viewers, at FROM set_peaks WHERE set_id = ? AND day = ?", (set_id, day_of(now_i))
    ).fetchone()
    current, before = _total_at(samples, now_i), _total_at(samples, now_i - CHANGE_WINDOW_S)
    change = round((current - before) / before * 100, 1) if current is not None and before else None
    return {
        "range_min": range_min,
        "step_s": step,
        "points": points,
        "peak_today": {"viewers": peak_row[0], "at": peak_row[1]} if peak_row else None,
        "change_15m_pct": change,
    }


# ---------------------------------------------------------------------------- D45: historico de inscritos
def subscribers_history(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
    now_i = int(now)
    today_start = _day_start(now_i)
    first_day_start = _day_start(today_start - 6 * 86400 + 3600)  # meia-noite local de 6 dias atras
    rows = conn.execute(
        "SELECT at, value FROM subscriber_samples WHERE at >= ? AND at <= ? ORDER BY at", (first_day_start, now_i)
    ).fetchall()
    last_by_day: dict[str, int] = {}
    for at, value in rows:
        last_by_day[day_of(at)] = value  # ordenado por "at": o ultimo valor do dia vence
    days = [{"day": day, "value": value} for day, value in sorted(last_by_day.items())]

    today_gain = last_hour_gain = None
    if rows and rows[-1][0] >= today_start:
        latest = rows[-1][1]
        before_today = conn.execute(
            "SELECT value FROM subscriber_samples WHERE at < ? ORDER BY at DESC LIMIT 1", (today_start,)
        ).fetchone()
        first_today = next((value for at, value in rows if at >= today_start), latest)
        today_gain = latest - (before_today[0] if before_today else first_today)
    if rows and rows[-1][0] >= now_i - 3600:
        latest = rows[-1][1]
        at_or_before = conn.execute(
            "SELECT value FROM subscriber_samples WHERE at <= ? ORDER BY at DESC LIMIT 1", (now_i - 3600,)
        ).fetchone()
        first_hour = next((value for at, value in rows if at > now_i - 3600), latest)
        last_hour_gain = latest - (at_or_before[0] if at_or_before else first_hour)
    return {"days": days, "today_gain": today_gain, "last_hour_gain": last_hour_gain}


# ---------------------------------------------------------------------------- D45: saude em detalhe
MINUTES = 60
EVENTS_SHOWN = 50
_WORKER_LABEL = {"lives": "lives", "inscritos": "inscritos"}


def health_detail(conn: sqlite3.Connection, now: float) -> dict[str, Any]:
    now_i = int(now)
    current = now_i - now_i % 60
    first = current - (MINUTES - 1) * 60
    rows: dict[str, dict[int, sqlite3.Row]] = {"lives": {}, "inscritos": {}}
    for row in conn.execute(
        "SELECT worker, minute, avg_ms, max_ms, ok, errors FROM health_minutes WHERE minute >= ? AND minute <= ?",
        (first, current),
    ):
        rows[row[0]][row[1]] = row

    minutes: list[dict[str, Any]] = []
    for i in range(MINUTES):
        minute = first + i * 60
        notes: list[str] = []
        for worker, per_minute in rows.items():
            row = per_minute.get(minute)
            if row is not None:
                if row["errors"] > 0:
                    notes.append(f"{row['errors']} erro(s) de leitura em {_WORKER_LABEL[worker]}")
            elif per_minute and _ok_around(per_minute, minute, first, current):
                notes.append(f"sem batimento de {_WORKER_LABEL[worker]}")
        minutes.append(
            {
                "minute": minute,
                "lives_avg_ms": rows["lives"][minute]["avg_ms"] if minute in rows["lives"] else None,
                "inscritos_avg_ms": rows["inscritos"][minute]["avg_ms"] if minute in rows["inscritos"] else None,
                "incident": bool(notes),
                "note": ("; ".join(notes))[:200] if notes else None,
            }
        )

    reads = []
    for r in conn.execute(
        "SELECT l.id, l.set_id, s.name AS set_name, l.platform, l.ref, l.label, l.color, st.status, st.title, "
        "lr.latency_ms, lr.last_read_at, lr.next_read_at, lr.error_code "
        "FROM lives l JOIN sets s ON s.id = l.set_id "
        "LEFT JOIN live_stats st ON st.live_id = l.id LEFT JOIN live_reads lr ON lr.live_id = l.id "
        "ORDER BY s.position, s.id, l.position, l.id"
    ):
        reads.append(
            {
                "live_id": r["id"],
                "set_id": r["set_id"],
                "set_name": r["set_name"],
                "display_name": display_name(r["platform"], r["ref"], r["label"], r["title"]),
                "platform": r["platform"],
                "color": r["color"],
                "status": r["status"],
                "latency_ms": r["latency_ms"],
                "last_read_at": r["last_read_at"],
                "next_read_at": r["next_read_at"],
                "error_code": r["error_code"],
            }
        )
    events = [
        {"at": e["at"], "level": e["level"], "source": e["source"], "message": e["message"]}
        for e in conn.execute("SELECT at, level, source, message FROM events ORDER BY at DESC, id DESC LIMIT ?", (EVENTS_SHOWN,))
    ]
    return {"minutes": minutes, "reads": reads, "events": events}


def _ok_around(per_minute: dict[int, sqlite3.Row], minute: int, first: int, current: int) -> bool:
    """Minuto ausente com o worker em ``ok`` (sem erros) no minuto de antes e no de depois mais proximos."""
    before = next((per_minute[m] for m in range(minute - 60, first - 60, -60) if m in per_minute), None)
    after = next((per_minute[m] for m in range(minute + 60, current + 60, 60) if m in per_minute), None)
    return before is not None and after is not None and before["errors"] == 0 and after["errors"] == 0
