"""LIV-ID-01, LIV-FMT-01: funções puras, com os vetores de docs/legacy-formats.md §1.3."""

from __future__ import annotations

import pytest

from worker_lives.util import extrair_video_id, formatar

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", ID),
        ("https://youtu.be/dQw4w9WgXcQ?t=5", ID),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", ID),
        ("dQw4w9WgXcQ", ID),
        ("https://youtube.com/watch?list=x&v=dQw4w9WgXcQ&t=1", ID),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQextra", ID),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", None),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", None),
        ("abc", None),
        (" dQw4w9WgXcQ", None),  # a função não faz strip: quem chama (o worker) faz
        ("", None),
    ],
)
def test_liv_id_01_extrair_video_id_vetores(entrada, esperado):
    assert extrair_video_id(entrada) == esperado


def test_liv_id_01_url_com_espaco_funciona_apos_strip():
    assert extrair_video_id(" dQw4w9WgXcQ ".strip()) == ID
    assert extrair_video_id("  https://youtu.be/dQw4w9WgXcQ  ".strip()) == ID


@pytest.mark.parametrize(
    ("n", "esperado"),
    [
        (0, "0"),
        (5, "5"),
        (999, "999"),
        (1000, "1.0K"),
        (1234, "1.2K"),
        (1250, "1.2K"),
        (1999, "2.0K"),
        (9999, "10.0K"),
        (99950, "100.0K"),
        (999949, "999.9K"),
        (999950, "1000.0K"),  # comportamento do legado, mantido de propósito (P7)
        (999999, "1000.0K"),
        (1000000, "1.0M"),
        (1250000, "1.2M"),
        (12345678, "12.3M"),
    ],
)
def test_liv_fmt_01_formatar_vetores(n, esperado):
    assert formatar(n) == esperado
