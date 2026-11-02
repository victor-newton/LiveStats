"""Grava respostas REAIS de `videos.list` do YouTube Data API v3 como fixtures de teste.

Uso (da raiz do repositório)::

    python tests/worker_lives/record_fixtures.py \
        [--key-file secrets/youtube_api_key.txt] \
        [--config tests/worker_lives/fixtures_config.json] \
        [--out tests/worker_lives/fixtures] \
        [--base-url https://www.googleapis.com/youtube/v3]

Regras (docs/repo-layout.md, T4; decisão D31):

* a chave é lida SOMENTE de arquivo (padrão ``secrets/youtube_api_key.txt``, fora do Git) e vai no
  cabeçalho ``X-Goog-Api-Key``; não existe chave embutida nem leitura de variável de ambiente;
* sem o arquivo (ou vazio) o script termina com erro claro e não grava nada;
* só o corpo da resposta e metadados (``recorded_at``, status HTTP, ``Content-Type``) são gravados;
  todos os demais cabeçalhos da resposta são descartados;
* se o valor da chave aparecer em QUALQUER parte do que seria gravado, o script termina com código
  diferente de zero e não grava NENHUM arquivo;
* o script nunca imprime a chave.

O arquivo de configuração lista as requisições (nome do arquivo de saída, IDs, e opcionalmente a
situação esperada de cada vídeo no momento da gravação, para que uma fixture "ao vivo" seja de
fato ao vivo quando gravada).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_KEY_FILE = REPO_ROOT / "secrets" / "youtube_api_key.txt"
DEFAULT_CONFIG = Path(__file__).resolve().parent / "fixtures_config.json"
DEFAULT_OUT = Path(__file__).resolve().parent / "fixtures"
DEFAULT_BASE_URL = "https://www.googleapis.com/youtube/v3"
DEFAULT_PART = "statistics,snippet,liveStreamingDetails"
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
# Formato das chaves do Google; montado em partes para este arquivo não casar com o próprio padrão.
KEY_PATTERN = re.compile("AI" + "za[0-9A-Za-z_-]{35}")


class RecordError(Exception):
    """Falha que impede a gravação (nada é gravado)."""


def read_key(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise RecordError(f"arquivo da chave não encontrado: {path}") from None
    except (OSError, UnicodeDecodeError):
        raise RecordError(f"não foi possível ler o arquivo da chave: {path}") from None
    key = text.strip()
    if not key:
        raise RecordError(f"arquivo da chave está vazio: {path}")
    return key


def check_base_url(raw: str) -> str:
    parts = urllib.parse.urlsplit(raw)
    host = parts.hostname
    if parts.scheme == "https" and host:
        return raw.rstrip("/")
    if parts.scheme == "http" and host in LOOPBACK_HOSTS:
        return raw.rstrip("/")
    raise RecordError("base-url inválida: só https, ou http para localhost/127.0.0.1/::1")


def fetch(base_url: str, key: str, ids: list[str], part: str) -> tuple[int, str, bytes]:
    query = urllib.parse.urlencode({"part": part, "id": ",".join(ids)})
    request = urllib.request.Request(  # noqa: S310 - esquema validado em check_base_url
        f"{base_url}/videos?{query}", headers={"X-Goog-Api-Key": key}
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.headers.get("Content-Type", ""), error.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        raise RecordError("falha de rede ao chamar a API (a chave não é exibida)") from None


def build_fixture(spec: dict, base_url: str, real_key: str) -> tuple[str, str]:
    """Faz a chamada e devolve (nome do arquivo, texto JSON a gravar)."""
    name = spec["name"]
    ids = list(spec["ids"])
    part = spec.get("part", DEFAULT_PART)
    sent_key = spec.get("api_key", real_key)
    status, content_type, raw = fetch(base_url, sent_key, ids, part)
    expected_status = spec.get("expect_status", 200)
    if status != expected_status:
        raise RecordError(f"{name}: status HTTP {status}, esperado {expected_status}")
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RecordError(f"{name}: a resposta não é JSON") from None
    if status == 200:
        found = {item.get("id"): item for item in body.get("items", [])}
        for video_id, expected in spec.get("expect_broadcast", {}).items():
            item = found.get(video_id)
            actual = (item or {}).get("snippet", {}).get("liveBroadcastContent")
            if actual != expected:
                raise RecordError(
                    f"{name}: o vídeo {video_id} está '{actual}', esperado '{expected}' agora"
                )
    fixture = {
        "meta": {
            "source": "youtube-data-api-v3",
            "synthetic": False,
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "http_status": status,
            "content_type": content_type,
            "request": {"endpoint": "videos", "part": part, "id": ",".join(ids)},
        },
        "body": body,
    }
    return name, json.dumps(fixture, ensure_ascii=False, indent=2) + "\n"


def run(key_file: Path, config_file: Path, out_dir: Path, base_url: str) -> list[str]:
    base_url = check_base_url(base_url)
    key = read_key(key_file)
    try:
        specs = json.loads(config_file.read_text(encoding="utf-8"))["fixtures"]
    except (OSError, ValueError, KeyError):
        raise RecordError(f"configuração inválida ou ausente: {config_file}") from None

    pending: list[tuple[str, str]] = []
    for spec in specs:
        name, text = build_fixture(spec, base_url, key)
        # A chave real não pode aparecer em nenhuma parte do arquivo; nenhuma chave no formato do
        # Google pode aparecer de forma alguma.
        if key in text or KEY_PATTERN.search(text):
            raise RecordError(f"{name}: a chave apareceu no conteúdo a gravar; nada foi gravado")
        pending.append((name, text))

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, text in pending:
        (out_dir / f"{name}.json").write_text(text, encoding="utf-8", newline="\n")
    return [name for name, _ in pending]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--key-file", type=Path, default=DEFAULT_KEY_FILE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args(argv)
    try:
        names = run(args.key_file, args.config, args.out, args.base_url)
    except RecordError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1
    for name in names:
        print(f"gravado: {name}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
