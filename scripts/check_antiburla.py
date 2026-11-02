# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
#!/usr/bin/env python3
"""Varredura anti-burla (SEC-ANTI-01): procura os padroes proibidos do PLANO (secao 3) no codigo de producao e nos testes.

Padroes: skip/xfail (pytest e JS), `# pragma: no cover`, caminhos de teste na producao (`if TESTING`, variaveis que desligam
login/CSRF/rate limit) e mock/monkeypatch. Os padroes sao escritos com classes de caracteres para que ESTE arquivo
tambem passe na propria varredura (nenhuma excecao por caminho).

    python scripts/check_antiburla.py [--base REF] [--root DIR]

`--base REF` tambem exige que os arquivos de `tests/contract/` e os testes ja aprovados (tests/api, worker_lives,
worker_inscritos, web, infra) nao tenham sido alterados ou removidos desde REF (so ADICOES de arquivos novos sao aceitas).
Sai com 0 se limpo, 1 se achou algo, 2 se nao conseguiu rodar.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PRODUCTION_DIRS = ("api", "worker_lives", "worker_inscritos", "web")
TEST_DIRS = ("tests",)
SUFFIXES = {".py", ".js", ".mjs", ".ts", ".html", ".conf", ".template", ".yml", ".yaml", ".sh", ".toml", ".ini", ".cfg"}
APPROVED_TEST_DIRS = (
    "tests/contract", "tests/api", "tests/worker_lives", "tests/worker_inscritos", "tests/web", "tests/infra",
)

# (nome, regex, onde vale)
FORBIDDEN = [
    ("skip/xfail do pytest", re.compile(r"pytest\.mark\.(?:skip|skipif|xfail)|pytest\.(?:skip|xfail)\s*\(|@unittest\.skip|unittest\.SkipTest"), "all"),
    ("test.skip / it.skip / describe.skip (JS)", re.compile(r"\b(?:test|it|describe)\.(?:skip|fixme|only)\b"), "all"),
    ("pragma no cover", re.compile(r"#\s*pragma:\s*no\s*cover"), "all"),
    ("caminho de teste na producao: if TESTING", re.compile(r"\bif\s+(?:not\s+)?(?:settings\.|config\.|os\.environ\.get\(['\"])?TEST[I]NG\b"), "all"),
    ("variavel que desliga login/CSRF/rate limit", re.compile(r"DISABLE[_]AUTH|SKIP[_]CSRF|NO[_]RATE[_]LIMIT|DISABLE[_]CSRF|DISABLE[_]RATE|AUTH[_]DISABLED|BYPASS[_]AUTH"), "all"),
    ("mock/monkeypatch (revisar: so a fronteira externa pode ser simulada)", re.compile(r"unittest\.mock|from\s+unittest\s+import\s+mock|\bmock\.patch|\bMagicMock\b|\bmonkeypatch\b|\bpatch\.object\b"), "tests"),
]

# Usos aprovados de monkeypatch/mock: o relogio e a fronteira HTTP do YouTube (regra 4). Qualquer outro alvo reprova.
ALLOWED_MOCK_LINE = re.compile(
    r"monkeypatch\.(?:setenv|delenv|chdir)\(|"          # ambiente do processo de teste (nao e simular a unidade)
    r"monkeypatch\.setattr\([^)]*(?:clock|time|sleep|monotonic|now)\b|"  # relogio
    r"def\s+\w+\(.*\bmonkeypatch\b|"                      # assinatura da fixture/teste que recebe o objeto
    r"\bmonkeypatch\b\s*[,)]"
)


# Testes aprovados que apenas LISTAM os nomes proibidos (como texto) para varrer a producao atras deles.
DENYLIST_FILES = {("tests/api/test_api_db_security.py", "variavel que desliga login/CSRF/rate limit")}


@dataclass
class Finding:
    path: str
    line: int
    rule: str
    text: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.text.strip()[:140]}"


def iter_files(root: Path, dirs: tuple[str, ...]):
    for d in dirs:
        base = root / d
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file() and path.suffix in SUFFIXES and not any(
                part in ("node_modules", "__pycache__", ".pytest_cache", "fonts") for part in path.parts
            ):
                yield path


def scan(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for kind, dirs in (("production", PRODUCTION_DIRS), ("tests", TEST_DIRS)):
        for path in iter_files(root, dirs):
            rel = path.relative_to(root).as_posix()
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except UnicodeDecodeError:
                continue
            for number, line in enumerate(lines, start=1):
                for rule, regex, where in FORBIDDEN:
                    if where == "tests" and kind != "tests":
                        continue
                    if regex.search(line):
                        if where == "tests" and ALLOWED_MOCK_LINE.search(line):
                            continue
                        if (rel, rule) in DENYLIST_FILES:
                            continue
                        findings.append(Finding(rel, number, rule, line))
    return findings


def run_git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} falhou: {result.stderr.strip()}")
    return result.stdout


def changed_approved_tests(root: Path, base: str) -> list[str]:
    """Arquivos de testes aprovados MODIFICADOS, REMOVIDOS ou RENOMEADOS desde `base` (adicoes nao contam)."""
    out = run_git(root, "diff", "--name-status", "--no-renames", base, "--", *APPROVED_TEST_DIRS)
    bad = []
    for line in out.splitlines():
        status, _, name = line.partition("\t")
        if status != "A":
            bad.append(f"{status} {name}")
    return bad


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--base", help="ref do Git para conferir que testes aprovados e tests/contract nao mudaram")
    args = parser.parse_args(argv)
    try:
        findings = scan(args.root)
        changed = changed_approved_tests(args.root, args.base) if args.base else []
    except (RuntimeError, OSError) as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2
    for finding in findings:
        print(finding)
    for item in changed:
        print(f"TESTE APROVADO ALTERADO desde {args.base}: {item}")
    if findings or changed:
        print(f"{len(findings)} padrao(oes) proibido(s), {len(changed)} teste(s) aprovado(s) alterado(s)")
        return 1
    print("OK: nenhum padrao proibido; testes aprovados intactos" if args.base else "OK: nenhum padrao proibido")
    return 0


if __name__ == "__main__":
    sys.exit(main())
