"""Filtro do numero lido da pagina (porte fiel de `filtrar_numero` do scraper original).

O comportamento e o do codigo original, inclusive os "bugs" preservados de proposito (P7 do
ARCHITECTURE.md): `filtrar_numero("1.234.567")` devolve `"1.234"`. Vetores em docs/legacy-formats.md 2.1.
"""

from __future__ import annotations

import re

_SUFIXOS = (("b", 1_000_000_000), ("m", 1_000_000), ("k", 1_000))

# Limites da coluna subscriber_reading.value (db/schema.sql): length BETWEEN 2 AND 32.
TAMANHO_MIN = 2
TAMANHO_MAX = 32


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
    """O valor so e gravado se tiver ao menos 2 caracteres (regra do legado).

    O teto de 32 caracteres vem do CHECK da tabela: acima disso o INSERT falharia, entao o valor
    e tratado como invalido (falha de leitura) em vez de derrubar o ciclo.
    """
    return bool(valor) and TAMANHO_MIN <= len(valor) <= TAMANHO_MAX
