"""Configuracao do worker-inscritos, lida SOMENTE de variaveis de ambiente nao secretas e de arquivos.

- A chave privada vem do ARQUIVO apontado por COOKIE_PRIVATE_KEY_FILE (Docker secret); nunca de variavel
  de ambiente com o valor e nunca com valor padrao (ARCHITECTURE.md sec. 4).
- Nao existe aqui (nem em lugar nenhum do worker) variavel de ambiente que ative caminho de teste: as URLs-base
  opcionais (D19) valem igualmente em producao e so aceitam https, ou http para loopback.
"""

from __future__ import annotations

import base64
import binascii
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from nacl.public import PrivateKey, PublicKey

DEFAULT_DB_PATH = "/data/livestats.db"
DEFAULT_WWW_BASE_URL = "https://www.youtube.com"
DEFAULT_STUDIO_BASE_URL = "https://studio.youtube.com"

DEFAULT_SCRAPE_INTERVAL = 10
DEFAULT_RELOAD_INTERVAL = 60  # D47
MIN_RELOAD_INTERVAL = 15
MAX_RELOAD_INTERVAL = 3600
DEFAULT_USER_POOL_SIZE = 1  # D51: paginas do pool dos contadores de usuario (0 desliga)
MAX_USER_POOL_SIZE = 8
DEFAULT_USER_COUNTER_INTERVAL = 60  # D51: segundos entre leituras de cada contador de usuario
MIN_USER_COUNTER_INTERVAL = 30
MAX_USER_COUNTER_INTERVAL = 3600
DEFAULT_MAX_USER_COUNTERS = 20  # D51: contadores de usuario ativos ao mesmo tempo
MAX_MAX_USER_COUNTERS = 500
MIN_SCRAPE_INTERVAL = 5
MAX_SCRAPE_INTERVAL = 3600  # worker_status.interval_seconds BETWEEN 1 AND 3600

CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class ConfigError(Exception):
    """Erro de configuracao. A mensagem nunca contem valores secretos."""


@dataclass(frozen=True)
class Config:
    db_path: str
    private_key_file: str
    public_key_file: str
    scrape_interval: int
    debug_screenshot: bool
    debug_dir: str
    www_base_url: str
    studio_base_url: str
    reload_interval: int = DEFAULT_RELOAD_INTERVAL  # D47: 0 desliga; segundos entre reaberturas do Studio
    user_pool_size: int = DEFAULT_USER_POOL_SIZE  # D51: 0 desliga os contadores de usuario
    user_counter_interval: int = DEFAULT_USER_COUNTER_INTERVAL
    max_user_counters: int = DEFAULT_MAX_USER_COUNTERS

    @property
    def studio_home_url(self) -> str:
        """Pagina inicial do Studio: com cookies validos redireciona para /channel/UC... (D38)."""
        return f"{self.studio_base_url}/"

    def studio_url_for(self, cid: str) -> str:
        """URL da pagina de analytics de inscritos do canal `cid` (o ID vem da deteccao, D38)."""
        return (
            f"{self.studio_base_url}/channel/{cid}"
            f"/analytics/tab-overview/period-default/explore"
            f"?c={cid}&entity_type=CHANNEL&entity_id={cid}"
            f"&time_period=4_weeks&explore_type=SUBSCRIBERS"
        )


def extrair_channel_id(url: str) -> str | None:
    """ID do canal (UC + 22) no caminho de uma URL do Studio, ou None (D38)."""
    try:
        caminho = urlsplit(url).path
    except ValueError:
        return None
    achado = CHANNEL_ID_RE.search(caminho)
    return achado.group(0) if achado else None


def validar_base_url(nome: str, valor: str) -> str:
    """Aceita so `https`, ou `http` para localhost/127.0.0.1/::1 (SUB-SEC-07). Devolve sem barra final."""
    try:
        partes = urlsplit(valor)
        host = partes.hostname
        partes.port  # noqa: B018 - levanta ValueError se a porta for invalida
    except ValueError:
        raise ConfigError(f"{nome}: URL invalida") from None
    if partes.scheme not in ("http", "https") or not host:
        raise ConfigError(f"{nome}: use uma URL https (ou http para localhost)")
    if partes.scheme == "http" and host not in _LOOPBACK_HOSTS:
        raise ConfigError(f"{nome}: http so e aceito para localhost, 127.0.0.1 ou ::1")
    if partes.username is not None or partes.password is not None or partes.query or partes.fragment:
        raise ConfigError(f"{nome}: a URL nao pode ter credenciais, query nem fragmento")
    return valor.rstrip("/")


def _ler_intervalo(env: Mapping[str, str]) -> int:
    bruto = env.get("SCRAPE_INTERVAL", "").strip()
    if not bruto:
        return DEFAULT_SCRAPE_INTERVAL
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfigError("SCRAPE_INTERVAL deve ser um inteiro") from None
    if valor < MIN_SCRAPE_INTERVAL:
        raise ConfigError(f"SCRAPE_INTERVAL deve ser >= {MIN_SCRAPE_INTERVAL}")
    if valor > MAX_SCRAPE_INTERVAL:
        raise ConfigError(f"SCRAPE_INTERVAL deve ser <= {MAX_SCRAPE_INTERVAL}")
    return valor


def _ler_recarga(env: Mapping[str, str]) -> int:
    """D47: STUDIO_RELOAD_INTERVAL (s). O contador do Studio (`yta-smooth-counter`) nao mostra quedas sozinho; reabrir a
    pagina de tempos em tempos le o valor real. 0 desliga."""
    bruto = env.get("STUDIO_RELOAD_INTERVAL", "").strip()
    if not bruto:
        return DEFAULT_RELOAD_INTERVAL
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfigError("STUDIO_RELOAD_INTERVAL deve ser um inteiro") from None
    if valor != 0 and not MIN_RELOAD_INTERVAL <= valor <= MAX_RELOAD_INTERVAL:
        raise ConfigError(f"STUDIO_RELOAD_INTERVAL deve ser 0 ou estar entre {MIN_RELOAD_INTERVAL} e {MAX_RELOAD_INTERVAL}")
    return valor


