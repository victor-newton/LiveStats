# ruff: noqa: E501, S603, S607, S108, S310, S311, B007, B018
"""INT-03 e INT-04: compatibilidade legada byte a byte pelas duas portas e script de comparacao com os apps atuais."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from e2e_stack import REPO, header

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

GOLDEN = REPO / "tests" / "contract" / "legacy"
SCRIPT = REPO / "scripts" / "compare_with_legacy.py"
LIVE_FILES = {1: "live1_ao_vivo.json", 2: "live2_agendada.json", 3: "live3_encerrada.json", 4: "live4_sem_url.json"}

# Semeia o banco compartilhado com os MESMOS valores dos golden files (colunas de db/schema.sql).
SEED = r"""
import json, sqlite3, sys
payload = json.load(sys.stdin)
con = sqlite3.connect("/data/livestats.db", timeout=10)
con.execute("PRAGMA busy_timeout = 5000")
for o in payload["lives"]:
    con.execute(
        "INSERT OR REPLACE INTO live_stats (slot, active, video_id, title, status, views, likes, views_formatted,"
        " likes_formatted, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (o["slot"], 1 if o["ativo"] == "true" else 0, o["video_id"], o["titulo"], o["status"], o["views"], o["likes"],
         o["views_formatado"], o["likes_formatado"], o["atualizado_em"]),
    )
s = payload["subscribers"]
con.execute("INSERT OR REPLACE INTO subscriber_reading (id, value, raw, read_at) VALUES (1,?,?,?)",
            (s["inscritos"], s["raw"], s["atualizado_em"]))
