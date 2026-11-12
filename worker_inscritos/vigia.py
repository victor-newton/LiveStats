"""D60: prazo duro por carga do Studio.

O Playwright sincrono nao se cancela por dentro: uma carga que nunca responde bloqueia o worker para sempre
(incidente real: uma carga de mais de 1 hora). A saida e um vigia numa outra thread: vencido o prazo, ele mata o
processo do Chromium (filho do proprio processo do worker); a chamada bloqueada levanta `PlaywrightError`
(TargetClosed) e o fluxo normal recria o navegador e o contexto. Nada aqui registra URL, cookie ou texto de excecao.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable

log = logging.getLogger("worker_inscritos.vigia")

MSG_PRESO = "O Studio nao respondeu; o navegador foi reiniciado"


def _ppid_e_nome(pid: int) -> tuple[int, str] | None:
    """(pai, nome do executavel) lidos de /proc/<pid>/stat; None se o processo sumiu ou nao e legivel."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            bruto = f.read().decode("utf-8", "replace")
    except OSError:
        return None
    ini, fim = bruto.find("("), bruto.rfind(")")
    if ini < 0 or fim < ini:
        return None
    try:
        return int(bruto[fim + 2 :].split()[1]), bruto[ini + 1 : fim]
    except (IndexError, ValueError):
        return None


def matar_chromium(raiz: int | None = None) -> int:
    """Mata (SIGKILL) os descendentes do processo `raiz` (padrao: o proprio) cujo nome contem 'chrome'.

    Devolve quantos foram mortos. Varre /proc; so toca em filhos do proprio worker.
    """
    raiz = os.getpid() if raiz is None else raiz
    pais: dict[int, tuple[int, str]] = {}
    try:
        nomes = os.listdir("/proc")
    except OSError:
        return 0
    for nome in nomes:
        if nome.isdigit():
            info = _ppid_e_nome(int(nome))
            if info is not None:
                pais[int(nome)] = info
    descendentes: set[int] = set()
    mudou = True
    while mudou:
        mudou = False
        for pid, (pai, _) in pais.items():
            if pid not in descendentes and (pai == raiz or pai in descendentes):
                descendentes.add(pid)
                mudou = True
    mortos = 0
    for pid in descendentes:
        if "chrome" in pais[pid][1].lower():
            try:
                os.kill(pid, signal.SIGKILL)
                mortos += 1
            except OSError:
                pass
    return mortos


class Vigia:
    """Timer de uma carga: `iniciar()` arma, `cancelar()` desarma; `estourou` fica True se o prazo venceu."""

    def __init__(self, prazo: float, matar: Callable[[], int] = matar_chromium):
        self.estourou = False
        self._matar = matar
        self._timer = threading.Timer(prazo, self._vencer)
        self._timer.daemon = True

    def _vencer(self) -> None:
        self.estourou = True
        log.error("[BROWSER] carga presa; navegador reiniciado")
        try:
            self._matar()
        except Exception as exc:  # noqa: BLE001 - o vigia nunca pode morrer em silencio
            log.error("[BROWSER] nao foi possivel encerrar o navegador (%s)", type(exc).__name__)

    def iniciar(self) -> None:
        self._timer.start()

    def cancelar(self) -> None:
        self._timer.cancel()
