"""SUB-SEC-01..07 e SUB-BR-01: seguranca do navegador, segredos, logs e configuracao de destino."""

from __future__ import annotations

import logging
import re
import socket
import tempfile
from pathlib import Path

import pytest
from nacl.public import PrivateKey

from worker_inscritos.browser import CHROMIUM_ARGS, IGNORE_DEFAULT_ARGS, USER_AGENT
from worker_inscritos.config import ConfigError, ler_chave_privada, load_config
from sub_support import (
    LEGACY_UA,
    PLACEHOLDER_CHANNEL,
    REPO_ROOT,
    cookie,
    dump_text,
    insert_cookie_set,
    parse_cookie_header,
    query,
)

PASTA_WORKER = REPO_ROOT / "worker_inscritos"
PASTA_TESTES = REPO_ROOT / "tests" / "worker_inscritos"


def chromium_cmdlines() -> list[list[str]]:
    """Linhas de comando dos processos Chromium em execucao (Linux, via /proc)."""
    resultado = []
    for entrada in Path("/proc").iterdir():
        if not entrada.name.isdigit():
            continue
        try:
            partes = (entrada / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        args = [p.decode("utf-8", "replace") for p in partes if p]
        if args and "chrom" in Path(args[0]).name.lower():
            resultado.append(args)
    return resultado


def arquivos_de_texto(pasta: Path):
    for caminho in sorted(pasta.rglob("*")):
        if caminho.is_file() and "__pycache__" not in caminho.parts and caminho.suffix != ".pyc":
            yield caminho


# ---------------------------------------------------------------------- SUB-SEC-01
def test_sub_sec_01_sem_porta_de_debug(env, make_worker, add_cookies):
    assert not any("remote-debugging-port" in arg for arg in CHROMIUM_ARGS)
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]  # o navegador esta de pe

    comandos = chromium_cmdlines()
    assert comandos, "nenhum processo Chromium encontrado em /proc"
    todos = [arg for args in comandos for arg in args]
    assert not any("remote-debugging-port" in arg for arg in todos)
    assert "--remote-debugging-pipe" in todos  # o Playwright fala com o Chromium por pipe
    for host in ("127.0.0.1", "::1"):
        with pytest.raises(OSError):
            socket.create_connection((host, 9222), timeout=1).close()


# ---------------------------------------------------------------------- SUB-SEC-02
IMAGENS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}


def imagens_em(*pastas: Path) -> list[Path]:
    return [p for pasta in pastas for p in pasta.rglob("*") if p.is_file() and p.suffix.lower() in IMAGENS]


def test_sub_sec_02_por_padrao_nenhuma_imagem_e_criada(env, make_worker, add_cookies, monkeypatch):
    cwd = env.tmp / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    assert query(env.db, "SELECT value FROM subscriber_reading") == [("460600",)]
    assert imagens_em(env.tmp) == []
    assert not (Path(env.env["TMPDIR"]) / "debug").exists()


@pytest.mark.parametrize("valor", ["0", "true", "yes", "", "2"])
def test_sub_sec_02_so_o_valor_1_ativa_o_screenshot(env, make_worker, add_cookies, valor):
    add_cookies([cookie("a", "1")])
    worker = make_worker(DEBUG_SCREENSHOT=valor)
    assert worker.config.debug_screenshot is False
    worker.step()
    assert imagens_em(env.tmp) == []


def test_sub_sec_02_com_debug_screenshot_grava_em_tmp_debug_e_nunca_em_data(env, make_worker, add_cookies):
    add_cookies([cookie("a", "1")])
    worker = make_worker(DEBUG_SCREENSHOT="1")
    worker.step()
    destino = Path(env.env["TMPDIR"]) / "debug"
    assert worker.config.debug_dir == str(destino)
    imagens = imagens_em(env.tmp)
    assert imagens == [destino / "debug.png"]
    assert imagens[0].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert imagens_em(env.db.parent) == []  # nada no diretorio do banco (/data)


def test_sub_sec_02_diretorio_de_debug_junto_do_banco_e_recusado(env):
    with pytest.raises(ConfigError):
        load_config({**env.env, "DEBUG_SCREENSHOT": "1", "TMPDIR": str(env.db.parent)})


def test_sub_sec_02_o_padrao_do_diretorio_de_debug_e_tmp_debug(env):
    sem_tmpdir = {k: v for k, v in env.env.items() if k != "TMPDIR"}
    assert load_config({**sem_tmpdir, "DEBUG_SCREENSHOT": "1"}).debug_dir == "/tmp/debug"


