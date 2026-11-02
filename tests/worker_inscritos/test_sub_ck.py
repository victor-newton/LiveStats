"""SUB-CK-01..05 (cookies) e SUB-AUTH-01/02 (login perdido / recuperacao)."""

from __future__ import annotations

import logging
import os

import pytest
from nacl.public import PrivateKey, SealedBox

from worker_inscritos.config import ler_chave_privada
from worker_inscritos.cookies import CookieDecryptError, decifrar_cookies, key_id, normalizar_cookies
from sub_support import cookie, dump_text, insert_cookie_set, parse_cookie_header, public_key_id, query

SEGREDO_NOME = "NOMESEGREDOxyz987"
SEGREDO_VALOR = "VALORSEGREDOabc654"


def status(env):
    return query(
        env.db,
        "SELECT state, auth_state, last_error_code, cookies_applied_id FROM worker_status WHERE worker='inscritos'",
    )[0]


# ---------------------------------------------------------------------- SUB-CK-01
def test_sub_ck_01_servidor_recebe_exatamente_name_value_de_cada_cookie(env, make_worker, add_cookies):
    add_cookies(
        [
            cookie("a", "1"),
            cookie("b", "2"),
            cookie("tok", "x=y=="),
            cookie("SID", "AbC-123_.~"),
            cookie("outro_dominio", "nao", domain="example.test"),  # outro dominio: nao vai ao 127.0.0.1
        ]
    )
    worker = make_worker()
    worker.step()

    pedidos = env.server.studio_requests()
    assert len(pedidos) == 1
    assert parse_cookie_header(pedidos[0]["cookie"]) == {"a=1", "b=2", "tok=x=y==", "SID=AbC-123_.~"}
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]  # e leu o contador


def test_sub_ck_01_decifrar_usa_a_chave_privada_do_arquivo(env, add_cookies):
    add_cookies([cookie("a", "1")])
    (cifrado,) = query(env.db, "SELECT ciphertext FROM cookie_sets")[0]
    chave = ler_chave_privada(str(env.key_file))
    assert decifrar_cookies(cifrado, chave) == [cookie("a", "1")]
    assert key_id(chave) == public_key_id(env.keypair.public_key)
    with pytest.raises(CookieDecryptError):
        decifrar_cookies(cifrado, PrivateKey.generate())


# ---------------------------------------------------------------------- SUB-CK-02
def test_sub_ck_02_sem_cookie_sets_marca_no_cookies_e_tenta_de_novo_a_cada_ciclo(env, make_worker, add_cookies):
    worker = make_worker()
    for _ in range(3):
        worker.step()  # nao pode levantar
    assert status(env) == ("error", "no_cookies", None, None)
    assert not worker.session.started, "sem cookies nao ha motivo para abrir o navegador"
    assert env.server.studio_hits() == 0
    assert query(env.db, "SELECT * FROM subscriber_reading") == []

    novo = add_cookies([cookie("a", "1")])  # o admin cola os cookies depois
    worker.step()
    assert status(env) == ("ok", "ok", None, novo)
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]


# ---------------------------------------------------------------------- SUB-CK-03
def _sem_segredos(env, caplog):
    assert SEGREDO_NOME not in caplog.text and SEGREDO_VALOR not in caplog.text
    assert SEGREDO_NOME not in dump_text(env.db) and SEGREDO_VALOR not in dump_text(env.db)


def test_sub_ck_03_ciphertext_de_outra_chave(env, make_worker, add_cookies, caplog):
    caplog.set_level(logging.DEBUG)
    outra = PrivateKey.generate()
    insert_cookie_set(env.db, outra.public_key, [cookie(SEGREDO_NOME, SEGREDO_VALOR)])
    worker = make_worker()
    worker.step()
    worker.step()  # nao pode estourar nem tentar de novo a mesma coisa

    assert status(env) == ("error", "decrypt_failed", "decrypt_failed", None)
    assert query(env.db, "SELECT * FROM subscriber_reading") == []
    assert not worker.session.started
    _sem_segredos(env, caplog)

    # um conjunto novo e valido recupera
    novo = add_cookies([cookie("a", "1")])
    worker.step()
    assert status(env) == ("ok", "ok", None, novo)


