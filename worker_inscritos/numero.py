"""Filtro do numero lido da pagina (`filtrar_numero`).

O formato e fixo (e nao muda): `filtrar_numero("1.234.567")` devolve `"1.234"`.
"""

from __future__ import annotations

import re

_SUFIXOS = (("b", 1_000_000_000), ("m", 1_000_000), ("k", 1_000))

# Limites da coluna subscriber_reading.value (db/schema.sql): length BETWEEN 2 AND 32.
TAMANHO_MIN = 2
TAMANHO_MAX = 32
MAX_INTEIRO_SQLITE = 9_223_372_036_854_775_807


def filtrar_numero(texto: str) -> str | None:
    lower = texto.lower().strip()
    for sufixo, mult in _SUFIXOS:
        if lower.endswith(sufixo):
            try:
                return str(int(float(lower[:-1].replace(",", ".")) * mult))
            except (ValueError, OverflowError):
                # OverflowError (ex.: "infk"): o original derrubaria o processo; aqui segue adiante
                # como no ValueError e acaba em None (valor invalido).
                pass
    num = re.sub(r"[^\d.]", "", texto)
    if not num:
        return None
    partes = num.split(".")
    if len(partes) > 1:
        milhar = partes[0]
        resto = partes[1]
        if len(resto) >= 3:
            num = milhar + "." + resto[0:2] + resto[-1]
    return num


def valor_aceito(valor: str | None) -> bool:
    """O valor so e gravado se tiver ao menos 2 caracteres (regra do formato publico).

    O teto de 32 caracteres vem do CHECK da tabela: acima disso o INSERT falharia, entao o valor
    e tratado como invalido (falha de leitura) em vez de derrubar o ciclo.
    """
    return bool(valor) and TAMANHO_MIN <= len(valor) <= TAMANHO_MAX


def valor_inteiro(texto: str) -> int | None:
    """Converte a contagem original do Studio em inteiro, preservando agrupadores e sufixos."""
    lower = texto.lower().strip().replace(",", ".")
    for sufixo, mult in _SUFIXOS:
        if lower.endswith(sufixo):
            try:
                valor = float(lower[:-1].strip()) * mult
                inteiro = int(valor)
                return inteiro if 0 <= inteiro <= MAX_INTEIRO_SQLITE else None
            except (ValueError, OverflowError):
                return None
    digitos = re.sub(r"\D", "", texto)
    if not digitos:
        return None
    try:
        inteiro = int(digitos)
        return inteiro if inteiro <= MAX_INTEIRO_SQLITE else None
    except ValueError:
        return None
