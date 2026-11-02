"""LIV-FIX-01/02/03: integridade das fixtures reais e o script que as grava."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from lives_helpers import FIXTURES, REPO_ROOT

SCRIPT = Path(__file__).resolve().parent / "record_fixtures.py"
KEY_PATTERN = re.compile("AI" + "za[0-9A-Za-z_-]{35}")
RECORDED_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
META_KEYS = {"source", "synthetic", "recorded_at", "http_status", "content_type", "request", "note"}
SYNTHETIC = {
    "error_quota_exceeded",
    "error_rate_limited",
    "error_server_500",
    "error_server_503",
    "invalid_body_not_json",
    "invalid_body_no_items",
}
REAL = {"videos_mixed", "videos_upcoming_no_likes", "videos_partial_missing", "error_invalid_key"}


def _fixtures() -> dict[str, dict]:
    return {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(FIXTURES.glob("*.json"))}


# ---------------------------------------------------------------------------- LIV-FIX-01


def test_liv_fix_01_conjunto_esperado_de_fixtures():
    fixtures = _fixtures()

    assert {n for n, f in fixtures.items() if f["meta"]["synthetic"]} == SYNTHETIC
    assert {n for n, f in fixtures.items() if not f["meta"]["synthetic"]} == REAL


def test_liv_fix_01_metadados_e_nenhum_cabecalho_alem_do_content_type():
    for name, fixture in _fixtures().items():
        assert set(fixture) <= {"meta", "body", "body_text"}, name
        assert ("body" in fixture) != ("body_text" in fixture), name
        meta = fixture["meta"]
        assert set(meta) <= META_KEYS, name
        assert meta["source"] == "youtube-data-api-v3", name
        assert isinstance(meta["synthetic"], bool), name
        assert isinstance(meta["http_status"], int), name
        assert isinstance(meta["content_type"], str) and meta["content_type"], name
        assert "recorded_at" in meta, name
        if meta["synthetic"]:
            assert meta["recorded_at"] is None, name
            assert meta["note"], name  # explica por que é sintética
        else:
            assert RECORDED_AT.match(meta["recorded_at"]), name


def test_liv_fix_01_sem_chave_nas_fixtures():
    chave_real = REPO_ROOT / "secrets" / "youtube_api_key.txt"
    valor = chave_real.read_text(encoding="utf-8").strip() if chave_real.exists() else None
    for path in sorted(FIXTURES.glob("*.json")):
        texto = path.read_text(encoding="utf-8")
        assert not KEY_PATTERN.search(texto), path.name
        if valor:
            assert valor not in texto, path.name


def test_liv_fix_01_fixtures_de_video_tem_a_estrutura_real():
    fixtures = _fixtures()
    broadcast = set()
    for name in ("videos_mixed", "videos_upcoming_no_likes", "videos_partial_missing"):
        meta, body = fixtures[name]["meta"], fixtures[name]["body"]
        assert meta["http_status"] == 200
        assert meta["content_type"].startswith("application/json")
        assert body["kind"] == "youtube#videoListResponse"
        assert body["items"], name
        for item in body["items"]:
            assert item["kind"] == "youtube#video"
            assert "liveBroadcastContent" in item["snippet"]
            broadcast.add(item["snippet"]["liveBroadcastContent"])
    assert {"live", "upcoming", "none"} <= broadcast  # ao vivo, agendado e encerrado

    live = next(i for i in fixtures["videos_mixed"]["body"]["items"] if i["snippet"]["liveBroadcastContent"] == "live")
    assert "concurrentViewers" in live["liveStreamingDetails"]
    (no_likes,) = fixtures["videos_upcoming_no_likes"]["body"]["items"]
    assert "likeCount" not in no_likes["statistics"]
    partial = fixtures["videos_partial_missing"]
    assert len(partial["meta"]["request"]["id"].split(",")) == 2
    assert len(partial["body"]["items"]) == 1  # o ID inexistente não veio em items


def test_liv_fix_01_erro_de_chave_invalida_e_real_e_os_sinteticos_seguem_o_formato_do_google():
    fixtures = _fixtures()
    invalid = fixtures["error_invalid_key"]
    assert invalid["meta"]["http_status"] == 400 == invalid["body"]["error"]["code"]
    assert invalid["meta"]["synthetic"] is False
    assert "API key not valid" in invalid["body"]["error"]["message"]

    esperado = {
        "error_quota_exceeded": (403, "quotaExceeded"),
        "error_rate_limited": (429, "rateLimitExceeded"),
        "error_server_500": (500, "backendError"),
        "error_server_503": (503, "backendError"),
    }
    for name, (status, reason) in esperado.items():
        fixture = fixtures[name]
        assert fixture["meta"]["http_status"] == status == fixture["body"]["error"]["code"]
        assert fixture["body"]["error"]["errors"][0]["reason"] == reason
    assert fixtures["invalid_body_not_json"]["meta"]["http_status"] == 200
    assert not fixtures["invalid_body_not_json"]["meta"]["content_type"].startswith("application/json")


# ---------------------------------------------------------------------------- servidor local


class _Handler(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def do_GET(self):  # noqa: N802 - nome exigido pelo http.server
        parts = urlsplit(self.path)
        ids = parse_qs(parts.query).get("id", [""])[0]
        type(self).seen.append({"path": parts.path, "query": parts.query, "headers": dict(self.headers)})
        title = type(self).leak if ids == "leak" else "titulo limpo"
        body = {"kind": "youtube#videoListResponse", "items": [{"kind": "youtube#video", "id": ids, "snippet": {"title": title}}]}
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=UTF-8")
        self.send_header("Set-Cookie", "sessao=valor-do-cookie")
        self.send_header("X-Cabecalho-Secreto", "valor-secreto")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    handler = type("Handler", (_Handler,), {"seen": [], "leak": ""})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{httpd.server_address[1]}/youtube/v3"
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def _record(
    tmp_path: Path,
    base_url: str,
    specs: list[dict],
    key_file: Path | None,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"fixtures": specs}), encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("YOUTUBE_")}
    env.update(extra_env or {})
    args = [sys.executable, str(SCRIPT), "--config", str(config), "--out", str(tmp_path / "out"), "--base-url", base_url]
    if key_file is not None:
        args += ["--key-file", str(key_file)]
    return subprocess.run(args, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture
def secret(tmp_path):
    valor = "chave-de-teste-" + os.urandom(12).hex()
    arquivo = tmp_path / "chave.txt"
    arquivo.write_text(valor + "\n", encoding="utf-8")
    return valor, arquivo


# ---------------------------------------------------------------------------- LIV-FIX-02


def test_liv_fix_02_falha_e_nao_grava_nada_se_a_chave_aparece_na_resposta(tmp_path, server, secret):
    handler, base_url = server
    valor, arquivo = secret
    handler.leak = f"titulo que ecoa {valor} por engano"
    specs = [{"name": "limpa", "ids": ["ok1"]}, {"name": "vaza", "ids": ["leak"]}]

    result = _record(tmp_path, base_url, specs, arquivo)

    assert result.returncode != 0
    assert valor not in result.stdout + result.stderr  # o script também não imprime a chave
    out = tmp_path / "out"
    assert not out.exists() or list(out.iterdir()) == []  # nem a fixture limpa foi gravada


def test_liv_fix_02_falha_se_aparecer_qualquer_chave_no_formato_do_google(tmp_path, server, secret):
    handler, base_url = server
    _, arquivo = secret
    handler.leak = "outra chave " + "AI" + "za" + "B" * 35

    result = _record(tmp_path, base_url, [{"name": "vaza", "ids": ["leak"]}], arquivo)

    assert result.returncode != 0
    assert not (tmp_path / "out").exists()


def test_liv_fix_02_resposta_limpa_grava_so_corpo_e_metadados(tmp_path, server, secret):
    handler, base_url = server
    valor, arquivo = secret

    result = _record(tmp_path, base_url, [{"name": "limpa", "ids": ["ok1", "ok2"]}], arquivo)

    assert result.returncode == 0, result.stderr
    gravado = tmp_path / "out" / "limpa.json"
    fixture = json.loads(gravado.read_text(encoding="utf-8"))
    assert set(fixture) == {"meta", "body"}
    assert set(fixture["meta"]) <= META_KEYS
    assert fixture["meta"]["http_status"] == 200
    assert fixture["meta"]["synthetic"] is False
    assert fixture["meta"]["content_type"] == "application/json; charset=UTF-8"
    assert RECORDED_AT.match(fixture["meta"]["recorded_at"])
    assert fixture["body"]["items"][0]["snippet"]["title"] == "titulo limpo"
    texto = gravado.read_text(encoding="utf-8")
    for descartado in ("Set-Cookie", "valor-do-cookie", "X-Cabecalho-Secreto", "valor-secreto", valor):
        assert descartado not in texto


# ---------------------------------------------------------------------------- LIV-FIX-03


def test_liv_fix_03_chave_so_do_arquivo_e_no_cabecalho(tmp_path, server, secret):
    handler, base_url = server
    valor, arquivo = secret

    result = _record(tmp_path, base_url, [{"name": "limpa", "ids": ["ok1"]}], arquivo)

    assert result.returncode == 0, result.stderr
    (visto,) = handler.seen
    headers = {k.lower(): v for k, v in visto["headers"].items()}
    assert headers["x-goog-api-key"] == valor
    assert visto["path"] == "/youtube/v3/videos"
    assert "key" not in parse_qs(visto["query"])
    assert valor not in visto["query"]
    assert parse_qs(visto["query"])["part"] == ["statistics,snippet,liveStreamingDetails"]


@pytest.mark.parametrize("conteudo", [None, "", "  \n"])
def test_liv_fix_03_sem_arquivo_de_chave_erro_claro_e_nada_gravado(tmp_path, server, conteudo):
    handler, base_url = server
    arquivo = tmp_path / "chave.txt"
    if conteudo is not None:
        arquivo.write_text(conteudo, encoding="utf-8")

    result = _record(tmp_path, base_url, [{"name": "x", "ids": ["ok1"]}], arquivo)

    assert result.returncode != 0
    assert "chave" in result.stderr
    assert not (tmp_path / "out").exists()
    assert handler.seen == []  # nenhuma requisição foi feita


def test_liv_fix_03_variavel_de_ambiente_nao_substitui_o_arquivo(tmp_path, server):
    handler, base_url = server

    result = _record(
        tmp_path, base_url, [{"name": "x", "ids": ["ok1"]}], tmp_path / "nao-existe.txt",
        extra_env={"YOUTUBE_API_KEY": "valor-do-ambiente", "YOUTUBE_API_KEY_FILE": "/nao/existe"},
    )

    assert result.returncode != 0
    assert handler.seen == []  # com a variável definida e sem arquivo, nenhuma chamada é feita
    assert not (tmp_path / "out").exists()


def test_liv_fix_03_base_url_insegura_e_recusada(tmp_path, secret):
    _, arquivo = secret

    result = _record(tmp_path, "http://exemplo.test/youtube/v3", [{"name": "x", "ids": ["ok1"]}], arquivo)

    assert result.returncode != 0
    assert not (tmp_path / "out").exists()


def test_liv_fix_03_script_nao_tem_chave_embutida_nem_le_o_ambiente():
    spec = importlib.util.spec_from_file_location("record_fixtures_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    codigo = SCRIPT.read_text(encoding="utf-8")

    assert module.DEFAULT_KEY_FILE == REPO_ROOT / "secrets" / "youtube_api_key.txt"
    assert "environ" not in codigo and "getenv" not in codigo
    assert not KEY_PATTERN.search(codigo)
    assert "X-Goog-Api-Key" in codigo
