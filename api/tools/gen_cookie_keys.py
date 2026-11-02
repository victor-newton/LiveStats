"""Gera o par de chaves Curve25519 usado para cifrar os cookies (caixa selada).

Saida padrao (duas linhas, base64 de 32 bytes cada):

    cookie_private_key=<base64>   -> secrets/cookie_private_key.txt   (somente o worker-inscritos)
    cookie_public_key=<base64>    -> secrets/cookie_public_key.txt    (somente a api)

Com ``--output-dir DIR`` grava os dois arquivos (``cookie_private_key.txt`` com modo 0600 e
``cookie_public_key.txt`` com 0644) em vez de imprimir, e recusa sobrescrever arquivos existentes.
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
from pathlib import Path

from nacl.public import PrivateKey


def generate() -> tuple[str, str]:
    private = PrivateKey.generate()
    return (
        base64.b64encode(bytes(private)).decode("ascii"),
        base64.b64encode(bytes(private.public_key)).decode("ascii"),
    )


def _write_new(path: Path, content: str, mode: int) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(content + "\n")
    os.chmod(path, mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Gera o par de chaves dos cookies cifrados.")
    parser.add_argument("--output-dir", type=Path, help="grava cookie_private_key.txt e cookie_public_key.txt neste diretorio")
    args = parser.parse_args(argv)

    private_b64, public_b64 = generate()
    if args.output_dir is None:
        print(f"cookie_private_key={private_b64}")
        print(f"cookie_public_key={public_b64}")
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        _write_new(args.output_dir / "cookie_private_key.txt", private_b64, 0o600)
        _write_new(args.output_dir / "cookie_public_key.txt", public_b64, 0o644)
    except FileExistsError:
        print("Arquivo de chave ja existe; nada foi sobrescrito.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