def _ler_inteiro(env: Mapping[str, str], nome: str, padrao: int, minimo: int, maximo: int, permite_zero: bool = False) -> int:
    bruto = env.get(nome, "").strip()
    if not bruto:
        return padrao
    try:
        valor = int(bruto)
    except ValueError:
        raise ConfigError(f"{nome} deve ser um inteiro") from None
    if permite_zero and valor == 0:
        return 0
    if not minimo <= valor <= maximo:
        faixa = f"0 ou estar entre {minimo} e {maximo}" if permite_zero else f"estar entre {minimo} e {maximo}"
        raise ConfigError(f"{nome} deve ser {faixa}")
    return valor


def load_config(env: Mapping[str, str] | None = None) -> Config:
    env = os.environ if env is None else env

    key_file = env.get("COOKIE_PRIVATE_KEY_FILE", "").strip()
    if not key_file:
        raise ConfigError("COOKIE_PRIVATE_KEY_FILE e obrigatorio (caminho do arquivo de segredo)")

    pub_file = env.get("COOKIE_PUBLIC_KEY_FILE", "").strip()
    if not pub_file:
        raise ConfigError("COOKIE_PUBLIC_KEY_FILE e obrigatorio (caminho do arquivo da chave publica, D37)")

    db_path = env.get("DB_PATH", "").strip() or DEFAULT_DB_PATH
    debug = env.get("DEBUG_SCREENSHOT", "").strip() == "1"
    debug_dir = str(Path(env.get("TMPDIR", "").strip() or "/tmp") / "debug")
    if debug:
        # O print do Studio mostra dados do canal (S3): nunca pode cair no volume de dados.
        try:
            Path(debug_dir).resolve().relative_to(Path(db_path).resolve().parent)
        except ValueError:
            pass
        else:
            raise ConfigError("DEBUG_SCREENSHOT: o diretorio de debug nao pode ficar junto do banco de dados")

    return Config(
        db_path=db_path,
        private_key_file=key_file,
        public_key_file=pub_file,
        scrape_interval=_ler_intervalo(env),
        reload_interval=_ler_recarga(env),
        user_pool_size=_ler_inteiro(env, "USER_POOL_SIZE", DEFAULT_USER_POOL_SIZE, 1, MAX_USER_POOL_SIZE, permite_zero=True),
        user_counter_interval=_ler_inteiro(
            env, "USER_COUNTER_INTERVAL", DEFAULT_USER_COUNTER_INTERVAL, MIN_USER_COUNTER_INTERVAL, MAX_USER_COUNTER_INTERVAL
        ),
        max_user_counters=_ler_inteiro(env, "MAX_USER_COUNTERS", DEFAULT_MAX_USER_COUNTERS, 1, MAX_MAX_USER_COUNTERS),
        debug_screenshot=debug,
        debug_dir=debug_dir,
        www_base_url=validar_base_url(
            "YOUTUBE_WWW_BASE_URL", env.get("YOUTUBE_WWW_BASE_URL", "").strip() or DEFAULT_WWW_BASE_URL
        ),
        studio_base_url=validar_base_url(
            "YOUTUBE_STUDIO_BASE_URL", env.get("YOUTUBE_STUDIO_BASE_URL", "").strip() or DEFAULT_STUDIO_BASE_URL
        ),
    )


def ler_chave_privada(caminho: str) -> PrivateKey:
    """Le a chave privada Curve25519 (32 bytes em base64) do arquivo de segredo. Sem valor padrao."""
    try:
        conteudo = Path(caminho).read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise ConfigError("arquivo da chave privada (COOKIE_PRIVATE_KEY_FILE) nao encontrado") from None
    except (OSError, UnicodeDecodeError):
        raise ConfigError("arquivo da chave privada (COOKIE_PRIVATE_KEY_FILE) ilegivel") from None
    if not conteudo:
        raise ConfigError("arquivo da chave privada (COOKIE_PRIVATE_KEY_FILE) vazio")
    try:
        bruto = base64.b64decode(conteudo, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError("chave privada invalida: esperado base64 de 32 bytes") from None
    if len(bruto) != PrivateKey.SIZE:
        raise ConfigError("chave privada invalida: esperado base64 de 32 bytes")
    return PrivateKey(bruto)


def ler_chave_publica(caminho: str, private_key: PrivateKey) -> PublicKey:
    """Le a chave PUBLICA (D37: so cifra) e confere que e o par da privada. Sem valor padrao."""
    try:
        conteudo = Path(caminho).read_text(encoding="ascii").strip()
    except FileNotFoundError:
        raise ConfigError("arquivo da chave publica (COOKIE_PUBLIC_KEY_FILE) nao encontrado") from None
    except (OSError, UnicodeDecodeError):
        raise ConfigError("arquivo da chave publica (COOKIE_PUBLIC_KEY_FILE) ilegivel") from None
    try:
        bruto = base64.b64decode(conteudo, validate=True)
    except (binascii.Error, ValueError):
        raise ConfigError("chave publica invalida: esperado base64 de 32 bytes") from None
    if len(bruto) != PublicKey.SIZE:
        raise ConfigError("chave publica invalida: esperado base64 de 32 bytes")
    chave = PublicKey(bruto)
    if bytes(chave) != bytes(private_key.public_key):
        raise ConfigError("a chave publica (COOKIE_PUBLIC_KEY_FILE) nao e o par da chave privada")
    return chave
