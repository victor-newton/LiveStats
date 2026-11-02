"""Cliente do `videos.list` do YouTube Data API v3 (uma chamada em lote por ciclo).

Segurança (D14, D18): a chave vai no cabeçalho `X-Goog-Api-Key`, nunca na URL; e nenhuma exceção
nem corpo de resposta é copiado para mensagens/logs. Cada falha vira um `YouTubeError` com código do
enum fechado de `worker_status.last_error_code` e uma mensagem fixa e curta.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

PART = "statistics,snippet,liveStreamingDetails"
REQUEST_TIMEOUT = 10.0

_QUOTA_REASONS = frozenset({"quotaExceeded", "dailyLimitExceeded"})
_RATE_REASONS = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})
_KEY_REASONS = frozenset({"keyInvalid", "keyExpired", "ipRefererBlocked", "accessNotConfigured"})


class YouTubeError(Exception):
    """Falha ao consultar o YouTube. `code` ∈ enum de `worker_status.last_error_code`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class VideoInfo:
    video_id: str
    title: str
    status: str  # ao_vivo | agendada | encerrada
    views: int
    likes: int


def fetch_videos(
    client: httpx.Client, base_url: str, api_key: str, video_ids: list[str]
) -> dict[str, VideoInfo]:
    """Uma chamada `GET {base_url}/videos` para todos os IDs; devolve os vídeos que vieram."""
    try:
        response = client.get(
            f"{base_url}/videos",
            params={"id": ",".join(video_ids), "part": PART},
            headers={"X-Goog-Api-Key": api_key},
        )
    except httpx.TimeoutException:
        raise YouTubeError("network_error", "Tempo esgotado ao consultar o YouTube") from None
    except httpx.HTTPError:
        raise YouTubeError("network_error", "Falha de rede ao consultar o YouTube") from None

    if response.status_code == 200:
        return _parse_videos(response)
    raise _error_for(response)


def _error_for(response: httpx.Response) -> YouTubeError:
    status = response.status_code
    reasons, detail_reasons = _error_reasons(response)
    if reasons & _QUOTA_REASONS:
        return YouTubeError("quota_exceeded", "Cota da API do YouTube esgotada")
    if status == 429 or reasons & _RATE_REASONS:
        return YouTubeError("rate_limited", "Limite de requisições da API do YouTube atingido")
    if reasons & _KEY_REASONS or any(r.startswith("API_KEY_") for r in detail_reasons):
        return YouTubeError(
            "config_error", "Chave da API recusada pelo YouTube (verifique o secret youtube_api_key)"
        )
    return YouTubeError("http_error", f"Erro HTTP {status} da API do YouTube")


def _error_reasons(response: httpx.Response) -> tuple[set[str], set[str]]:
    """Extrai `error.errors[].reason` e `error.details[].reason` do corpo de erro (se houver)."""
    try:
        error = response.json()["error"]
        reasons = {e["reason"] for e in error.get("errors", []) if isinstance(e.get("reason"), str)}
        details = {d["reason"] for d in error.get("details", []) if isinstance(d.get("reason"), str)}
    except (ValueError, KeyError, TypeError, AttributeError):
        return set(), set()
    return reasons, details


def _invalid() -> YouTubeError:
    return YouTubeError("invalid_response", "Resposta inválida da API do YouTube")


def _parse_videos(response: httpx.Response) -> dict[str, VideoInfo]:
    try:
        payload = response.json()
    except ValueError:
        raise _invalid() from None
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise _invalid()

    videos: dict[str, VideoInfo] = {}
    for item in payload["items"]:
        info = _parse_item(item)
        videos[info.video_id] = info
    return videos


def _section(item: dict, name: str) -> dict:
    value = item.get(name)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _invalid()
    return value


def _to_int(value: object) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise _invalid()
    try:
        number = int(value)
    except ValueError:
        raise _invalid() from None
    if number < 0:
        raise _invalid()
    return number


def _parse_item(item: object) -> VideoInfo:
    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
        raise _invalid()
    statistics = _section(item, "statistics")
    snippet = _section(item, "snippet")
    live = _section(item, "liveStreamingDetails")

    broadcast = snippet.get("liveBroadcastContent")
    if broadcast == "live":
        status = "ao_vivo"
    elif broadcast == "upcoming":
        status = "agendada"
    else:
        status = "encerrada"

    title = snippet.get("title", "")
    if not isinstance(title, str):
        raise _invalid()

    views = _to_int(statistics.get("viewCount"))
    likes = _to_int(statistics.get("likeCount"))
    if status == "ao_vivo":
        concurrent = _to_int(live.get("concurrentViewers"))
        if concurrent > 0:
            views = concurrent

    return VideoInfo(video_id=item["id"], title=title, status=status, views=views, likes=likes)