# ---------------------------------------------------------------------- SUB-SEC-03
def test_sub_sec_03_logs_nao_trazem_nome_nem_valor_de_cookie(env, make_worker, add_cookies, caplog):
    caplog.set_level(logging.DEBUG)
    add_cookies(
        [
            cookie("NOMEBOMxyz1", "VALORBOMabc1"),
            cookie("__Host-NOMERUIMxyz2", "VALORRUIMabc2"),  # prefixo __Host- sem Secure: o Chromium recusa
            cookie("NOMEOKxyz3", "VALOROKabc3"),
        ]
    )
    worker = make_worker()
    worker.step()

    texto = caplog.text
    assert "ignorado" in texto, "a falha de add_cookies precisa ter acontecido para o teste valer"
    for segredo in ("NOMEBOM", "VALORBOM", "NOMERUIM", "VALORRUIM", "NOMEOK", "VALOROK"):
        assert segredo not in texto
        assert segredo not in dump_text(env.db)
    # os cookies bons foram injetados mesmo assim, um a um
    assert parse_cookie_header(env.server.studio_requests()[0]["cookie"]) == {"NOMEBOMxyz1=VALORBOMabc1", "NOMEOKxyz3=VALOROKabc3"}


def test_sub_sec_03_logs_com_cookies_que_nao_decifram(env, make_worker, caplog):
    caplog.set_level(logging.DEBUG)
    insert_cookie_set(env.db, PrivateKey.generate().public_key, [cookie("NOMEXxyz", "VALORXabc")])
    worker = make_worker()
    worker.step()
    assert "NOMEX" not in caplog.text and "VALORX" not in caplog.text
    assert "decifrar" in caplog.text or "chave" in caplog.text


# ---------------------------------------------------------------------- SUB-SEC-04
def test_sub_sec_04_chave_ausente_vazia_ou_invalida_e_erro(tmp_path):
    with pytest.raises(ConfigError):
        ler_chave_privada(str(tmp_path / "nao_existe.txt"))
    vazio = tmp_path / "vazio.txt"
    vazio.write_text("  \n")
    with pytest.raises(ConfigError):
        ler_chave_privada(str(vazio))
    for conteudo in ("isto nao e base64!!", "QUJD", "A" * 43 + "="):  # base64 invalido, 3 bytes, tamanho errado
        arq = tmp_path / "ruim.txt"
        arq.write_text(conteudo)
        with pytest.raises(ConfigError):
            ler_chave_privada(str(arq))


def test_sub_sec_04_config_exige_o_arquivo_e_ignora_variavel_com_o_valor(env):
    sem_arquivo = {k: v for k, v in env.env.items() if k != "COOKIE_PRIVATE_KEY_FILE"}
    valor_valido = env.key_file.read_text().strip()
    for nome in ("COOKIE_PRIVATE_KEY", "COOKIE_PRIVATE_KEY_VALUE", "PRIVATE_KEY"):
        with pytest.raises(ConfigError):
            load_config({**sem_arquivo, nome: valor_valido})


def test_sub_sec_04_chave_valida_e_lida_do_arquivo(env):
    assert bytes(ler_chave_privada(str(env.key_file))) == bytes(env.keypair)


# ---------------------------------------------------------------------- SUB-SEC-05
def test_sub_sec_05_contexto_efemero_sem_perfil_persistente(env, make_worker, add_cookies, monkeypatch):
    cwd = env.tmp / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()

    args_chromium = [arg for args in chromium_cmdlines() for arg in args]
    perfis = {Path(a.split("=", 1)[1]) for a in args_chromium if a.startswith("--user-data-dir=")}
    assert perfis, "o Chromium deve usar um diretorio de perfil temporario"
    tmp = Path(tempfile.gettempdir())
    assert all(p.parent == tmp for p in perfis), perfis  # so dentro de /tmp

    assert list(cwd.iterdir()) == []
    assert sorted(p.name for p in env.db.parent.iterdir() if not p.name.startswith("livestats.db")) == []
    assert not [p for p in env.tmp.rglob("*") if "perfil" in p.name.lower() or "profile" in p.name.lower()]

    worker.session.close()
    assert not any(p.exists() for p in perfis), "o perfil temporario precisa sumir ao encerrar"


def test_sub_sec_05_codigo_nao_usa_perfil_persistente():
    codigo = "\n".join(p.read_text(encoding="utf-8") for p in PASTA_WORKER.glob("*.py"))
    for proibido in ("launch_persistent_context", "user_data_dir", "user-data-dir", "perfil_bot"):
        assert proibido not in codigo


# ---------------------------------------------------------------------- SUB-SEC-06
def test_sub_sec_06_nenhum_id_de_canal_real_nem_cookie_real():
    canal = re.compile(r"UC[0-9A-Za-z_-]{22}")
    cookie_real = re.compile(r"(?:SAPISID|APISID|SIDCC|__Secure-[13]PSID[A-Z]*|__Secure-[13]PAPISID|LOGIN_INFO)\s*[=:]\s*\S{20,}")
    achados = []
    for pasta in (PASTA_WORKER, PASTA_TESTES):
        for caminho in arquivos_de_texto(pasta):
            texto = caminho.read_text(encoding="utf-8", errors="replace")
            achados += [(caminho.name, m) for m in canal.findall(texto) if m != PLACEHOLDER_CHANNEL]
            achados += [(caminho.name, m) for m in cookie_real.findall(texto)]
    assert achados == []


