"""Lista fechada de fontes dos overlays (D35). UNICO lugar onde ela e definida.

Os overlays rodam no OBS/vMix de um PC Windows: sao familias de sistema, nada e baixado (a CSP ``font-src 'self'``
so rege fontes web e nao muda). ``Roboto`` e o padrao antigo do contador. A api so aceita nomes desta lista;
o painel monta o dropdown a partir de ``GET /api/admin/fonts``.
"""

from __future__ import annotations

FONTS: tuple[str, ...] = (
    "Roboto",
    "Segoe UI",
    "Segoe UI Black",
    "Arial",
    "Arial Black",
    "Bahnschrift",
    "Calibri",
    "Cambria",
    "Candara",
    "Consolas",
    "Constantia",
    "Corbel",
    "Franklin Gothic Medium",
    "Georgia",
    "Impact",
    "Lucida Console",
    "Palatino Linotype",
    "Tahoma",
    "Times New Roman",
    "Trebuchet MS",
    "Verdana",
)
DEFAULT_FONT = "Roboto"
DEFAULT_TEXT_COLOR = "#FFFFFF"
