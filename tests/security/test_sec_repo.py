# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""SEC-10, SEC-19, SEC-22, SEC-ANTI-01, SEC-ANTI-02: repositorio e imagens (gitleaks, Trivy, varreduras estaticas, anti-burla)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import warnings
from pathlib import Path

import pytest
from e2e_stack import IMAGES, REPO, docker, require_docker

GITLEAKS_IMAGE = "ghcr.io/gitleaks/gitleaks:v8.28.0"
TRIVY_IMAGE = "aquasec/trivy:0.67.2"
CONTRACT_COMMIT = "7bef085"
SCRIPT = REPO / "scripts" / "check_antiburla.py"


def git(*args: str) -> str:
    r = subprocess.run(["git", *args], cwd=REPO, capture_output=True, encoding="utf-8")
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout


def tracked_files() -> list[str]:
    return [f for f in git("ls-files").splitlines() if f]


def tracked_text(path: str) -> str | None:
    try:
        return (REPO / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


# ---------------------------------------------------------------------------------------------------- SEC-10
@pytest.mark.docker
def test_sec_10_gitleaks_e_trivy_sem_achados_e_sem_arquivo_de_ignorados(tmp_path, stack):
    require_docker()
    # --- nenhum arquivo de ignorados nem allowlist
    for name in (".trivyignore", ".trivyignore.yaml", ".gitleaksignore", ".trivy.yaml", "trivy.yaml"):
        assert name not in tracked_files() and not (REPO / name).exists(), f"arquivo de ignorados: {name}"
    toml = (REPO / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "allowlist" not in toml.lower().replace("nao define allowlist", "")

    # --- gitleaks: historico completo (clone local do repositorio, sem arquivos nao versionados)
    clone = tmp_path / "clone"
    r = subprocess.run(["git", "clone", "-q", "--no-hardlinks", str(REPO), str(clone)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    leaks = docker(
        "run", "--rm", "-v", f"{clone}:/repo:ro", GITLEAKS_IMAGE, "detect", "--source", "/repo",
        "--config", "/repo/.gitleaks.toml", "--no-banner", "--redact", "--exit-code", "1", check=False, timeout=900,
    )
    problems: list[str] = []  # tudo e executado e reportado junto; qualquer achado reprova o teste
    if leaks.returncode != 0:
        problems.append(f"gitleaks encontrou segredos (ver relatorio):\n{leaks.stderr[-800:]}")

    # --- Trivy: sistema de arquivos e cada imagem (HIGH/CRITICAL)
    cache = "t8-trivy-cache"
    try:
        common = ["run", "--rm", "-v", f"{cache}:/root/.cache/"]
        fs = docker(
            *common, "-v", f"{clone}:/repo:ro", TRIVY_IMAGE, "fs", "--scanners", "vuln,secret",
            "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", "--no-progress", "/repo", check=False, timeout=1800,
        )
        if fs.returncode != 0:
            problems.append(f"trivy fs:\n{fs.stdout[-3000:]}{fs.stderr[-1000:]}")
        for name, image in IMAGES.items():
            tar = tmp_path / f"{name}.tar"
            docker("save", "-o", str(tar), image, timeout=900)
            scan = docker(
                *common, "-v", f"{tmp_path}:/in:ro", TRIVY_IMAGE, "image", "--input", f"/in/{name}.tar", "--scanners", "vuln",
                "--severity", "HIGH,CRITICAL", "--ignore-unfixed", "--exit-code", "1", "--no-progress", check=False, timeout=1800,
            )
            # politica do V1: HIGH/CRITICAL COM correcao disponivel reprova; sem correcao so e LISTADO (sem reprovar, sem ignorados)
            pending = docker(
                *common, "-v", f"{tmp_path}:/in:ro", TRIVY_IMAGE, "image", "--input", f"/in/{name}.tar", "--scanners", "vuln",
                "--severity", "HIGH,CRITICAL", "--ignore-status", "fixed", "--exit-code", "0", "--no-progress", check=False, timeout=1800,
            )
            if "Total: 0" not in pending.stdout:
                warnings.warn(f"Trivy sem correcao disponivel em {name} (revisar no V2): " + pending.stdout[-2500:], stacklevel=1)
            tar.unlink()
            if scan.returncode != 0:
                problems.append(f"trivy image {name}:\n{scan.stdout[-3000:]}{scan.stderr[-1000:]}")
    finally:
        docker("volume", "rm", "-f", cache, check=False)
    assert not problems, "\n\n".join(problems)


# ---------------------------------------------------------------------------------------------------- SEC-19
def test_sec_19_segredos_fora_do_git():
    assert git("ls-files", "secrets").split() == ["secrets/README.md"]
    files = tracked_files()
    envs = [f for f in files if Path(f).name == ".env" or (Path(f).name.startswith(".env.") and Path(f).name != ".env.example")]
    assert envs == [], envs
    assert not [f for f in files if f.endswith((".pem", ".key")) or Path(f).name in ("cookies.json", "debug.png")]

    api_key = re.compile(r"AIza[0-9A-Za-z_-]{35}")
    channel = re.compile(r"\bUC[0-9A-Za-z_-]{22}\b")
    for path in files:
        if path.startswith("tests/worker_lives/fixtures/"):
            continue  # respostas gravadas do YouTube: trazem o channelId PUBLICO de videos de terceiros
        text = tracked_text(path)
        if text is None:
            continue
        assert not api_key.search(text), f"chave no formato AIza em {path}"
        for found in channel.findall(text):
            assert set(found[2:]) == {"x"}, f"ID de canal que nao e placeholder em {path}: {found}"


# ---------------------------------------------------------------------------------------------------- SEC-22
def test_sec_22_nao_ha_opcao_para_desligar_secure_httponly_samesite_nem_tls_no_nginx():
    option = re.compile(r"\b[A-Z][A-Z0-9_]*(?:SECURE|HTTP_?ONLY|SAME_?SITE|INSECURE)[A-Z0-9_]*\b")
    scope = [f for f in tracked_files() if f in ("docker-compose.yml", ".env.example") or f.startswith(("web/nginx/", "api/app/"))]
    assert "docker-compose.yml" in scope and any(f.startswith("api/app/") for f in scope) and any(f.startswith("web/nginx/") for f in scope)
    for path in scope:
        text = tracked_text(path) or ""
        assert not option.search(text), f"possivel opcao para alterar atributos do cookie em {path}: {option.findall(text)}"
        if path.startswith("web/nginx/"):
            assert not re.search(r"listen\s+[^;]*\bssl\b|ssl_certificate|ssl_protocols|\bssl\s+on\b", text), f"TLS no Nginx do repositorio: {path}"
    # os atributos estao fixos no codigo da api (constante unica), sem leitura de ambiente na mesma regiao
    code = (REPO / "api" / "app" / "routes_admin.py").read_text(encoding="utf-8")
    assert "Path=/; HttpOnly; Secure; SameSite=Strict" in code
    assert not re.search(r"(?:environ|getenv)[^\n]*(?:cookie|session)", "\n".join(tracked_text(f) or "" for f in scope if f.startswith("api/app/")), re.I)


# ---------------------------------------------------------------------------------------------------- SEC-ANTI
def test_sec_anti_01_varredura_anti_burla_limpa_e_testes_aprovados_intactos():
    base = os.environ.get("ANTIBURLA_BASE", "main")
    r = subprocess.run([sys.executable, str(SCRIPT), "--base", base], cwd=REPO, capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, f"anti-burla:\n{r.stdout}\n{r.stderr}"
    assert r.stdout.strip().startswith("OK")


def test_sec_anti_01_a_varredura_realmente_detecta_cada_padrao(tmp_path):
    """A varredura nao pode ser um no-op: arvore sintetica com cada padrao proibido => todos apontados."""
    (tmp_path / "api").mkdir()
    (tmp_path / "tests").mkdir()
    bad_prod = "\n".join(["if TEST" + "ING:", "    pass", "DISABLE" + "_AUTH = 1", "x = 1  # pragma" + ": no cover"])
    (tmp_path / "api" / "bad.py").write_text(bad_prod, encoding="utf-8")
    bad_test = "\n".join(
        ["import pytest", "@pytest.mark." + "skip", "@pytest.mark." + "xfail", "def t(): pytest." + "skip('x')",
         "from unittest." + "mock import patch", "test." + "skip('x')", "SKIP" + "_CSRF = True", "NO_RATE" + "_LIMIT = 1"]
    )
    (tmp_path / "tests" / "test_bad.py").write_text(bad_test, encoding="utf-8")
    r = subprocess.run([sys.executable, str(SCRIPT), "--root", str(tmp_path)], capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 1, r.stdout
    for fragment in ("skip/xfail do pytest", "(JS)", "pragma no cover", "producao: if", "variavel que desliga", "revisar: so a fronteira"):
        assert fragment in r.stdout, (fragment, r.stdout)
    clean = tmp_path / "clean"
    (clean / "api").mkdir(parents=True)
    (clean / "api" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    assert subprocess.run([sys.executable, str(SCRIPT), "--root", str(clean)], capture_output=True).returncode == 0


def test_sec_anti_02_golden_files_identicos_ao_commit_do_contrato():
    registered = json.loads((Path(__file__).parent / "golden_sha256.json").read_text(encoding="utf-8"))
    assert registered["contract_commit"] == CONTRACT_COMMIT
    golden_dir = REPO / "tests" / "contract" / "legacy"
    on_disk = sorted(p.name for p in golden_dir.iterdir())
    assert on_disk == sorted(registered["sha256"]), "golden files adicionados ou removidos"
    for name, digest in registered["sha256"].items():
        assert hashlib.sha256((golden_dir / name).read_bytes()).hexdigest() == digest, f"{name} alterado"
        # o hash registrado confere com o conteudo do commit do contrato (o registro nao pode ser trocado junto)
        blob = subprocess.run(
            ["git", "show", f"{CONTRACT_COMMIT}:tests/contract/legacy/{name}"], cwd=REPO, capture_output=True
        )
        assert blob.returncode == 0, f"commit do contrato nao encontrado no historico (clone raso?): {blob.stderr!r}"
        assert hashlib.sha256(blob.stdout).hexdigest() == digest
