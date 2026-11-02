# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""Pilha REAL de integracao da T8: `docker compose` (compose real + tests/e2e/compose.e2e.yml), projeto proprio.

Unica fronteira simulada: o YouTube (tests/e2e/fake_youtube.py), alcancado pelos workers pelas URLs-base da D19.
Segredos sao gerados na hora com as ferramentas da propria api (`tools.gen_cookie_keys`, `tools.hash_password`) e
apagados no fim; a chave "da API" e uma sentinela aleatoria. Nada aqui le `./secrets/` do repositorio.

Testes `docker`/`e2e` FALHAM sem Docker (nunca sao pulados).
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
import random
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tests" / "infra"))

from docker_helpers import (  # noqa: E402  (reaproveita os ajudantes da T7 sem altera-los)
    CLIENT_CODE,
    PYTHON_IMAGE,
    docker,
    header,
    header_values,
    http_request,
    require_docker,
    wait_until,
)

__all__ = ["E2EStack", "Session", "get_stack", "parse_ts", "header", "header_values", "http_request", "wait_until", "docker"]

PROJECT = "livestats-t8"
IMAGES = {name: f"livestats-t8/{name}:test" for name in ("web", "api", "worker-lives", "worker-inscritos")}
CONTAINERS = {
    "web": "t8-web",
    "api": "t8-api",
    "worker-lives": "t8-worker-lives",
    "worker-inscritos": "t8-worker-inscritos",
}
FAKE_CONTAINERS = {"fake-lives": "t8-fake-lives", "fake-studio": "t8-fake-studio"}
ALL_CONTAINERS = {**CONTAINERS, **FAKE_CONTAINERS}
NETWORKS = ("livestats-t8-internal", "livestats-t8-edge", "livestats-t8-egress")
VOLUMES = ("livestats-t8-data", "livestats-t8-admin")
COMPOSE_FILES = [REPO / "docker-compose.yml", HERE / "compose.e2e.yml"]
RECORDED_FIXTURE = REPO / "tests" / "worker_lives" / "fixtures" / "videos_mixed.json"

PUBLIC_PORT = 8080
ADMIN_PORT = 8081
FAKE_PORT = 18555
USERNAME = "t8admin"
LIVE_INTERVAL = 10
SCRAPE_INTERVAL = 5

# IDs de video sinteticos (11 caracteres do alfabeto do YouTube) servidos pelo YouTube falso.
VID_LIVE, VID_UPCOMING, VID_ENDED, VID_OTHER = "AoVivo00001", "Agendada002", "Encerrada03", "Outro000004"
SLOT_URL = "https://www.youtube.com/watch?v={}"

_STACK: E2EStack | None = None
_FAILURE: BaseException | None = None
_LOCK = threading.Lock()


def get_stack() -> E2EStack:
    """Singleton da sessao de testes (e2e e security compartilham a mesma pilha)."""
    global _STACK, _FAILURE
    with _LOCK:
        if _FAILURE is not None:
            raise AssertionError(f"a pilha de teste nao subiu: {_FAILURE}")
        if _STACK is None:
            stack = E2EStack()
            atexit.register(stack.stop)
            try:
                stack.start()
            except BaseException as exc:
                _FAILURE = exc
                stack.stop()
                raise
            _STACK = stack
        return _STACK


@dataclass
class Session:
    cookie: str
    csrf: str

    @property
    def cookie_header(self) -> str:
        return f"__Host-livestats_session={self.cookie}"


