"""INFRA-DEP-01: deploy/lib.sh troca o DOCKER_CONFIG quando a home tem um .docker ilegivel (caso do ZimaOS)."""

from __future__ import annotations

import subprocess

import pytest

import docker_helpers as dh

pytestmark = pytest.mark.docker

# O valor e lido de `env`: e o que um processo filho (o docker) enxerga. Roda como nobody (root le qualquer pasta). /h/.docker e do root com 710, como /DATA/.docker no ZimaOS.
SCRIPT = r"""
set -e
mkdir -p /h/.docker /h2/.docker && chmod 710 /h/.docker && chmod 755 /h2 /h2/.docker && chmod 755 /h
su -s /bin/sh nobody -c '
  HOME=/h; . /repo/deploy/lib.sh; F=$(env | sed -n "s/^DOCKER_CONFIG=//p"); echo "ilegivel=$F"
  [ -d "$F" ] && [ -w "$F" ] && echo gravavel
  unset DOCKER_CONFIG; HOME=/h2; . /repo/deploy/lib.sh; echo "legivel=$(env | sed -n "s/^DOCKER_CONFIG=//p")"
  DOCKER_CONFIG=/ja/definido; HOME=/h; . /repo/deploy/lib.sh; echo "definido=$DOCKER_CONFIG"
'
"""


def test_infra_dep_01_docker_config_proprio_quando_home_docker_ilegivel():
    dh.require_docker()
    r = subprocess.run(
        ["docker", "run", "--rm", "-v", f"{dh.REPO}:/repo:ro", "python:3.12-slim", "sh", "-c", SCRIPT],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    linhas = dict(l.split("=", 1) for l in r.stdout.splitlines() if "=" in l)
    assert linhas["ilegivel"].startswith("/tmp/livestats-docker."), r.stdout
    assert "gravavel" in r.stdout
    assert linhas["legivel"] == "", "com ~/.docker legivel nao muda nada"
    assert linhas["definido"] == "/ja/definido", "DOCKER_CONFIG ja definido e respeitado"
