"""SUB-NUM-01/02: `filtrar_numero` (vetores do codigo original) e valor curto demais."""

from __future__ import annotations

import pytest

from worker_inscritos.numero import filtrar_numero, valor_aceito
from sub_support import query

# Tabela de docs/legacy-formats.md 2.1 (saida do codigo original, inclusive os bugs preservados de proposito: P7).
VETORES = [
    ("460600", "460600"),
    ("460.600", "460.600"),
    ("460,600", "460600"),
    ("460.6K", "460600"),
    ("1.2M", "1200000"),
    ("3B", "3000000000"),
    ("2.5B", "2500000000"),
    ("12k", "12000"),
    ("1,5k", "1500"),
    ("12", "12"),
    ("1", "1"),
    ("1.234.567", "1.234"),
    ("1.2345", "1.235"),
    ("1234.5678", "1234.568"),
    ("10.000", "10.000"),
    ("1.5.5k", "1.5.5"),
    ("1,234", "1234"),
    ("1 234", "1234"),
    ("  460600 ", "460600"),
    ("abc", None),
    ("", None),
    ("K", None),
]


@pytest.mark.parametrize("entrada,esperado", VETORES, ids=[repr(v[0]) for v in VETORES])
def test_sub_num_01_vetores_filtrar_numero(entrada, esperado):
    assert filtrar_numero(entrada) == esperado


def test_sub_num_01_overflow_nao_derruba_o_processo():
    # O original levantaria OverflowError aqui; o porte trata como numero invalido.
    assert filtrar_numero("infk") is None


def test_sub_num_02_valor_com_menos_de_2_caracteres_nao_e_aceito():
    assert filtrar_numero("1") == "1"
    assert valor_aceito("1") is False
    assert valor_aceito("12") is True
    assert valor_aceito(None) is False
    assert valor_aceito("") is False
    # teto da coluna subscriber_reading.value (32): acima disso o INSERT falharia
    assert valor_aceito("1" * 32) is True
    assert valor_aceito("1" * 33) is False


def test_sub_num_02_worker_nao_grava_valor_curto_e_conta_como_falha(env, make_worker, add_cookies):
    env.server.page_name = "valor_curto.html"  # contador "000K" -> filtrar_numero devolve "0"
    add_cookies([{"name": "a", "value": "1", "domain": "127.0.0.1", "path": "/", "secure": False}])
    worker = make_worker()
    worker.step()
    worker.step()

    assert query(env.db, "SELECT * FROM subscriber_reading") == []
    assert worker.falhas == 2
    state, code, failures = query(
        env.db, "SELECT state, last_error_code, consecutive_failures FROM worker_status WHERE worker='inscritos'"
    )[0]
    assert (state, code, failures) == ("degraded", "invalid_value", 2)
