# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""INT-SES-01 (D37): o Studio falso renova um cookie de sessao; depois de reiniciar o worker-inscritos a leitura continua
sem colar nada. Pilha real (api, SQLite, Chromium, PyNaCl); a unica fronteira simulada e o YouTube."""

from __future__ import annotations

import json
import secrets
import time

import pytest
from e2e_stack import wait_until

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

DECRYPT_ATUAL = r"""
import base64, json, sqlite3
from nacl.public import PrivateKey, SealedBox
priv = PrivateKey(base64.b64decode(open("/run/secrets/cookie_private_key", "rb").read().strip(), validate=True))
con = sqlite3.connect("file:/data/livestats.db?mode=ro", uri=True)
row = con.execute("SELECT id, source, renewed_from, ciphertext FROM cookie_sets ORDER BY id DESC LIMIT 1").fetchone()
print(json.dumps({"id": row[0], "source": row[1], "renewed_from": row[2], "cookies": json.loads(SealedBox(priv).decrypt(row[3]).decode())}))
"""


def test_int_ses_01_sessao_renovada_sobrevive_ao_reinicio_do_worker(stack):
    stack.ensure_subs("460600")
    nova = "T8ROT" + secrets.token_hex(12)
    auth = stack.auth_cookie
    (stack.state_dir / "fake-studio-rotated").unlink(missing_ok=True)
    try:
        # o Google passa a rotacionar o cookie de sessao: o valor colado vale UMA vez e a resposta traz o novo
        stack.fake_set(rotate={"name": auth["name"], "old": auth["value"], "new": nova}, subscribers="460600")
        t_paste = time.time()
        pasted = stack.upload_cookies()
        assert pasted["cookies_count"] == 2
        antes = stack.workers_status()["cookies"]

        # (1) o worker leu, exportou a sessao do Chromium e gravou a renovacao (source='worker')
        wait_until(
            lambda: (lambda c: c["source"] == "worker" and c["applied"])(stack.workers_status()["cookies"]),
            timeout=120, interval=1,
        )
        status = stack.workers_status()
        cookies = status["cookies"]
        assert cookies["source"] == "worker" and cookies["renewed_from"] is not None and cookies["count"] == 2
        assert status["workers"]["inscritos"]["auth_state"] == "ok"
        assert nova not in json.dumps(status) and auth["value"] not in json.dumps(status), "status vazou valor de cookie"
        assert antes["source"] == "admin"

        claro = json.loads(stack.py("worker-inscritos", DECRYPT_ATUAL).stdout)
        assert claro["source"] == "worker"
        por_nome = {c["name"]: c for c in claro["cookies"]}
        assert por_nome[auth["name"]]["value"] == nova, "o conjunto gravado e o renovado, nao o colado"
        assert all(set(c) == {"name", "value", "domain", "path", "secure"} for c in claro["cookies"])

        # (2) logs do worker sem nome nem valor de cookie
        log = stack.logs("worker-inscritos")
        assert "sessao renovada: 2 cookies" in log
        for segredo in (nova, auth["value"], auth["name"], stack.extra_cookie["value"], stack.extra_cookie["name"]):
            assert segredo not in log, "log do worker vazou cookie"

        # (3) reinicio do worker-inscritos (e do Studio falso): nada e colado de novo
        t_restart = time.time()
        stack.restart_workers(("worker-inscritos",))
        stack.fake_set(subscribers="461111")
        wait_until(
            lambda: stack.public_json("/inscritos.json")["inscritos"] == "461111"
            and stack.workers_status()["workers"]["inscritos"]["auth_state"] == "ok",
            timeout=150, interval=1,
        )
        depois = [r for r in stack.fake_requests("fake-studio", "studio", since=t_restart)]
        assert depois, "o worker reiniciado nunca abriu o Studio"
        assert all(r["cookie_ok"] for r in depois), "o worker reiniciado usou o cookie colado (velho)"
        assert nova in depois[0]["cookie_header"] and auth["value"] not in depois[0]["cookie_header"]
        assert not [p for p in stack.fake_requests("fake-studio", "signin", since=t_paste)], "houve redirecionamento ao login"
        assert stack.workers_status()["cookies"]["source"] in ("worker",)
    finally:
        stack.fake_state.pop("rotate", None)
        stack.fake_write()
        (stack.state_dir / "fake-studio-rotated").unlink(missing_ok=True)
        stack.fake_set(login_ok=True, subscribers="460600")
        stack.upload_cookies()
        stack.ensure_subs("460600")
