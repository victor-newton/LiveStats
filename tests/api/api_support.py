"""Apoio dos testes da api: montagem da pilha (app + bancos reais em arquivos temporarios), relogio
falso (fronteira permitida), validacao de respostas contra ``docs/openapi.yaml`` e leitura direta dos
bancos (o teste faz o papel dos workers)."""

from __future__ import annotations

import base64
import secrets
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

REPO = Path(__file__).resolve().parents[2]
API_DIR = REPO / "api"
APP_DIR = API_DIR / "app"
TOOLS_DIR = API_DIR / "tools"
GOLDEN = REPO / "tests" / "contract" / "legacy"
DB_DIR = REPO / "db"

if str(API_DIR) not in sys.path:  # o pytest.ini da raiz (T2) ja faz `pythonpath = . api`; isto cobre rodar sem ele
    sys.path.insert(0, str(API_DIR))

from app.main import create_app  # noqa: E402

BASE_URL = "https://testserver"  # o cookie de sessao e Secure
SESSION_COOKIE = "__Host-livestats_session"
START_EPOCH = 1_800_000_000.0  # 2027-01-15T08:00:00Z


class FakeClock:
    """Relogio injetavel (fronteira externa permitida pelo plano de testes)."""

    def __init__(self, start: float = START_EPOCH) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


# ------------------------------------------------------------------------------------------ ambiente
@dataclass
class Stack:
    app: object
    client: TestClient
    env: dict
    clock: FakeClock
    username: str
    password: str
    private_key: object
    public_key_bytes: bytes
    tmp: Path
    extra_clients: list = field(default_factory=list)

    # caminhos
    @property
    def db_path(self) -> str:
        return self.env["DB_PATH"]

    @property
    def admin_db_path(self) -> str:
        return self.env["ADMIN_DB_PATH"]

    def shared(self) -> sqlite3.Connection:
        """Conexao SIMPLES (sem autorizador) a livestats.db: o teste faz o papel de worker/inspetor."""
        return plain_connect(self.db_path)

    def admin(self) -> sqlite3.Connection:
        return plain_connect(self.admin_db_path)

    def new_client(self) -> TestClient:
        """Outro navegador (cookie jar proprio) falando com a MESMA app ja iniciada."""
        other = TestClient(self.app, base_url=BASE_URL)
        self.extra_clients.append(other)
        return other

    def db_files(self, name: str) -> list[Path]:
        base = Path(self.env[name])
        return [p for p in (base, Path(str(base) + "-wal"), Path(str(base) + "-shm")) if p.exists()]

    def all_db_bytes(self, name: str) -> bytes:
        return b"".join(p.read_bytes() for p in self.db_files(name))

    # autenticacao
    def login(self, client: TestClient | None = None, ip: str | None = None, username: str | None = None,
              password: str | None = None):
        client = client or self.client
        headers = {"X-Real-IP": ip} if ip else {}
        return client.post(
            "/api/admin/login",
            json={"username": username if username is not None else self.username,
                  "password": password if password is not None else self.password},
            headers=headers,
        )

    def login_csrf(self, client: TestClient | None = None, ip: str | None = None) -> str:
        resp = self.login(client, ip)
        assert resp.status_code == 200, resp.text
        return resp.json()["csrf_token"]


