"""Testes adicionais da T3: configuracao (API-CFG-01), ferramentas de segredos (API-TOOL-01/02) e healthcheck (API-HC-01)."""

from __future__ import annotations

import base64
import os
import secrets
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
import uvicorn
from argon2 import PasswordHasher
from nacl.public import PrivateKey, SealedBox

from api_support import API_DIR, BASE_URL, Stack, running_stack, make_env
from app.main import create_app


# ------------------------------------------------------------------------------------- API-CFG-01
@pytest.mark.parametrize(
    "override",
    [
        {"ADMIN_USERNAME": None},
        {"ADMIN_USERNAME": "   "},
        {"ADMIN_USERNAME": "u" * 65},
        {"DB_PATH": None},
        {"DB_PATH": ""},
        {"PUBLIC_BASE_URL": "ftp://exemplo.com"},
        {"PUBLIC_BASE_URL": "javascript:alert(1)"},
        {"PUBLIC_BASE_URL": "isto nao e url"},
        {"DB_SCHEMA_FILE": "/nao/existe/schema.sql"},
        {"ADMIN_DB_SCHEMA_FILE": "/nao/existe/admin_schema.sql"},
    ],
)
def test_api_cfg_01_configuracao_invalida_impede_a_subida(tmp_path, creds, override):
    from app.config import ConfigError

    with pytest.raises(ConfigError):
        with running_stack(tmp_path, creds, **override):
            pytest.fail("a app subiu com configuracao invalida")


def test_api_cfg_01_nenhum_segredo_vem_do_ambiente_em_texto(tmp_path, creds):
    # variaveis "antigas" de segredo no ambiente sao ignoradas: so os arquivos *_FILE valem
    with running_stack(tmp_path, creds, ADMIN_PASSWORD="outra-senha", ADMIN_PASSWORD_HASH="x", COOKIE_PRIVATE_KEY_FILE="/x") as stack:
        assert stack.login().status_code == 200
        assert stack.login(password="outra-senha", ip="10.5.0.1").status_code == 401


# ------------------------------------------------------------------------------------- API-TOOL-01
def run_tool(module: str, *args: str, stdin: str = ""):
    return subprocess.run(
        [sys.executable, "-m", module, *args], input=stdin, capture_output=True, text=True, cwd=API_DIR, timeout=60,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_api_tool_01_hash_password_gera_hash_argon2id_que_a_api_aceita(tmp_path, creds):
    password = "Senha-" + secrets.token_urlsafe(16)
    done = run_tool("tools.hash_password", stdin=password + "\n")
    assert done.returncode == 0, done.stderr
    lines = done.stdout.splitlines()
    assert len(lines) == 1 and lines[0].startswith("$argon2id$")
    assert password not in done.stdout and password not in done.stderr
    assert PasswordHasher().verify(lines[0], password)

    # ponta a ponta: a api sobe com esse hash e autentica com a senha
    generated = SimpleNamespace(
        username=creds.username, password=password, password_hash=lines[0],
        private_key=creds.private_key, public_key_bytes=creds.public_key_bytes,
    )
    with running_stack(tmp_path, generated) as stack:
        assert stack.login().status_code == 200


def test_api_tool_01_hash_password_recusa_senha_curta_e_gera_sais_diferentes():
    short = run_tool("tools.hash_password", stdin="curta\n")
    assert short.returncode == 1 and short.stdout == ""
    password = "Senha-" + secrets.token_urlsafe(12)
    first = run_tool("tools.hash_password", stdin=password).stdout
    second = run_tool("tools.hash_password", stdin=password).stdout
    assert first != second  # sal aleatorio


# ------------------------------------------------------------------------------------- API-TOOL-02
def test_api_tool_02_gen_cookie_keys_imprime_um_par_valido():
    done = run_tool("tools.gen_cookie_keys")
    assert done.returncode == 0, done.stderr
    values = dict(line.split("=", 1) for line in done.stdout.splitlines())
    assert set(values) == {"cookie_private_key", "cookie_public_key"}
    private = base64.b64decode(values["cookie_private_key"], validate=True)
    public = base64.b64decode(values["cookie_public_key"], validate=True)
    assert len(private) == len(public) == 32
    assert bytes(PrivateKey(private).public_key) == public
    other = dict(line.split("=", 1) for line in run_tool("tools.gen_cookie_keys").stdout.splitlines())
    assert other["cookie_private_key"] != values["cookie_private_key"]


def test_api_tool_02_output_dir_grava_arquivos_com_permissoes_e_nao_sobrescreve(tmp_path, creds):
    out = tmp_path / "keys"
    assert run_tool("tools.gen_cookie_keys", "--output-dir", str(out)).returncode == 0
    private_file, public_file = out / "cookie_private_key.txt", out / "cookie_public_key.txt"
    assert stat.S_IMODE(private_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(public_file.stat().st_mode) == 0o644
    before = private_file.read_text()
    again = run_tool("tools.gen_cookie_keys", "--output-dir", str(out))
    assert again.returncode == 1 and private_file.read_text() == before

    # ponta a ponta: a api cifra com o arquivo gerado e SO a chave privada gerada decifra
    private = PrivateKey(base64.b64decode(private_file.read_text().strip()))
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    with running_stack(app_dir, creds, COOKIE_PUBLIC_KEY_FILE=str(public_file)) as stack:
        csrf = stack.login_csrf()
        assert stack.client.post("/api/admin/cookies", json=[{"name": "a", "value": "b"}], headers={"X-CSRF-Token": csrf}).status_code == 200
        with stack.shared() as conn:
            blob = conn.execute("SELECT ciphertext FROM cookie_sets").fetchone()[0]
        assert b'"name":"a"' in SealedBox(private).decrypt(blob)


# ------------------------------------------------------------------------------------- API-HC-01
def test_api_hc_01_healthcheck_reflete_a_saude_do_servidor(tmp_path, creds):
    env = make_env(tmp_path, creds.password_hash, creds.public_key_bytes, creds.username)
    server = uvicorn.Server(uvicorn.Config(create_app(env), host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.time() + 20
        while not server.started and time.time() < deadline:
            time.sleep(0.05)
        assert server.started, "uvicorn nao subiu"
        port = server.servers[0].sockets[0].getsockname()[1]

        def healthcheck(p: int) -> int:
            return subprocess.run(
                [sys.executable, "-m", "app.healthcheck"], cwd=API_DIR, capture_output=True, timeout=30,
                env={**os.environ, "PORT": str(p), "PYTHONDONTWRITEBYTECODE": "1"},
            ).returncode

        assert healthcheck(port) == 0
        os.unlink(env["DB_PATH"])
        for ext in ("-wal", "-shm"):
            if os.path.exists(env["DB_PATH"] + ext):
                os.unlink(env["DB_PATH"] + ext)
        assert healthcheck(port) == 1  # /health responde 503: nao esta saudavel
    finally:
        server.should_exit = True
        thread.join(timeout=20)
    assert healthcheck_port_closed(port)


def healthcheck_port_closed(port: int) -> bool:
    done = subprocess.run(
        [sys.executable, "-m", "app.healthcheck"], cwd=API_DIR, capture_output=True, timeout=30,
        env={**os.environ, "PORT": str(port), "PYTHONDONTWRITEBYTECODE": "1"},
    )
    return done.returncode == 1
