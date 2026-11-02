#!/usr/bin/env python3
# ruff: noqa: E501, S603, S607
"""Roda testes da T8 dentro de um motor Docker LINUX real (docker:<versao fixa>-dind, privilegiado).

O Docker Desktop (Windows/Mac) tem rede diferente do Linux do ZimaOS (NAT da VM, roteamento entre bridges). Aqui a pilha
sobe num motor com iptables reais, e o pytest roda DENTRO do dind, falando com o daemon local. Nada do repositorio e
alterado (montado somente leitura). No fim, remove o dind, seus volumes e a imagem.

    python scripts/run_in_dind.py [argumentos do pytest]      (padrao: os testes de isolamento de rede)
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

DIND_IMAGE = "docker:28.0.4-dind"
NAME = "t8-dind"
REPO = Path(__file__).resolve().parents[1]
DEFAULT_ARGS = [
    "tests/security/test_sec_container.py", "tests/security/test_sec_proxy.py",
    "-k", "sec_07 or sec_11 or sec_12 or sec_20 or sec_21 or sec_05 or sec_24",
]


def run(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, text=True, encoding="utf-8", errors="replace", **kwargs)


def main() -> int:
    pytest_args = sys.argv[1:] or DEFAULT_ARGS
    run("docker", "rm", "-f", NAME, "-v", check=False, capture_output=True)
    try:
        run("docker", "run", "-d", "--privileged", "--name", NAME, "-e", "DOCKER_TLS_CERTDIR=", "-v", f"{REPO}:/repo:ro", DIND_IMAGE, capture_output=True)
        for _ in range(60):
            if run("docker", "exec", NAME, "docker", "info", check=False, capture_output=True).returncode == 0:
                break
            time.sleep(2)
        else:
            print("dind nao ficou pronto", file=sys.stderr)
            return 2
        info = run("docker", "exec", NAME, "docker", "info", "--format", "engine={{.ServerVersion}} os={{.OperatingSystem}} kernel={{.KernelVersion}} driver={{.Driver}}", capture_output=True)
        print(info.stdout.strip(), flush=True)
        run("docker", "exec", NAME, "apk", "add", "--no-cache", "python3", "py3-yaml", "py3-pytest", capture_output=True)
        result = run(
            "docker", "exec", "-e", "PYTHONDONTWRITEBYTECODE=1", "-w", "/repo", NAME,
            "python3", "-m", "pytest", *pytest_args, "-q", "--tb=short", "-p", "no:cacheprovider", check=False,
        )
        return result.returncode
    finally:
        run("docker", "rm", "-f", NAME, "-v", check=False, capture_output=True)
        run("docker", "rmi", "-f", DIND_IMAGE, check=False, capture_output=True)


if __name__ == "__main__":
    sys.exit(main())
