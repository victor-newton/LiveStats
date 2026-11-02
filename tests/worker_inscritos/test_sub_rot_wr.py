"""SUB-ROT-01/02 (recarga) e SUB-WR-01/02 (gravacao da leitura)."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone

from sub_support import REPO_ROOT, cookie, parse_cookie_header, query


# ---------------------------------------------------------------------- SUB-ROT
def test_sub_rot_01_30_falhas_seguidas_recarregam_a_pagina_e_zeram_as_falhas(env, make_worker, add_cookies):
    env.server.page_name = "sem_contador.html"
    add_cookies([cookie("a", "1")])
    worker = make_worker()

    for _ in range(29):
        worker.step()
    assert worker.falhas == 29
    assert env.server.studio_hits() == 1, "antes de 30 falhas nao pode recarregar"

    worker.step()  # 30a falha: recarrega
    assert env.server.studio_hits() == 2
    assert worker.falhas == 0
    # a recarga reinjeta os cookies
    assert parse_cookie_header(env.server.studio_requests()[-1]["cookie"]) == {"a=1"}

    worker.step()
    assert worker.falhas == 1
    assert env.server.studio_hits() == 2

    for _ in range(29):  # mais 30 falhas seguidas -> outra recarga
        worker.step()
    assert env.server.studio_hits() == 3
    assert worker.falhas == 0


def test_sub_rot_02_troca_de_cookies_recarrega_com_os_novos(env, make_worker, add_cookies):
    env.server.page_name = "sem_contador.html"
    primeiro = add_cookies([cookie("a", "1")])
    worker = make_worker()
    for _ in range(5):
        worker.step()
    assert env.server.studio_hits() == 1
    assert worker.falhas == 5
    assert query(env.db, "SELECT cookies_applied_id FROM worker_status") == [(primeiro,)]

    segundo = add_cookies([cookie("b", "2"), cookie("c", "3")])  # a api apaga o conjunto anterior
    assert segundo != primeiro
    worker.step()

    assert env.server.studio_hits() == 2
    assert parse_cookie_header(env.server.studio_requests()[-1]["cookie"]) == {"b=2", "c=3"}
    assert query(env.db, "SELECT cookies_applied_id FROM worker_status") == [(segundo,)]
    assert worker.falhas == 1, "as 5 falhas anteriores foram zeradas pela troca (so a leitura desta rodada falhou)"


def test_sub_rot_02_troca_de_cookies_com_pagina_boa_deixa_falhas_em_zero(env, make_worker, add_cookies):
    env.server.page_name = "sem_contador.html"
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    for _ in range(4):
        worker.step()
    assert worker.falhas == 4

    env.server.page_name = "variante_a.html"
    novo = add_cookies([cookie("a", "9")])
    worker.step()
    assert env.server.studio_hits() == 2
    assert worker.falhas == 0
    assert query(env.db, "SELECT cookies_applied_id, state, consecutive_failures FROM worker_status") == [
        (novo, "ok", 0)
    ]


def test_sub_rot_02_sem_troca_de_cookies_nao_recarrega(env, make_worker, add_cookies):
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    for _ in range(4):
        worker.step()
    assert env.server.studio_hits() == 1


# ---------------------------------------------------------------------- SUB-WR
ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z$")


def test_sub_wr_01_leitura_valida_grava_linha_unica_e_read_at_muda_a_cada_ciclo(env, make_worker, add_cookies):
    add_cookies([cookie("a", "1")])
    worker = make_worker()

    vistos = []
    for _ in range(3):
        worker.step()
        linhas = query(env.db, "SELECT id, value, raw, read_at FROM subscriber_reading")
        assert len(linhas) == 1
        assert linhas[0][:3] == (1, "460600", "460600")
        assert ISO_Z.match(linhas[0][3])
        vistos.append(linhas[0][3])
        time.sleep(0.01)
    assert len(set(vistos)) == 3, "read_at precisa mudar a cada leitura, mesmo com o mesmo valor"
    assert vistos == sorted(vistos)


def test_sub_wr_01_raw_e_truncado_em_256_caracteres(env, make_worker, add_cookies):
    bruto = "460600" + "x" * 300
    env.server.html_override = f"<!DOCTYPE html><html><body><yta-smooth-counter>{bruto}</yta-smooth-counter></body></html>"
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()

    (valor, raw), = query(env.db, "SELECT value, raw FROM subscriber_reading")
    assert valor == "460600"
    assert len(raw) == 256
    assert raw == bruto[:256]


def test_sub_wr_02_linha_gravada_reproduz_o_inscritos_json_golden(env, make_worker, add_cookies):
    golden = (REPO_ROOT / "tests" / "contract" / "legacy" / "inscritos.json").read_bytes()
    esperado = json.loads(golden)
    instante = datetime(2026, 11, 2, 14, 32, 10, 654321, tzinfo=timezone.utc).timestamp()
    assert esperado["atualizado_em"] == "2026-11-02T14:32:10.654321Z"  # o golden e o que estamos reproduzindo

    add_cookies([cookie("a", "1")])
    worker = make_worker(clock=lambda: instante)  # relogio injetado (fronteira permitida)
    worker.step()

    (valor, raw, read_at), = query(env.db, "SELECT value, raw, read_at FROM subscriber_reading")
    corpo = json.dumps(
        {"inscritos": valor, "raw": raw, "atualizado_em": read_at},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    assert corpo == golden
