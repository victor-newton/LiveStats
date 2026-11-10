"""Gera o hash argon2id (formato PHC) da senha do admin.

Uso (a senha NUNCA e argumento de linha de comando, para nao ir ao historico do shell):

    docker compose run --rm --no-deps -T api python -m tools.hash_password   # le a senha da entrada padrao
    python -m tools.hash_password                                            # terminal: pede duas vezes

Imprime somente o hash, em uma linha, na saida padrao. Salve em ``secrets/admin_password_hash.txt``.
"""

from __future__ import annotations

import getpass
import sys

from argon2 import PasswordHasher

MIN_LENGTH = 8


def read_password() -> str:
    if sys.stdin.isatty():
        first = getpass.getpass("Senha do admin: ")
        if getpass.getpass("Repita a senha: ") != first:
            raise SystemExit("As senhas nao conferem.")
        return first
    line = sys.stdin.readline()
    return line.rstrip("\r\n")


def main() -> int:
    password = read_password()
    if len(password) < MIN_LENGTH:
        print(f"A senha deve ter pelo menos {MIN_LENGTH} caracteres.", file=sys.stderr)
        return 1
    print(PasswordHasher().hash(password))
    return 0


if __name__ == "__main__":
    sys.exit(main())