@dataclass
class E2EStack:
    started: bool = False
    startup_seconds: float = 0.0
    _last_request: float = 0.0
    fresh: dict = field(default_factory=dict)

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        require_docker()
        for port in (PUBLIC_PORT, ADMIN_PORT):
            with socket.socket() as probe:
                probe.settimeout(1)
                if probe.connect_ex(("127.0.0.1", port)) == 0:
                    raise AssertionError(f"a porta {port} do host ja esta em uso; a pilha de teste precisa dela")
        self.tmp = Path(tempfile.mkdtemp(prefix="t8-"))
        self.secrets_dir = self.tmp / "secrets"
        self.state_dir = self.tmp / "state"
        for d in (self.secrets_dir, self.state_dir):
            d.mkdir()
        self.state_dir.chmod(0o777)

        self.subnet_third = random.randrange(100, 250)
        self.base = f"172.30.{self.subnet_third}"
        self.edge_subnet = f"{self.base}.0/24"
        self.cloudflared_ip = f"{self.base}.20"  # "cloudflared" do teste (TRUSTED_PROXY_CIDR)
        self.npm_ip = f"{self.base}.21"  # "NPM" do teste (ADMIN_TRUSTED_PROXY_IP)

        self.password = secrets.token_urlsafe(18)
        self.api_key = "T8KEY" + secrets.token_hex(16)  # sentinela: parece uma chave, nao e uma
        self.channel_id = "UC" + secrets.token_urlsafe(17)[:22].replace("-", "a").replace("_", "b")
        self.auth_cookie = {"name": "T8AUTH" + secrets.token_hex(4), "value": "T8CV" + secrets.token_hex(12)}
        self.extra_cookie = {"name": "T8EXTRA" + secrets.token_hex(4), "value": "T8EV" + secrets.token_hex(12)}
        self.env_extra = {"TRUSTED_PROXY_CIDR": f"{self.cloudflared_ip}/32", "ADMIN_TRUSTED_PROXY_IP": self.npm_ip}
        self.env_extra.pop("ADMIN_BIND", None)

        self._session: Session | None = None
        self.started = True  # a partir daqui o stop() limpa o que existir
        self._cleanup_leftovers()
        self.compose("build", timeout=1800)
        self._make_secrets()
        self._write_fake_state()
        t0 = time.time()
        self.compose("up", "-d", "--wait", "--wait-timeout", "180", timeout=400)
        self.startup_seconds = time.time() - t0
        self.up_started_at = t0
        self._snapshot_fresh()

    def env(self) -> dict:
        env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": PROJECT,
            "ADMIN_USERNAME": USERNAME,
            "YOUTUBE_CHANNEL_ID": self.channel_id,
            "EDGE_SUBNET": self.edge_subnet,
            "E2E_FAKE_PORT": str(FAKE_PORT),
            "E2E_FAKE_SRC": str(HERE / "fake_youtube.py"),
            "E2E_RECORDED_FIXTURE": str(RECORDED_FIXTURE),
            "E2E_STATE_DIR": str(self.state_dir),
            "E2E_SECRETS_DIR": str(self.secrets_dir),
            "TRUSTED_PROXY_CIDR": "",
            "ADMIN_TRUSTED_PROXY_IP": "",
        }
        env.pop("ADMIN_BIND", None)
        env.update(self.env_extra)
        return env

    def compose(self, *args, check=True, timeout=300, input_text=None):
        cmd = ["docker", "compose", "-p", PROJECT, "--project-directory", str(REPO)]
        for f in COMPOSE_FILES:
            cmd += ["-f", str(f)]
        r = subprocess.run(
            [*cmd, *args], capture_output=True, text=True, timeout=timeout, env=self.env(), encoding="utf-8",
            input=input_text,
        )
        if check and r.returncode != 0:
            raise AssertionError(f"compose {' '.join(args)} falhou:\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}")
        return r

    def _cleanup_leftovers(self) -> None:
        """Sobras de uma execucao anterior interrompida (mesmo projeto de teste)."""
        self.compose("down", "-v", "--remove-orphans", check=False, timeout=300)

    def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        try:
            extras = docker("ps", "-aq", "--filter", "label=livestats-t8-extra=1", check=False).stdout.split()
            if extras:
                docker("rm", "-f", *extras, check=False)
            self.compose("down", "-v", "--remove-orphans", check=False, timeout=300)
            for image in IMAGES.values():
                docker("rmi", "-f", image, check=False)
        finally:
            shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ segredos de teste
    def _make_secrets(self) -> None:
        sd = self.secrets_dir
        sd.chmod(0o777)  # o uid 10001 da imagem da api precisa gravar aqui (ferramenta gen_cookie_keys)
        docker(
            "run", "--rm", "-v", f"{sd}:/out", IMAGES["api"], "python", "-m", "tools.gen_cookie_keys",
            "--output-dir", "/out",
        )
        r = docker("run", "--rm", "-i", IMAGES["api"], "python", "-m", "tools.hash_password", input_text=self.password + "\n")
        pwd_hash = r.stdout.strip()
        assert pwd_hash.startswith("$argon2id$")
        (sd / "admin_password_hash.txt").write_text(pwd_hash + "\n", encoding="ascii")
        (sd / "youtube_api_key.txt").write_text(self.api_key + "\n", encoding="ascii")
        for name in ("admin_password_hash.txt", "youtube_api_key.txt"):
            (sd / name).chmod(0o644)
        self.pwd_hash = pwd_hash

    # ------------------------------------------------------------------ YouTube falso
    def default_videos(self) -> dict:
        return {
            VID_LIVE: {"title": "T8 Live ao vivo", "broadcast": "live", "views": 5000, "likes": 640, "concurrent": 3374},
            VID_UPCOMING: {"title": "T8 Live agendada", "broadcast": "upcoming", "views": 0, "likes": 90},
            VID_ENDED: {"title": "T8 Live encerrada", "broadcast": "none", "views": 1250000, "likes": 99950},
            VID_OTHER: {"title": "T8 Outro video", "broadcast": "live", "views": 777, "likes": 7, "concurrent": 321},
        }

    def _write_fake_state(self) -> None:
        self.fake_state = {
            "api_key_sha256": hashlib.sha256(self.api_key.encode()).hexdigest(),
            "videos": self.default_videos(),
            "subscribers": "460600",
            "auth_cookie": self.auth_cookie,
            "login_ok": True,
        }
        self.fake_write()

    def fake_write(self) -> None:
        tmp = self.state_dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.fake_state), encoding="utf-8")
        os.replace(tmp, self.state_dir / "state.json")

    def fake_set(self, **changes) -> None:
        self.fake_state.update(changes)
        self.fake_write()

    def fake_set_video(self, video_id: str, **changes) -> None:
        self.fake_state["videos"].setdefault(video_id, {})
        self.fake_state["videos"][video_id].update(changes)
        self.fake_write()

    def fake_requests(self, name: str, kind: str | None = None, since: float = 0.0) -> list[dict]:
        path = self.state_dir / f"{name}-requests.jsonl"
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return [r for r in rows if r["t"] >= since and (kind is None or r.get("kind") == kind)]

    # ------------------------------------------------------------------ HTTP
    def public(self, path, method="GET", headers=None, body=None):
        return self.on_port(PUBLIC_PORT, path, method, headers, body)

    def admin(self, path, method="GET", headers=None, body=None):
        return self.on_port(ADMIN_PORT, path, method, headers, body)

    def on_port(self, port, path, method="GET", headers=None, body=None):
        """Requisicao do host. Espacada (~9 req/s) para nao estourar o limit_req do proprio IP do host (10 r/s)."""
        wait = 0.11 - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        try:
            return http_request("127.0.0.1", port, path, method, headers, body)
        finally:
            self._last_request = time.monotonic()

    def login(self, username=None, password=None, port=ADMIN_PORT, patience=90) -> Session:
        """Login real (corpo JSON). Espera e repete se o limite de tentativas (429) estiver ativo por testes anteriores."""
        payload = json.dumps({"username": username or USERNAME, "password": password or self.password})
        deadline = time.time() + patience
        while True:
            r = self.on_port(
                port, "/api/admin/login", "POST", {"Content-Type": "application/json"}, payload
            )
            if r["status"] == 429 and time.time() < deadline:
                time.sleep(float(header(r, "Retry-After") or 5) + 1)
                continue
            assert r["status"] == 200, f"login falhou: {r['status']} {r['body'][:200]}"
            body = json.loads(r["body"])
            cookie = [v for v in header_values(r, "Set-Cookie") if v.startswith("__Host-livestats_session=")][0]
            return Session(cookie.split(";", 1)[0].split("=", 1)[1], body["csrf_token"])

    @property
    def session(self) -> Session:
        if self._session is None:
            self._session = self.login()
        return self._session

    def api(self, method, path, data=None, session: Session | None = None, csrf=True, port=ADMIN_PORT, headers=None):
        """Chamada autenticada a /api/admin/*. `session=None` usa a sessao padrao da pilha."""
        session = session or self.session
        h = {"Cookie": session.cookie_header}
        if csrf:
            h["X-CSRF-Token"] = session.csrf
        body = None
        if data is not None:
            body = json.dumps(data)
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        return self.on_port(port, path, method, h, body)

    def set_slots(self, slots: list[str]) -> None:
        r = self.api("PUT", "/api/admin/slots", {"slots": slots})
        assert r["status"] == 200, r["body"]

    def upload_cookies(self, cookies: list[dict] | None = None) -> dict:
        cookies = cookies or self.default_cookies()
        r = self.api("POST", "/api/admin/cookies", cookies)
        assert r["status"] == 200, r["body"]
        return json.loads(r["body"])

    def default_cookies(self) -> list[dict]:
        return [
            {**self.auth_cookie, "domain": "127.0.0.1", "path": "/", "secure": False},
            {**self.extra_cookie, "domain": "127.0.0.1", "path": "/", "secure": False},
        ]

    def public_json(self, path, port=PUBLIC_PORT):
        r = self.on_port(port, path)
        assert r["status"] == 200, f"{path}: {r['status']} {r['body'][:200]}"
        return json.loads(r["body"], object_pairs_hook=dict)

    def workers_status(self) -> dict:
        r = self.api("GET", "/api/admin/workers/status")
        assert r["status"] == 200, r["body"]
        return json.loads(r["body"])

    # ------------------------------------------------------------------ docker
    def exec(self, name: str, *cmd: str, input_text: str | None = None, check=True, timeout=120):
        container = ALL_CONTAINERS[name]
        args = ["exec", "-i", container, *cmd]
        return docker(*args, check=check, timeout=timeout, input_text=input_text if input_text is not None else "")

    def py(self, name: str, code: str, *args: str, input_text: str | None = None, check=True):
        return self.exec(name, "python", "-c", code, *args, input_text=input_text, check=check)

    def inspect(self, name: str) -> dict:
        return json.loads(docker("inspect", ALL_CONTAINERS[name]).stdout)[0]

    def logs(self, name: str) -> str:
        r = docker("logs", ALL_CONTAINERS[name], check=False)
        return r.stdout + r.stderr

    def client(self, last_octet: int, requests: list[dict], port: int = PUBLIC_PORT, host: str = "web") -> list[dict]:
        """Requisicoes de um container com IP FIXO na rede edge (a origem que o Nginx enxerga e controlada)."""
        payload = json.dumps({"host": host, "port": port, "requests": requests})
        r = docker(
            "run", "--rm", "--network", "livestats-t8-edge", "--ip", f"{self.base}.{last_octet}", PYTHON_IMAGE,
            "python", "-c", CLIENT_CODE, payload, timeout=300,
        )
        return json.loads(r.stdout)

    def recreate_web(self, **env) -> None:
        """Recria so o `web` com outras variaveis (TRUSTED_PROXY_CIDR, ADMIN_TRUSTED_PROXY_IP, ADMIN_BIND)."""
        for key, value in env.items():
            if value is None:
                self.env_extra.pop(key, None)
            else:
                self.env_extra[key] = value
        self.compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "--wait-timeout", "90", "web", timeout=200)

    def restart_workers(self, which=("worker-lives", "worker-inscritos")) -> None:
        """Reinicia workers e seus YouTubes falsos (o falso vive no namespace de rede do worker)."""
        fakes = [{"worker-lives": "fake-lives", "worker-inscritos": "fake-studio"}[w] for w in which]
        self.compose("restart", *which, timeout=200)
        self.compose("restart", *fakes, timeout=200)
        self.wait_healthy(which)

    def wait_healthy(self, names=("api", "web", "worker-lives", "worker-inscritos"), timeout=150) -> None:
        def ok():
            for n in names:
                st = self.inspect(n)["State"]
                if not st["Running"] or st.get("Health", {}).get("Status") != "healthy":
                    return False
            return True

        wait_until(ok, timeout=timeout, interval=2)

    def _snapshot_fresh(self) -> None:
        """Respostas logo apos a subida do zero (antes de qualquer dado): serve a 404/503 de INT/SEC."""
        snap = {}
        for port in (PUBLIC_PORT, ADMIN_PORT):
            for path in ("/inscritos.json", "/api/public/subscribers", "/live1.json", "/api/public/lives/1", "/health"):
                snap[(port, path)] = self.on_port(port, path, headers={"Origin": "https://qualquer.example"})
        self.fresh = snap

    # ------------------------------------------------------------------ estados conhecidos (idempotentes)
    def default_slots(self) -> list[str]:
        return [SLOT_URL.format(VID_LIVE), SLOT_URL.format(VID_UPCOMING), SLOT_URL.format(VID_ENDED), ""]

    def ensure_lives(self) -> None:
        """Slots 1-3 com os videos padrao, slot 4 vazio, e o worker-lives ja refletindo isso."""
        self.fake_set(videos=self.default_videos())
        self.set_slots(self.default_slots())

        def ready():
            one = self.public_json("/live1.json")[0]
            three = self.public_json("/live3.json")[0]
            four = self.public_json("/live4.json")[0]
            return (
                one["video_id"] == VID_LIVE and one["titulo"] == "T8 Live ao vivo"
                and three["video_id"] == VID_ENDED and four["status"] == "sem_url"
            )

        wait_until(ready, timeout=3 * LIVE_INTERVAL, interval=1)

    def ensure_subs(self, value: str = "460600") -> None:
        """Cookies de teste aplicados e leitura do contador corrente (`value`)."""
        self.fake_set(login_ok=True, subscribers=value)
        st = self.workers_status()
        healthy = (
            st["cookies"]["configured"] and st["cookies"]["applied"]
            and st["workers"]["inscritos"]["auth_state"] == "ok"
        )
        if not healthy:
            self.upload_cookies()

        def ready():
            s = self.workers_status()
            sub = s["subscribers"]
            return (
                s["cookies"]["applied"] and s["workers"]["inscritos"]["auth_state"] == "ok"
                and sub is not None and sub["inscritos"] == value
            )

        wait_until(ready, timeout=90, interval=1)


def parse_ts(text: str):
    """'2026-11-02T16:43:22.101713Z' -> datetime (UTC)."""
    from datetime import datetime

    return datetime.fromisoformat(text.replace("Z", "+00:00"))