def plain_connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def write_secret(path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


def make_env(tmp: Path, password_hash: str, public_key_bytes: bytes, username: str, **overrides: str | None) -> dict:
    """Variaveis de ambiente da api. ``overrides[nome] = None`` remove a variavel."""
    secrets_dir = tmp / "secrets"
    env = {
        "DB_PATH": str(tmp / "data" / "livestats.db"),
        "ADMIN_DB_PATH": str(tmp / "admin-state" / "admin.db"),
        "ADMIN_USERNAME": username,
        "ADMIN_PASSWORD_HASH_FILE": write_secret(secrets_dir / "admin_password_hash", password_hash + "\n"),
        "COOKIE_PUBLIC_KEY_FILE": write_secret(
            secrets_dir / "cookie_public_key", base64.b64encode(public_key_bytes).decode("ascii") + "\n"
        ),
        "DB_SCHEMA_FILE": str(DB_DIR / "schema.sql"),
        "ADMIN_DB_SCHEMA_FILE": str(DB_DIR / "admin_schema.sql"),
    }
    (tmp / "data").mkdir(exist_ok=True)
    (tmp / "admin-state").mkdir(exist_ok=True)
    for key, value in overrides.items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    return env


@contextmanager
def running_stack(tmp: Path, creds, **overrides):
    """Sobe a app com bancos e segredos temporarios (lifespan real) e entrega a ``Stack``."""
    env = make_env(tmp, creds.password_hash, creds.public_key_bytes, creds.username, **overrides)
    clock = FakeClock()
    app = create_app(env, clock)
    with TestClient(app, base_url=BASE_URL) as client:
        yield Stack(
            app=app, client=client, env=env, clock=clock, username=creds.username, password=creds.password,
            private_key=creds.private_key, public_key_bytes=creds.public_key_bytes, tmp=tmp,
        )


def is_authorizer_denial(exc: BaseException) -> bool:
    """Recusa do autorizador do SQLite: ``not authorized`` (acoes) ou ``access to <t>.<c> is prohibited`` (colunas)."""
    return isinstance(exc, sqlite3.DatabaseError) and ("not authorized" in str(exc) or "is prohibited" in str(exc))


def random_token(prefix: str = "") -> str:
    return prefix + secrets.token_hex(12)


# --------------------------------------------------------------------------------------- dados semente
GOLDEN_LIVE_ROWS = {
    # valores de entrada que geram os golden files (docs/legacy-formats.md 1.2)
    1: dict(active=1, video_id="dQw4w9WgXcQ", title="Sorteio ao vivo — açaí & café <b>teste</b>", status="ao_vivo",
            views=1234, likes=56, views_formatted="1.2K", likes_formatted="56",
            updated_at="2026-11-02T14:32:00.123456Z"),
    2: dict(active=1, video_id="9bZkp7q19f0", title="Estreia amanhã", status="agendada", views=0, likes=0,
            views_formatted="0", likes_formatted="0", updated_at="2026-11-02T14:32:00.234567Z"),
    3: dict(active=1, video_id="jNQXAC9IVRw", title="Replay completo", status="encerrada", views=1250000,
            likes=99950, views_formatted="1.2M", likes_formatted="100.0K", updated_at="2026-11-02T14:32:00.345678Z"),
    4: dict(active=0, video_id="", title="", status="sem_url", views=0, likes=0, views_formatted="0",
            likes_formatted="0", updated_at="2026-11-02T14:32:00.456789Z"),
}
GOLDEN_FILES = {1: "live1_ao_vivo.json", 2: "live2_agendada.json", 3: "live3_encerrada.json", 4: "live4_sem_url.json"}


def seed_live(conn: sqlite3.Connection, slot: int, **row) -> None:
    """Grava uma linha de ``live_stats`` como o worker-lives faria."""
    data = {**GOLDEN_LIVE_ROWS[slot], **row}
    conn.execute(
        "INSERT OR REPLACE INTO live_stats (slot, active, video_id, title, status, views, likes, views_formatted, "
        "likes_formatted, updated_at) VALUES (:slot, :active, :video_id, :title, :status, :views, :likes, "
        ":views_formatted, :likes_formatted, :updated_at)",
        {"slot": slot, **data},
    )
    conn.commit()


def seed_golden_lives(conn: sqlite3.Connection) -> None:
    for slot in GOLDEN_LIVE_ROWS:
        seed_live(conn, slot)


GOLDEN_SUBSCRIBERS = dict(value="460600", raw="460600", read_at="2026-11-02T14:32:10.654321Z")


def seed_subscribers(conn: sqlite3.Connection, **row) -> None:
    data = {**GOLDEN_SUBSCRIBERS, **row}
    conn.execute(
        "INSERT OR REPLACE INTO subscriber_reading (id, value, raw, read_at) VALUES (1, :value, :raw, :read_at)", data
    )
    conn.commit()


# ------------------------------------------------------------------------------- contrato (openapi)
_SPEC = yaml.safe_load((REPO / "docs" / "openapi.yaml").read_text(encoding="utf-8"))


_EFFECTIVE = _SPEC  # o contrato exatamente como esta em docs/openapi.yaml
_REGISTRY = Registry().with_resource(
    "urn:livestats:openapi", Resource.from_contents(_EFFECTIVE, default_specification=DRAFT202012)
)


def _validate(instance, schema: dict) -> None:
    Draft202012Validator(schema, registry=_REGISTRY).validate(instance)


def assert_schema(instance, name: str) -> None:
    """Valida ``instance`` contra ``components.schemas.<name>`` do openapi.yaml."""
    _validate(instance, {"$ref": f"urn:livestats:openapi#/components/schemas/{name}"})


def _resolve_response(response_spec: dict) -> dict:
    ref = response_spec.get("$ref")
    if ref:
        return _EFFECTIVE["components"]["responses"][ref.rsplit("/", 1)[-1]]
    return response_spec


def assert_matches_openapi(resp, template: str, method: str) -> None:
    """A resposta (status + corpo JSON) esta declarada e e conforme em ``paths[template][method]``."""
    operation = _EFFECTIVE["paths"][template][method.lower()]
    declared = operation["responses"]
    key = str(resp.status_code)
    assert key in declared, f"{method} {template}: status {key} nao declarado no contrato ({sorted(declared)})"
    content = _resolve_response(declared[key]).get("content", {}).get("application/json")
    if content is None:
        return
    schema = content["schema"]
    _validate(resp.json(), _rewrite_refs(schema))


def _rewrite_refs(node):
    if isinstance(node, dict):
        return {
            k: (f"urn:livestats:openapi{v}" if k == "$ref" and isinstance(v, str) and v.startswith("#/") else _rewrite_refs(v))
            for k, v in node.items()
        }
    if isinstance(node, list):
        return [_rewrite_refs(i) for i in node]
    return node


def walk_keys(node):
    """Todas as chaves de um JSON (para provar que ``input`` nunca aparece)."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_keys(item)


# ------------------------------------------------------------------------------- slots (D34: url + meta + cor)
DEFAULT_COLOR = "#FF0033"


def slot_item(url: str = "", goal: int | None = None, color: str = DEFAULT_COLOR) -> dict:
    """Um item do corpo de ``PUT /api/admin/slots`` no formato da D34."""
    return {"url": url, "goal": goal, "color": color}


def slot_items(*urls: str) -> list[dict]:
    """Itens com so a URL preenchida (meta nula, cor padrao)."""
    return [slot_item(url) for url in urls]


BLANK_SLOTS = slot_items("", "", "", "")