con.commit()
"""


def test_int_04_a_pilha_ao_vivo_tem_a_mesma_estrutura_nas_duas_portas(stack):
    """Estrutura (campos, ordem, tipos, formato dos bytes) da pilha com dados REAIS dos workers, 8081 x 8080."""
    stack.ensure_lives()
    stack.ensure_subs("460600")
    run = _run_script("--legacy-url", "http://127.0.0.1:8081", "--new-url", "http://127.0.0.1:8080")
    assert run.returncode == 0, run.stdout + run.stderr
    # e contra os golden files (estrutura do legado, valores de outros dados): mesma ordem de chaves
    server, _ = _serve(_golden_routes())
    try:
        legacy_url = f"http://127.0.0.1:{server.server_address[1]}"
        structure = _run_script("--legacy-url", legacy_url, "--new-url", "http://127.0.0.1:8080")
        # slots 1-3 e 4 da pilha tem a mesma estrutura dos golden files correspondentes (status do slot 4 = sem_url)
        assert structure.returncode == 0, structure.stdout + structure.stderr
    finally:
        server.shutdown()


@pytest.fixture(scope="module")
def seeded(stack):
    """Para os workers (senao reescrevem o banco), semeia os valores dos golden files e restaura tudo no fim."""
    stack.session  # login antes de mexer em qualquer coisa
    lives = [json.loads((GOLDEN / name).read_text(encoding="utf-8"))[0] for name in LIVE_FILES.values()]
    subscribers = json.loads((GOLDEN / "inscritos.json").read_text(encoding="utf-8"))
    stack.compose("stop", "worker-lives", "worker-inscritos", "fake-lives", "fake-studio", timeout=200)
    try:
        stack.py("api", SEED, input_text=json.dumps({"lives": lives, "subscribers": subscribers}))
        yield
    finally:
        stack.compose("start", "worker-lives", "worker-inscritos", "fake-lives", "fake-studio", timeout=200)
        stack.restart_workers()
        stack.ensure_lives()
        stack.ensure_subs("460600")


def _bytes(resp) -> bytes:
    return resp["body"].encode("utf-8")


def _headers_without_volatile(resp) -> dict:
    drop = {"date", "connection"}
    return {k.lower(): v for k, v in resp["headers"] if k.lower() not in drop}


def test_int_03_formatos_legados_byte_a_byte_pelas_duas_portas(stack, seeded):
    responses = {}
    for port in (8080, 8081):
        for n, name in LIVE_FILES.items():
            r = stack.on_port(port, f"/live{n}.json", headers={"Origin": "https://qualquer.example"})
            assert r["status"] == 200, (port, n, r["body"][:200])
            assert _bytes(r) == (GOLDEN / name).read_bytes(), f"/live{n}.json na porta {port} difere do golden"
            responses[(port, f"/live{n}.json")] = r
        r = stack.on_port(port, "/inscritos.json", headers={"Origin": "https://qualquer.example"})
        assert r["status"] == 200
        assert _bytes(r) == (GOLDEN / "inscritos.json").read_bytes(), f"/inscritos.json na porta {port} difere do golden"
        responses[(port, "/inscritos.json")] = r

    # mesmos cabecalhos nas duas portas (inclui os CORS * / GET nos GET publicos de dados)
    for path in [f"/live{n}.json" for n in LIVE_FILES] + ["/inscritos.json"]:
        a, b = responses[(8080, path)], responses[(8081, path)]
        assert _headers_without_volatile(a) == _headers_without_volatile(b), path
        assert header(a, "Access-Control-Allow-Origin") == "*"
        assert header(a, "Access-Control-Allow-Methods") == "GET"
        assert header(a, "Content-Type").startswith("application/json")

    # /health e /contador existem nas duas portas, sem CORS
    for port in (8080, 8081):
        health = stack.on_port(port, "/health", headers={"Origin": "https://qualquer.example"})
        assert health["status"] == 200
        body = json.loads(health["body"])
        assert body["status"] == "ok" and "cookies_file_exists" not in body  # D8
        contador = stack.on_port(port, "/contador", headers={"Origin": "https://qualquer.example"})
        assert contador["status"] == 200 and header(contador, "Content-Type").startswith("text/html")
        for r in (health, contador):
            assert not [k for k, _ in r["headers"] if k.lower().startswith("access-control-allow")]

    # erros legados tambem sao identicos aos golden files
    for port in (8080, 8081):
        bad = stack.on_port(port, "/live5.json")
        assert bad["status"] == 404 or bad["status"] == 422
        bad_api = stack.on_port(port, "/api/public/lives/5")
        assert _bytes(bad_api) == (GOLDEN / "error_slot_invalido.json").read_bytes()
        assert bad_api["status"] == 404
    for (port, path), r in stack.fresh.items():
        if path in ("/inscritos.json", "/api/public/subscribers"):
            assert r["status"] == 503
            assert _bytes(r) == (GOLDEN / "error_inscritos_indisponiveis.json").read_bytes()


class _Legacy(BaseHTTPRequestHandler):
    """Dublê do 'app antigo' para testar o SCRIPT de comparacao: serve o que o teste mandar, so por GET."""

    routes: dict[str, bytes] = {}
    seen: list[tuple[str, str]] = []

    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        type(self).seen.append(("GET", self.path))
        body = type(self).routes.get(self.path)
        status = 200 if body is not None else 404
        body = body if body is not None else b'{"detail":"nao"}'
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        type(self).seen.append(("POST", self.path))
        self.send_response(405)
        self.end_headers()


def _serve(routes: dict[str, bytes]):
    handler = type("H", (_Legacy,), {"routes": routes, "seen": []})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, handler


def _run_script(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, timeout=120, encoding="utf-8"
    )


def _golden_routes() -> dict[str, bytes]:
    routes = {f"/live{n}.json": (GOLDEN / name).read_bytes() for n, name in LIVE_FILES.items()}
    routes["/inscritos.json"] = (GOLDEN / "inscritos.json").read_bytes()
    return routes


def test_int_04_comparacao_com_os_apps_atuais_campo_a_campo(stack, seeded):
    # (a) "apps atuais" que respondem exatamente os golden files x pilha nova semeada igual => sem divergencia
    server, handler = _serve(_golden_routes())
    try:
        legacy_url = f"http://127.0.0.1:{server.server_address[1]}"
        ok = _run_script("--legacy-url", legacy_url, "--new-url", "http://127.0.0.1:8080", "--values")
        assert ok.returncode == 0, ok.stdout + ok.stderr
        assert "OK" in ok.stdout
        assert {m for m, _ in handler.seen} == {"GET"}, "o script so pode usar GET"
        assert sorted(p for _, p in handler.seen) == sorted(_golden_routes())
    finally:
        server.shutdown()

    # (b) legado com a ORDEM das chaves trocada, um campo a menos, um tipo diferente e um valor diferente
    routes = _golden_routes()
    live1 = json.loads(routes["/live1.json"].decode("utf-8"))[0]
    reordered = {"slot": live1["slot"], "ativo": live1["ativo"], **{k: v for k, v in live1.items() if k not in ("slot", "ativo")}}
    routes["/live1.json"] = json.dumps([reordered], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    inscritos = json.loads(routes["/inscritos.json"].decode("utf-8"))
    del inscritos["raw"]
    routes["/inscritos.json"] = json.dumps(inscritos, separators=(",", ":")).encode("utf-8")
    live2 = json.loads(routes["/live2.json"].decode("utf-8"))[0]
    live2["views"] = str(live2["views"])  # tipo diferente (string em vez de numero)
    routes["/live2.json"] = json.dumps([live2], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    live3 = json.loads(routes["/live3.json"].decode("utf-8"))[0]
    live3["titulo"] = "outro titulo"
    routes["/live3.json"] = json.dumps([live3], ensure_ascii=False, separators=(",", ":")).encode("utf-8")

    server, handler = _serve(routes)
    try:
        legacy_url = f"http://127.0.0.1:{server.server_address[1]}"
        bad = _run_script("--legacy-url", legacy_url, "--new-url", "http://127.0.0.1:8080", "--values")
        assert bad.returncode == 1, bad.stdout + bad.stderr
        assert "DIVERGE /live1.json [ordem das chaves]" in bad.stdout
        assert "DIVERGE /inscritos.json [campo 'raw']" in bad.stdout
        assert "DIVERGE /live2.json [tipo de 'views']" in bad.stdout
        assert "DIVERGE /live3.json [valor de 'titulo']" in bad.stdout
        assert "DIVERGE /live4.json" not in bad.stdout
        assert {m for m, _ in handler.seen} == {"GET"}
    finally:
        server.shutdown()

    # (c) sem --values, valores diferentes nao contam, mas a estrutura continua sendo comparada
    server, _ = _serve(routes)
    try:
        legacy_url = f"http://127.0.0.1:{server.server_address[1]}"
        structure = _run_script("--legacy-url", legacy_url, "--new-url", "http://127.0.0.1:8080")
        assert structure.returncode == 1
        assert "valor de 'titulo'" not in structure.stdout
        assert "ordem das chaves" in structure.stdout
    finally:
        server.shutdown()

    # (d) URL sem resposta => erro explicito (codigo 2), nunca "tudo igual"
    dead = _run_script("--legacy-url", "http://127.0.0.1:9", "--new-url", "http://127.0.0.1:8080", "--timeout", "3")
    assert dead.returncode == 2 and "ERRO" in dead.stderr
