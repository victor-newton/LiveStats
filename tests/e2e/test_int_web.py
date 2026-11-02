"""INT-08 (suite tests/web contra o Nginx real) e INT-12 (limites publicos) pela pilha real."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time

import pytest
from e2e_browser import chromium_context
from e2e_stack import REPO, header

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


def test_int_08_painel_suite_web_contra_a_pilha_real_na_8081(stack):
    stack.ensure_lives()
    summary = _run_web_suite("tests/web", "http://127.0.0.1:8081")
    assert "passed" in summary


def test_int_09_overlay_suite_web_contra_a_porta_publica_8080(stack):
    summary = _run_web_suite("tests/web/test_web_overlay.py", "http://127.0.0.1:8080")
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
