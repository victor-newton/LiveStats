"""Auxiliares dos testes do worker-lives.

Fronteiras simuladas (regra anti-burla 4): SOMENTE o HTTP do YouTube (respx) e o relógio. Os itens
de vídeo servidos pelo dublê HTTP vêm das respostas REAIS gravadas em `fixtures/`; o dublê apenas
escolhe, entre os itens reais gravados, os que o `id` da requisição pede (como a API faz). O banco
é SQLite real, aplicado a partir de `db/schema.sql` (o papel da api).
"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEMA_SQL = REPO_ROOT / "db" / "schema.sql"
GOLDEN = REPO_ROOT / "tests" / "contract" / "legacy"
DEFAULT_BASE = "https://www.googleapis.com/youtube/v3"

# IDs reais gravados (tests/worker_lives/fixtures_config.json)
LIVE_ID = "At5J757Nqgo"
UPCOMING_ID = "dXjFZapxqcc"
ENDED_ID = "zrxGZ-XpKnw"
NO_LIKES_ID = "5dzFmvKHmYY"
MISSING_ID = "zzzzzzzzzzz"


def watch(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def epoch(iso_z: str) -> float:
    """`2026-11-02T14:32:00.123456Z` -> epoch (UTC)."""
    moment = datetime.strptime(iso_z, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return moment.timestamp()


# ---------------------------------------------------------------------------- relógio


class FakeClock:
    """Relógio falso: `sleep` avança o tempo e chama `hook` (se houver)."""

    def __init__(self, start: float = 1_793_000_000.5) -> None:
        self.t = start
        self.sleeps: list[float] = []
        self.hook = None

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.t += seconds
        if self.hook is not None:
            self.hook(self)


# ---------------------------------------------------------------------------- fixtures reais


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def fixture_response(name: str) -> httpx.Response:
    """Reproduz uma fixture gravada (status, Content-Type e corpo) como `httpx.Response`."""
    fixture = load_fixture(name)
    meta = fixture["meta"]
    if "body_text" in fixture:
        content = fixture["body_text"].encode("utf-8")
    else:
        content = json.dumps(fixture["body"], ensure_ascii=False).encode("utf-8")
    return httpx.Response(
        meta["http_status"], headers={"Content-Type": meta["content_type"]}, content=content
    )


def real_catalog() -> dict[str, dict]:
    """Itens de vídeo REAIS (de todas as fixtures de `videos.list` bem-sucedidas), por ID."""
    catalog: dict[str, dict] = {}
    for name in ("videos_mixed", "videos_upcoming_no_likes", "videos_partial_missing"):
        for item in load_fixture(name)["body"]["items"]:
            catalog.setdefault(item["id"], item)
    return catalog


class FakeYouTube:
    """Dublê da fronteira HTTP do YouTube (respx) com os itens reais gravados."""

    def __init__(self, router, base_url: str = DEFAULT_BASE) -> None:
        self.catalog = copy.deepcopy(real_catalog())
        self.requests: list[httpx.Request] = []
        self.queue: list = []
        self.on_request = None
        parts = urlsplit(base_url)
        pattern = {"method": "GET", "scheme": parts.scheme, "host": parts.hostname}
        pattern["path"] = f"{parts.path}/videos"
        if parts.port is not None:
            pattern["port"] = parts.port
        self.route = router.route(**pattern).mock(side_effect=self._respond)

    def enqueue(self, *responses) -> None:
        """Respostas (ou exceções) consumidas na ordem, uma por requisição."""
        self.queue.extend(responses)

    def _respond(self, request: httpx.Request):
        self.requests.append(request)
        if self.on_request is not None:
            self.on_request(self, request)
        if self.queue:
            item = self.queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        wanted = request.url.params["id"].split(",")
        body = {
            "kind": "youtube#videoListResponse",
            "etag": "fake",
            "items": [copy.deepcopy(self.catalog[i]) for i in wanted if i in self.catalog],
            "pageInfo": {"totalResults": len(wanted), "resultsPerPage": len(wanted)},
        }
        return httpx.Response(
            200, headers={"Content-Type": "application/json; charset=UTF-8"}, json=body
        )


# ---------------------------------------------------------------------------- banco (papel da api)


def create_schema(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    finally:
        conn.close()


def set_slots(db_path: Path, urls: list[str]) -> None:
    assert len(urls) == 4
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            for slot, url in enumerate(urls, start=1):
                conn.execute("UPDATE slots SET url = ? WHERE slot = ?", (url, slot))
    finally:
        conn.close()


def _query(db_path: Path, sql: str, args: tuple = ()) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def live_rows(db_path: Path) -> dict[int, dict]:
    return {r["slot"]: r for r in _query(db_path, "SELECT * FROM live_stats ORDER BY slot")}


def worker_status(db_path: Path) -> dict | None:
    rows = _query(db_path, "SELECT * FROM worker_status WHERE worker = 'lives'")
    return rows[0] if rows else None


# ---------------------------------------------------------------------------- formato legado


def legacy_json(row: dict) -> bytes:
    """Serializa uma linha de `live_stats` como o legado (docs/legacy-formats.md §0 e §1.2)."""
    if row["active"]:
        obj = {
            "video_id": row["video_id"],
            "titulo": row["title"],
            "status": row["status"],
            "views": row["views"],
            "likes": row["likes"],
            "views_formatado": row["views_formatted"],
            "likes_formatado": row["likes_formatted"],
            "atualizado_em": row["updated_at"],
            "slot": row["slot"],
            "ativo": "true",
        }
    else:
        obj = {
            "slot": row["slot"],
            "ativo": "false",
            "video_id": row["video_id"],
            "titulo": row["title"],
            "status": row["status"],
            "views": row["views"],
            "likes": row["likes"],
            "views_formatado": row["views_formatted"],
            "likes_formatado": row["likes_formatted"],
            "atualizado_em": row["updated_at"],
        }
    return json.dumps([obj], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