# ---------------------------------------------------------------------- SUB-SEC-07
@pytest.mark.parametrize(
    "url",
    [
        "https://studio.youtube.com",
        "https://studio.youtube.com/",
        "https://exemplo.test:8443/prefixo",
        "http://localhost:8123",
        "http://127.0.0.1:8123/www",
        "http://[::1]:8123",
    ],
)
def test_sub_sec_07_urls_aceitas(env, url):
    cfg = load_config({**env.env, "YOUTUBE_WWW_BASE_URL": url, "YOUTUBE_STUDIO_BASE_URL": url})
    assert cfg.www_base_url == url.rstrip("/")
    assert cfg.studio_base_url == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://studio.youtube.com",
        "http://example.com",
        "http://localhost.evil.com",
        "http://127.0.0.1.evil.com",
        "http://10.0.0.5:8080",
        "ftp://127.0.0.1",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "//evil.example",
        "https://",
        "https://usuario:senha@exemplo.test",
        "https://exemplo.test/?q=1",
        "http://127.0.0.1:abc",
        "localhost:8000",
    ],
)
@pytest.mark.parametrize("variavel", ["YOUTUBE_WWW_BASE_URL", "YOUTUBE_STUDIO_BASE_URL"])
def test_sub_sec_07_urls_recusadas(env, variavel, url):
    with pytest.raises(ConfigError):
        load_config({**env.env, variavel: url})


def test_sub_sec_07_padroes_sao_os_enderecos_oficiais(env):
    sem = {k: v for k, v in env.env.items() if not k.startswith("YOUTUBE_") or k == "YOUTUBE_CHANNEL_ID"}
    cfg = load_config(sem)
    assert cfg.www_base_url == "https://www.youtube.com"
    assert cfg.studio_base_url == "https://studio.youtube.com"


# ---------------------------------------------------------------------- SUB-BR-01
def test_sub_br_01_navegador_sem_sinais_de_automacao(env, make_worker, add_cookies):
    assert USER_AGENT == LEGACY_UA
    assert "--enable-automation" in IGNORE_DEFAULT_ARGS
    assert "--disable-blink-features=AutomationControlled" in CHROMIUM_ARGS

    add_cookies([cookie("a", "1")])
    worker = make_worker()
    worker.step()
    page = worker.session.page

    assert page.evaluate("navigator.webdriver") is None  # indefinido, como no original
    assert page.evaluate("typeof navigator.webdriver") == "undefined"
    assert page.evaluate("navigator.userAgent") == LEGACY_UA
    assert env.server.studio_requests()[0]["user_agent"] == LEGACY_UA
    assert page.evaluate("[window.innerWidth, window.innerHeight]") == [1280, 900]

    args_chromium = [arg for args in chromium_cmdlines() for arg in args]
    assert any(arg.startswith("--headless") for arg in args_chromium)
    assert "--window-size=1280,900" in args_chromium
    assert "--disable-gpu" in args_chromium
    assert "--disable-dev-shm-usage" in args_chromium
    assert "--enable-automation" not in args_chromium


# ---------------------------------------------------------------------- SUB-CFG
def test_sub_cfg_01_channel_id_obrigatorio_e_com_formato_uc_mais_22(env):
    sem = {k: v for k, v in env.env.items() if k != "YOUTUBE_CHANNEL_ID"}
    with pytest.raises(ConfigError):
        load_config(sem)
    for ruim in ("", "   ", "UCcurto", "XCxxxxxxxxxxxxxxxxxxxxxx", "UCxxxxxxxxxxxxxxxxxxxxxxx", "UCxxxxxxxxxxxxxxxxxxxxx!"):
        with pytest.raises(ConfigError):
            load_config({**env.env, "YOUTUBE_CHANNEL_ID": ruim})


def test_sub_cfg_01_url_do_studio_igual_a_do_legado_caractere_a_caractere():
    esperado = (
        "https://studio.youtube.com/channel/UCxxxxxxxxxxxxxxxxxxxxxx"
        "/analytics/tab-overview/period-default/explore"
        "?c=UCxxxxxxxxxxxxxxxxxxxxxx&entity_type=CHANNEL&entity_id=UCxxxxxxxxxxxxxxxxxxxxxx"
        "&time_period=4_weeks&explore_type=SUBSCRIBERS"
    )
    cfg = load_config(
        {"YOUTUBE_CHANNEL_ID": "UCxxxxxxxxxxxxxxxxxxxxxx", "COOKIE_PRIVATE_KEY_FILE": "/run/secrets/cookie_private_key"}
    )
    assert cfg.studio_url == esperado


def test_sub_cfg_01_sem_default_de_canal_no_codigo():
    codigo = "\n".join(p.read_text(encoding="utf-8") for p in PASTA_WORKER.glob("*.py"))
    assert not re.search(r"UC[0-9A-Za-z_-]{22}", codigo.replace(PLACEHOLDER_CHANNEL, ""))
