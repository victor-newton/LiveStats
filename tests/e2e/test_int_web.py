# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""INT-08 (suite tests/web contra o Nginx real) e INT-12 (limites publicos) pela pilha real."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from e2e_browser import chromium_context
from e2e_stack import PYTHON_IMAGE, REPO, docker, header, http_request, wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]


def _run_web_suite(target: str, base_url: str) -> str:
    env = {**os.environ, "WEB_BASE_URL": base_url, "PYTHONIOENCODING": "utf-8"}
    result = subprocess.run(
        [sys.executable, "-m", "pytest", target, "-q", "--tb=short", "-p", "no:cacheprovider"],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=1800, encoding="utf-8",
    )
    tail = (result.stdout + result.stderr)[-4000:]
    assert result.returncode == 0, f"{target} contra {base_url} falhou:\n{tail}"
    summary = result.stdout.strip().splitlines()[-1]
    assert re.search(r"\b\d+ passed\b", summary), summary
    assert not re.search(r"\b(failed|error|errors|skipped|xfailed|xpassed|deselected)\b", summary), summary
    return summary


@contextmanager
def _visitor_proxy(stack, last_octet: int, host_port: int, upstream_port: int, header_name: str):
    """Container na edge, publicado so em 127.0.0.1. 8080: IP fixo do cloudflared do teste + CF-Connecting-IP.
    8081 (D33, `header_name == "source-ip"`): HTTPS ate o web, com um IP de ORIGEM diferente por requisicao."""
    name = f"t8-ip-proxy-{host_port}"
    docker("rm", "-f", name, check=False)
    extra: list[str] = []
    image = PYTHON_IMAGE
    if header_name == "source-ip":
        image = "python:3.12-alpine"  # traz o `ip` (busybox) para adicionar o bloco de IPs de origem
        extra = [
            "--cap-add", "NET_ADMIN", "-e", f"PROXY_BASE={stack.base}", "-e", "PROXY_TLS_CA=/srv/admin_tls_cert.pem",
            "-v", f"{stack.cert}:/srv/admin_tls_cert.pem:ro",
        ]
    docker(
        "run", "-d", "--name", name, "--label", "livestats-t8-extra=1", "--network", "livestats-t8-edge",
        "--ip", f"{stack.base}.{last_octet}", "-p", f"127.0.0.1:{host_port}:8888", *extra,
        "-v", f"{Path(__file__).resolve().parent / 'ip_proxy.py'}:/srv/ip_proxy.py:ro",
        image, "python", "/srv/ip_proxy.py", "8888", "web", str(upstream_port), header_name,
    )
    try:
        wait_until(lambda: http_request("127.0.0.1", host_port, "/health", timeout=5)["status"] == 200, timeout=30, interval=1)
        yield f"http://127.0.0.1:{host_port}"
    finally:
        docker("rm", "-f", name, check=False)


def test_int_08_painel_suite_web_contra_a_pilha_real_na_8081(stack):
    stack.ensure_lives()
    # Visitantes distintos por requisicao (IP de origem diferente a cada uma, como varios usuarios na LAN): a suite funcional tem ~90 testes de um unico
    # IP e, direto no Nginx, bateria no limit_req (comprovado: 27 respostas 429 em estaticos). O limite e testado em INT-12.
    with _visitor_proxy(stack, 21, 18081, 8081, "source-ip") as base:
        summary = _run_web_suite("tests/web", base)
    assert "passed" in summary


def test_int_09_overlay_suite_web_contra_a_porta_publica_8080(stack):
    with _visitor_proxy(stack, 20, 18080, 8080, "CF-Connecting-IP") as base:
        summary = _run_web_suite("tests/web/test_web_overlay.py", base)
    assert "passed" in summary


def test_int_12_overlays_nunca_sao_limitados_mas_rajadas_sim(stack):
    stack.ensure_subs("460600")
    time.sleep(4)  # esvazia o balde do limite do IP do host antes de medir

    # overlay real (Chromium) consultando a cada 5 s por ~35 s: nenhuma resposta 429
    with chromium_context() as ctx:
        page = ctx.new_page()
        statuses: list[tuple[str, int]] = []
        page.on("response", lambda r: statuses.append((r.url, r.status)))
        page.goto("http://127.0.0.1:8080/contador")
        page.wait_for_timeout(35000)
    polls = [s for u, s in statuses if "/api/public/subscribers" in u]
    assert len(polls) >= 6, polls
    assert all(s == 200 for s in polls), polls
    assert all(s < 400 for _, s in statuses), statuses

    # 100 requisicoes rapidas de um unico IP => algumas 429, em JSON, com Retry-After
    out = stack.client(41, [{"path": "/api/public/subscribers", "repeat": 100}], port=8080)
    codes = [r["status"] for r in out]
    assert len(codes) == 100
    assert codes.count(429) >= 20, f"limite nao agiu: {codes.count(429)} respostas 429"
    first_429 = codes.index(429)
    assert first_429 >= 25, f"o limite cortou cedo demais (apos {first_429} respostas)"
    assert set(codes) <= {200, 429}
    limited = next(r for r in out if r["status"] == 429)
    assert json.loads(limited["body"]) == {"detail": "Muitas tentativas. Tente novamente em instantes."}
    assert header(limited, "Retry-After")
    time.sleep(4)