def test_sub_ck_03_ciphertext_corrompido_com_key_id_correto(env, make_worker, caplog):
    caplog.set_level(logging.DEBUG)
    lixo = os.urandom(120)
    insert_cookie_set(
        env.db, env.keypair.public_key, [cookie(SEGREDO_NOME, SEGREDO_VALOR)], ciphertext=lixo,
        key_id=public_key_id(env.keypair.public_key),
    )
    worker = make_worker()
    worker.step()

    assert status(env) == ("error", "decrypt_failed", "decrypt_failed", None)
    assert query(env.db, "SELECT * FROM subscriber_reading") == []
    _sem_segredos(env, caplog)


def test_sub_ck_03_conteudo_decifrado_que_nao_e_lista_de_cookies(env, make_worker):
    cifrado = SealedBox(env.keypair.public_key).encrypt(b'{"nao":"uma lista"}')
    insert_cookie_set(env.db, env.keypair.public_key, [cookie("a", "1")], ciphertext=cifrado)
    worker = make_worker()
    worker.step()
    assert status(env) == ("error", "decrypt_failed", "decrypt_failed", None)


# ---------------------------------------------------------------------- SUB-CK-04
def test_sub_ck_04_dominio_com_markdown_vira_o_texto_do_link():
    bruto = [{"name": "n", "value": "v", "domain": ".[youtube.com](http://youtube.com)", "path": "/x", "secure": True}]
    assert normalizar_cookies(bruto) == [
        {"name": "n", "value": "v", "domain": ".youtube.com", "path": "/x", "secure": True}
    ]


def test_sub_ck_04_domain_path_e_secure_ausentes_recebem_os_padroes():
    assert normalizar_cookies([{"name": "n", "value": "v"}]) == [
        {"name": "n", "value": "v", "domain": ".youtube.com", "path": "/", "secure": False}
    ]


def test_sub_ck_04_dominio_com_markdown_funciona_de_ponta_a_ponta(env, make_worker, add_cookies):
    add_cookies([cookie("m", "1", domain="[127.0.0.1](http://127.0.0.1)")])
    worker = make_worker()
    worker.step()
    assert parse_cookie_header(env.server.studio_requests()[0]["cookie"]) == {"m=1"}


# ---------------------------------------------------------------------- SUB-CK-05
def test_sub_ck_05_so_os_5_campos_sao_usados():
    bruto = [
        cookie(
            "h", "1", httpOnly=True, sameSite="strict", expirationDate=4102444800, hostOnly=True, session=False,
            storeId="0", id=7,
        )
    ]
    (saida,) = normalizar_cookies(bruto)
    assert set(saida) == {"name", "value", "domain", "path", "secure"}


def test_sub_ck_05_cookie_entra_como_cookie_de_sessao_sem_os_atributos_extras(env, make_worker, add_cookies):
    add_cookies([cookie("h", "1", httpOnly=True, sameSite="strict", expirationDate=4102444800)])
    worker = make_worker()
    worker.step()
    (injetado,) = [c for c in worker.session.context.cookies() if c["name"] == "h"]
    assert injetado["expires"] == -1  # sem expirationDate -> cookie de sessao (como no Selenium legado)
    assert injetado["httpOnly"] is False
    assert injetado["sameSite"] != "Strict"


# ---------------------------------------------------------------------- SUB-AUTH
@pytest.mark.parametrize(
    "login_path",
    [
        "/signin/v2/identifier?service=youtube",
        "/gate?continue=https%3A%2F%2Faccounts.google.com%2Fx",
    ],
    ids=["signin-no-caminho", "accounts.google.com-na-url"],
)
def test_sub_auth_01_redirecionamento_para_login_nao_grava_nada(env, make_worker, add_cookies, login_path):
    env.server.require_cookie = ("sid", "good")
    env.server.login_path = login_path
    add_cookies([cookie("sid", "expirado")])
    worker = make_worker()
    worker.step()
    worker.step()

    assert "/channel/" not in worker.session.current_url()
    assert status(env)[:3] == ("error", "login_required", "login_required")
    # a pagina de login tem um "contador" 999999 de proposito: nao pode ser gravado
    assert query(env.db, "SELECT * FROM subscriber_reading") == []
    assert worker.falhas == 2


def test_sub_auth_02_cookies_bons_recuperam_o_estado(env, make_worker, add_cookies):
    env.server.require_cookie = ("sid", "good")
    add_cookies([cookie("sid", "expirado")])
    worker = make_worker()
    worker.step()
    assert status(env)[:3] == ("error", "login_required", "login_required")

    novo = add_cookies([cookie("sid", "good")])
    worker.step()
    assert status(env) == ("ok", "ok", None, novo)
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]
    assert query(env.db, "SELECT consecutive_failures, last_error_message FROM worker_status") == [(0, None)]
