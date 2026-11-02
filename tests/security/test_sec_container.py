"""SEC-07, 08, 09, 11, 12, 13, 14, 15, 18, 20, 21: endurecimento, segredos e isolamento dos containers REAIS."""

from __future__ import annotations

import json
import re
import socket
import subprocess
import threading
import time
import warnings
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from e2e_stack import (
    ALL_CONTAINERS,
    CONTAINERS,
    FAKE_PORT,
    IMAGES,
    docker,
    header,
    http_request,
    wait_until,
)
from sec_support import JSON_HEADERS

pytestmark = [pytest.mark.e2e, pytest.mark.docker]

# Procura bytes em todos os arquivos de varios diretorios do container (sentinelas chegam por stdin, nunca por argv).
SCAN = r"""
import json, os, sys
needles = [n.encode() for n in json.load(sys.stdin)]
roots = sys.argv[1:]
hits = []
for root in roots:
    for base, dirs, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            try:
                data = open(p, "rb").read()
            except OSError:
                continue
            for n in needles:
                if n in data:
                    hits.append(p)
print(json.dumps(sorted(set(hits))))
"""

ENVIRON_SCAN = r"""
import json, glob, sys
needles = [n.encode() for n in json.load(sys.stdin)]
hits = []
for p in glob.glob("/proc/[0-9]*/environ"):
    try:
        data = open(p, "rb").read()
    except OSError:
        continue
    hits += [p for n in needles if n in data]
print(json.dumps(hits))
"""


# ---------------------------------------------------------------------------------------------------- SEC-07
def test_sec_07_nenhum_container_roda_como_root_com_rootfs_somente_leitura_e_sem_capabilities(stack):
    for name, container in CONTAINERS.items():
        info = stack.inspect(name)
        user = info["Config"]["User"]
        assert re.fullmatch(r"\d+(:\d+)?", user), (name, user)
        assert int(user.split(":")[0]) != 0, (name, user)
        host = info["HostConfig"]
        assert host["ReadonlyRootfs"] is True, name
        assert host["CapDrop"] == ["ALL"], (name, host["CapDrop"])
        assert not host.get("CapAdd"), (name, host.get("CapAdd"))
        assert any(o.startswith("no-new-privileges") and not o.endswith("false") for o in host["SecurityOpt"] or []), name
        assert host["Privileged"] is False, name
        assert host["NetworkMode"] != "host" and host["PidMode"] in ("", "private"), name
        assert not any("docker.sock" in b for b in host.get("Binds") or []), name
        uid = docker("exec", container, "id", "-u").stdout.strip()
        assert uid != "0" and uid == user.split(":")[0], (name, uid)
        # rootfs de fato somente leitura
        probe = docker("exec", container, "sh", "-c", "touch /rootfs-probe 2>&1; echo rc=$?", check=False)
        assert "rc=0" not in probe.stdout, f"{name}: conseguiu escrever na raiz"


# ---------------------------------------------------------------------------------------------------- SEC-08 / 13
def _sentinels(stack) -> list[str]:
    return [stack.api_key]


def _export_contains(container: str, needle: bytes) -> bool:
    proc = subprocess.Popen(["docker", "export", container], stdout=subprocess.PIPE)
    tail = b""
    found = False
    assert proc.stdout is not None
    while chunk := proc.stdout.read(1 << 20):
        if needle in tail + chunk:
            found = True
            break
        tail = chunk[-len(needle):]
    proc.kill()
    proc.wait()
    return found


def test_sec_08_a_chave_da_api_so_existe_no_secret_do_worker_lives(stack):
    stack.ensure_lives()  # a chave esta em uso (o YouTube falso so responde com ela)
    stack.ensure_subs("460600")
    key = stack.api_key
    # presente SO em /run/secrets/youtube_api_key do worker-lives
    assert stack.exec("worker-lives", "cat", "/run/secrets/youtube_api_key").stdout.strip() == key
    for name in ("api", "web", "worker-inscritos"):
        listing = stack.exec(name, "sh", "-c", "ls /run/secrets 2>/dev/null; true").stdout.split()
        assert "youtube_api_key" not in listing, name

    # docker history --no-trunc de todas as imagens
    for name, image in IMAGES.items():
        assert key not in docker("history", "--no-trunc", image).stdout, f"imagem {name}"
    # docker inspect (env, mounts, tudo) de todos os containers, inclusive os YouTubes falsos
    for name in ALL_CONTAINERS:
        assert key not in json.dumps(stack.inspect(name)), f"inspect {name}"
    # logs de todos os containers (e os sentinelas de cookie tambem nao podem vazar)
    cookie_secrets = [stack.auth_cookie["value"], stack.extra_cookie["value"]]
    for name in ALL_CONTAINERS:
        text = stack.logs(name)
        assert key not in text, f"log de {name}"
        for secret in cookie_secrets:
            assert secret not in text, f"valor de cookie no log de {name}"
    # respostas HTTP (publicas e de admin) de todas as rotas
    bodies = []
    for port in (8080, 8081):
        for path in ("/health", "/live1.json", "/live2.json", "/live3.json", "/live4.json", "/inscritos.json", "/api/public/lives/1", "/api/public/subscribers", "/contador"):
            r = stack.on_port(port, path)
            bodies.append(json.dumps(r["headers"]) + r["body"])
    for path in ("/api/admin/session", "/api/admin/slots", "/api/admin/workers/status"):
        r = stack.api("GET", path)
        bodies.append(json.dumps(r["headers"]) + r["body"])
    joined = "\n".join(bodies)
    for secret in [key, *cookie_secrets]:
        assert secret not in joined

    # sistema de arquivos do container api (camadas + volumes + tmpfs) e do web, e ambiente de todos os processos
    needle = key.encode()
    assert not _export_contains(CONTAINERS["api"], needle)
    assert not _export_contains(CONTAINERS["web"], needle)
    roots = ["/data", "/admin-state", "/tmp", "/run", "/app", "/home"]
    assert json.loads(stack.py("api", SCAN, *roots, input_text=json.dumps([key])).stdout) == []
    web_hits = stack.exec("web", "sh", "-c", f"grep -rlF -e '{key}' /tmp /etc/nginx /var/cache/nginx /run /usr/share/nginx 2>/dev/null; true").stdout
    assert web_hits.strip() == ""
    for name in ("api", "worker-inscritos", "worker-lives"):
        assert json.loads(stack.py(name, ENVIRON_SCAN, input_text=json.dumps([key])).stdout) == [], f"environ de {name}"
    environ_web = stack.exec("web", "sh", "-c", "cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n'; true").stdout
    assert key not in environ_web


def test_sec_13_segredos_nao_estao_no_ambiente(stack):
    values = [stack.api_key, stack.pwd_hash]
    pub = (stack.secrets_dir / "cookie_public_key.txt").read_text(encoding="ascii").strip()
    values.append(pub)
    private = stack.exec("worker-inscritos", "cat", "/run/secrets/cookie_private_key").stdout.strip()
    values.append(private)
    for name in CONTAINERS:
        env = stack.inspect(name)["Config"]["Env"] or []
        blob = "\n".join(env)
        for v in values:
            assert v not in blob, f"segredo no Config.Env de {name}"
        for entry in env:
            var, _, value = entry.partition("=")
            assert var not in ("YOUTUBE_API_KEY", "COOKIE_PRIVATE_KEY", "COOKIE_PUBLIC_KEY", "ADMIN_PASSWORD_HASH", "ADMIN_PASSWORD"), (name, var)
            if re.search(r"(KEY|PASSWORD|TOKEN|SECRET|HASH)$", var) and var != "GPG_KEY":
                assert value == "" or value.startswith("/run/secrets/") or var.endswith("_FILE"), (name, var)


# ---------------------------------------------------------------------------------------------------- SEC-09 / 14
FIND_PRIVATE = r"""
import os
skip = ("/proc", "/sys", "/dev")
print([os.path.join(b, f) for b, d, fs in os.walk("/") if not b.startswith(skip) for f in fs if f.startswith("cookie_private_key")])
"""

DECRYPT_TRY = r"""
import base64, glob, hashlib, json, sqlite3
from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, SealedBox
con = sqlite3.connect("file:/data/livestats.db?mode=ro", uri=True)
ct = con.execute("SELECT ciphertext FROM cookie_sets ORDER BY id DESC LIMIT 1").fetchone()[0]
cands = {}
for path in glob.glob("/run/secrets/*"):
    raw = open(path, "rb").read().strip()
    cands[path + ":raw"] = raw
    cands[path + ":sha256"] = hashlib.sha256(raw).digest()
    try:
        cands[path + ":b64"] = base64.b64decode(raw, validate=True)
    except Exception:
        pass
tried, outcome = 0, {}
for label, key in cands.items():
    if len(key) != 32:
        continue
    tried += 1
    try:
        SealedBox(PrivateKey(key)).decrypt(ct)
        outcome[label] = "DECRYPTED"
    except CryptoError:
        outcome[label] = "failed"
print(json.dumps({"len": len(ct), "tried": tried, "outcome": outcome, "secrets": sorted(glob.glob("/run/secrets/*"))}))
"""

DECRYPT_OK = r"""
import base64, json, sqlite3
from nacl.public import PrivateKey, SealedBox
priv = PrivateKey(base64.b64decode(open("/run/secrets/cookie_private_key", "rb").read().strip(), validate=True))
con = sqlite3.connect("file:/data/livestats.db?mode=ro", uri=True)
ct = con.execute("SELECT ciphertext FROM cookie_sets ORDER BY id DESC LIMIT 1").fetchone()[0]
print(SealedBox(priv).decrypt(ct).decode())
"""


def test_sec_09_a_api_nao_consegue_decifrar_os_cookies(stack):
    stack.ensure_subs("460600")
    api_secrets = {p.rsplit("/", 1)[1] for p in stack.exec("api", "sh", "-c", "ls -d /run/secrets/*").stdout.split()}
    assert api_secrets == {"cookie_public_key", "admin_password_hash"}
    for name in ("worker-lives", "api"):
        found = stack.py(name, FIND_PRIVATE).stdout
        assert found.strip() == "[]", (name, found)
    out = json.loads(stack.py("api", DECRYPT_TRY).stdout)
    assert out["len"] > 48 and out["tried"] >= 3, out
    assert set(out["outcome"].values()) == {"failed"}, out
    # controle: o MESMO ciphertext decifra no worker-inscritos (tem a chave privada)
    plain = json.loads(stack.py("worker-inscritos", DECRYPT_OK).stdout)
    assert {c["name"] for c in plain} == {stack.auth_cookie["name"], stack.extra_cookie["name"]}


FILE_SCAN_NAMES = r"""
import json, os, sys
needles = [n.encode() for n in json.load(sys.stdin)]
res = {}
for root in sys.argv[1:]:
    for base, _, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            data = open(p, "rb").read()
            res[p] = [n.decode() for n in needles if n in data]
print(json.dumps(res))
"""


def test_sec_14_cookies_cifrados_no_disco(stack):
    stack.ensure_subs("460600")
    needles = [stack.auth_cookie["name"], stack.auth_cookie["value"], stack.extra_cookie["name"], stack.extra_cookie["value"]]
    res = json.loads(stack.py("api", FILE_SCAN_NAMES, "/data", "/admin-state", input_text=json.dumps(needles)).stdout)
    assert any(p.endswith("livestats.db") for p in res), res.keys()
    assert all(v == [] for v in res.values()), {p: v for p, v in res.items() if v}
    # e o ciphertext decifra de verdade no worker-inscritos (o Studio falso recebeu os cookies: INT-02)
    plain = json.loads(stack.py("worker-inscritos", DECRYPT_OK).stdout)
    assert {c["value"] for c in plain} == {stack.auth_cookie["value"], stack.extra_cookie["value"]}
    # nem as respostas da api devolvem os cookies
    assert stack.auth_cookie["value"] not in stack.api("GET", "/api/admin/workers/status")["body"]


# ---------------------------------------------------------------------------------------------------- SEC-15
SESSION_SCAN = r"""
import hashlib, json, os, sys
sid = sys.stdin.read().strip()
digest = hashlib.sha256(sid.encode()).hexdigest()
out = {"sid": {}, "digest": {}}
for root in ("/admin-state", "/data"):
    for base, _, files in os.walk(root):
        for f in files:
            p = os.path.join(base, f)
            data = open(p, "rb").read()
            out["sid"][p] = sid.encode() in data
            out["digest"][p] = digest.encode() in data
print(json.dumps(out))
"""


def test_sec_15_id_da_sessao_nao_fica_em_claro_no_disco(stack):
    sess = stack.login()
    out = json.loads(stack.py("api", SESSION_SCAN, input_text=sess.cookie).stdout)
    assert out["sid"], "nenhum arquivo varrido"
    assert not any(out["sid"].values()), [p for p, v in out["sid"].items() if v]
    admin_hit = [p for p, v in out["digest"].items() if v and p.startswith("/admin-state/admin.db")]
    assert admin_hit, "o SHA-256 do id deveria estar em admin.db"
    assert not [p for p, v in out["digest"].items() if v and p.startswith("/data/")]
    assert stack.admin("/api/admin/session", "GET", {"Cookie": sess.cookie_header})["status"] == 200


# ---------------------------------------------------------------------------------------------------- SEC-18 / 20
PERMS = r"""
import json, os, stat
def mode(p): return stat.S_IMODE(os.stat(p).st_mode)
out = {"data": oct(mode("/data")), "files": {}}
for f in os.listdir("/data"):
    out["files"][f] = oct(mode("/data/" + f))
try:
    out["admin"] = oct(mode("/admin-state")); out["admin_uid"] = os.stat("/admin-state").st_uid
except OSError:
    out["admin"] = None
print(json.dumps(out))
"""

WRITE_DATA = r"""
import os, sys
p = "/data/.t8-write-test-" + sys.argv[1]
open(p, "w").write("x"); assert open(p).read() == "x"; os.remove(p)
try:
    os.listdir("/admin-state"); print("ADMIN-VISIVEL")
except OSError:
    print("ok")
"""


def test_sec_18_permissoes_dos_volumes(stack):
    out = json.loads(stack.py("api", PERMS).stdout)
    assert out["data"] == "0o2770", out
    assert out["files"], out
    for f, mode in out["files"].items():
        assert int(mode, 8) & 0o007 == 0, (f, mode)
    assert out["admin"] == "0o700" and out["admin_uid"] == 10001, out
    for name in ("api", "worker-lives", "worker-inscritos"):
        res = stack.py(name, WRITE_DATA, name).stdout.strip()
        assert res == ("ok" if name != "api" else "ADMIN-VISIVEL"), (name, res)
    for name in ("worker-lives", "worker-inscritos"):
        assert stack.py(name, "import os; print(os.path.exists('/admin-state'))").stdout.strip() == "False"


FORGE = r"""
import hashlib, sqlite3, sys, time
forged = sys.argv[1]
con = sqlite3.connect("/data/livestats.db", timeout=10)
con.execute("CREATE TABLE IF NOT EXISTS admin_sessions (id_hash TEXT PRIMARY KEY, csrf_token TEXT, username TEXT, client_ip TEXT, created_at INTEGER, last_seen_at INTEGER, expires_at INTEGER)")
now = int(time.time())
con.execute("INSERT OR REPLACE INTO admin_sessions VALUES (?,?,?,?,?,?,?)",
            (hashlib.sha256(forged.encode()).hexdigest(), "c" * 43, "t8admin", "", now, now, now + 3600))
con.commit()
print("forjado")
"""


def test_sec_20_estado_de_admin_isolado_dos_workers(stack):
    for name in ("worker-lives", "worker-inscritos"):
        mounts = {m["Name"] for m in stack.inspect(name)["Mounts"] if m.get("Name")}
        assert "livestats-t8-admin" not in mounts, name
        found = stack.py(name, "import os\nprint([os.path.join(b, f) for b, _, fs in os.walk('/') for f in fs if f.startswith('admin.db') and not b.startswith(('/proc', '/sys'))])").stdout
        assert found.strip() == "[]", (name, found)
    admin_holders = [n for n in CONTAINERS if "livestats-t8-admin" in {m.get("Name") for m in stack.inspect(n)["Mounts"]}]
    assert admin_holders == ["api"]

    # "worker comprometido": escreve uma sessao forjada (hash escolhido por ele) no banco compartilhado
    forged = "sessao-forjada-" + "z" * 30
    stack.py("worker-lives", FORGE, forged)
    try:
        h = {"Cookie": f"__Host-livestats_session={forged}", "X-CSRF-Token": "c" * 43, **JSON_HEADERS}
        assert stack.admin("/api/admin/session", "GET", h)["status"] == 401
        assert stack.admin("/api/admin/slots", "GET", h)["status"] == 401
        assert stack.admin("/api/admin/slots", "PUT", h, json.dumps({"slots": ["", "", "", ""]}))["status"] == 401
    finally:
        stack.py("worker-lives", "import sqlite3; c=sqlite3.connect('/data/livestats.db'); c.execute('DROP TABLE IF EXISTS admin_sessions'); c.commit()")


# ---------------------------------------------------------------------------------------------------- SEC-11
class _Ok(BaseHTTPRequestHandler):
    def log_message(self, *args):
        return

    def do_GET(self):  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")


PROBE = r"""
import sys, urllib.request
try:
    print("REACHED:" + urllib.request.urlopen(sys.argv[1], timeout=5).read().decode())
except Exception as e:
    print("BLOCKED:" + type(e).__name__)
"""
DNS = r"""
import socket, sys
try:
    socket.getaddrinfo(sys.argv[1], 80); print("RESOLVED")
except OSError:
    print("NOT-RESOLVED")
"""


def _network_ip(stack, name: str, network: str) -> str:
    return stack.inspect(name)["NetworkSettings"]["Networks"][network]["IPAddress"]


def test_sec_11_isolamento_de_rede(stack):
    server = ThreadingHTTPServer(("0.0.0.0", 0), _Ok)  # noqa: S104 - "internet" do teste: servidor no host, fora do compose
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    try:
        gateway = json.loads(docker("network", "inspect", "livestats-t8-egress").stdout)[0]["IPAM"]["Config"][0]["Gateway"]
        targets = [f"http://host.docker.internal:{port}/", f"http://{gateway}:{port}/"]

        def reached(name: str) -> bool:
            return any(stack.py(name, PROBE, t).stdout.startswith("REACHED:ok") for t in targets)

        # workers: saida alcanca o servidor externo ao compose
        assert reached("worker-lives") and reached("worker-inscritos")
        # api (rede internal: true): nem esse servidor, nem IP publico, nem DNS externo
        assert not reached("api")
        assert stack.py("api", PROBE, "https://1.1.1.1/").stdout.startswith("BLOCKED")
        assert stack.py("api", DNS, "example.com").stdout.strip() == "NOT-RESOLVED"
        assert stack.py("api", PROBE, "http://127.0.0.1:8000/health").stdout.startswith("REACHED:")  # controle da sonda
        # web: sem saida para a internet. A garantia vem de `enable_ip_masquerade=false` na rede edge (e de o web nao
        # estar na egress). Config SEMPRE verificada; o teste vivo so e conclusivo se o motor Docker respeita a opcao
        # (controle: uma rede descartavel com a MESMA opcao). Docker Desktop (NAT da VM) nao respeita: ver relatorio T8.
        edge_opts = json.loads(docker("network", "inspect", "livestats-t8-edge").stdout)[0]["Options"]
        assert edge_opts.get("com.docker.network.bridge.enable_ip_masquerade") == "false"
        assert set(stack.inspect("web")["NetworkSettings"]["Networks"]) == {"livestats-t8-edge", "livestats-t8-internal"}

        def web_reaches(url: str) -> bool:
            r = stack.exec("web", "sh", "-c", f"wget -q -T 4 -O /dev/null {url} && echo REACHED || echo BLOCKED", check=False).stdout
            return "REACHED" in r

        docker("network", "rm", "t8-probe-masq", check=False)
        docker("network", "create", "-o", "com.docker.network.bridge.enable_ip_masquerade=false", "--label", "livestats-t8-extra=1", "t8-probe-masq")
        try:
            control = docker(
                "run", "--rm", "--network", "t8-probe-masq", "python:3.12-slim", "python", "-c", PROBE, "http://1.1.1.1/"
            ).stdout
        finally:
            docker("network", "rm", "t8-probe-masq", check=False)
        if control.startswith("BLOCKED"):
            for url in ("http://1.1.1.1/", "http://8.8.8.8/"):
                assert not web_reaches(url), f"web alcancou a internet ({url})"
        else:
            warnings.warn(
                "motor Docker ignora enable_ip_masquerade=false (ex.: Docker Desktop): saida do web nao verificavel ao vivo",
                stacklevel=1,
            )

        # workers nao alcancam api/web: nem por nome nem por IP
        api_ip = _network_ip(stack, "api", "livestats-t8-internal")
        web_ips = [ip["IPAddress"] for ip in stack.inspect("web")["NetworkSettings"]["Networks"].values()]
        for name in ("worker-lives", "worker-inscritos"):
            for host in ("api", "web", "t8-api", "t8-web"):
                assert stack.py(name, DNS, host).stdout.strip() == "NOT-RESOLVED", (name, host)
            assert stack.py(name, PROBE, f"http://{api_ip}:8000/health").stdout.startswith("BLOCKED"), name
            for ip in web_ips:
                assert stack.py(name, PROBE, f"http://{ip}:8080/health").stdout.startswith("BLOCKED"), (name, ip)
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------------------------------- SEC-12
LISTEN = r"""
import glob, json, os
listen = {}
for f in ("/proc/net/tcp", "/proc/net/tcp6"):
    for line in open(f).read().splitlines()[1:]:
        cols = line.split()
        if cols[3] == "0A":
            addr, port = cols[1].rsplit(":", 1)
            listen[cols[9]] = [addr, int(port, 16)]
owned = []
for fd in glob.glob("/proc/[0-9]*/fd/*"):
    try:
        target = os.readlink(fd)
    except OSError:
        continue
    if target.startswith("socket:[") and target[8:-1] in listen:
        owned.append(listen[target[8:-1]])
cmds = []
for p in glob.glob("/proc/[0-9]*/cmdline"):
    try:
        cmds.append(open(p, "rb").read().replace(b"\0", b" ").decode(errors="replace"))
    except OSError:
        pass
print(json.dumps({"listen": sorted(listen.values()), "owned_by_worker": owned, "cmds": cmds}))
"""


def test_sec_12_sem_porta_de_debug_e_sem_portas_extras(stack):
    stack.ensure_subs("460600")  # garante o Chromium em execucao
    with socket.socket() as s:
        s.settimeout(2)
        assert s.connect_ex(("127.0.0.1", 9222)) != 0
    for name in CONTAINERS:
        ports = docker("port", CONTAINERS[name]).stdout.split()
        mapped = sorted({p for p in ports if re.fullmatch(r"\d+/tcp", p)})
        assert mapped == (["8080/tcp", "8081/tcp"] if name == "web" else []), (name, ports)
    out = json.loads(stack.py("worker-inscritos", LISTEN).stdout)
    # nenhum socket em escuta pertence a processos do worker. O namespace de rede e compartilhado com o YouTube falso
    # de teste (porta dele) e tem o DNS embutido do Docker (127.0.0.11); mais nada pode estar em escuta.
    assert out["owned_by_worker"] == [], out["owned_by_worker"]
    assert all(
        (addr == "0100007F" and port == FAKE_PORT) or addr == "0B00007F" for addr, port in out["listen"]
    ), out["listen"]
    chromium = [c for c in out["cmds"] if "chrom" in c.lower() and "--type" not in c]
    assert chromium, "o Chromium do worker deveria estar rodando"
    assert any("--remote-debugging-pipe" in c for c in chromium)
    assert not any("remote-debugging-port" in c for c in out["cmds"])
    assert stack.py("worker-inscritos", "import socket; s=socket.socket(); s.settimeout(2); print(s.connect_ex(('127.0.0.1', 9222)))").stdout.strip() != "0"


# ---------------------------------------------------------------------------------------------------- SEC-21
def _refused(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(3)
        return s.connect_ex((host, port)) != 0


def test_sec_21_admin_bind_seguro_por_padrao(stack):
    ports = docker("port", CONTAINERS["web"]).stdout
    assert re.search(r"8081/tcp -> 127\.0\.0\.1:8081", ports), ports
    assert re.search(r"8080/tcp -> (0\.0\.0\.0|\[::\]):8080", ports), ports
    # 127.0.0.2 e outro endereco de loopback (faz o papel de "IP da LAN"): 8081 recusa, 8080 responde
    assert _refused("127.0.0.2", 8081)
    assert http_request("127.0.0.2", 8080, "/health")["status"] == 200
    # com ADMIN_BIND=<IP>, a 8081 responde nesse IP e deixa de responder no 127.0.0.1
    stack.recreate_web(ADMIN_BIND="127.0.0.2")
    try:
        wait_until(lambda: http_request("127.0.0.2", 8081, "/api/admin/session")["status"] == 401, timeout=20)
        assert _refused("127.0.0.1", 8081)
        assert "127.0.0.2:8081" in docker("port", CONTAINERS["web"]).stdout
        assert http_request("127.0.0.1", 8080, "/health")["status"] == 200
    finally:
        stack.recreate_web(ADMIN_BIND=None)
    wait_until(lambda: http_request("127.0.0.1", 8081, "/api/admin/session")["status"] == 401, timeout=20)
    assert _refused("127.0.0.2", 8081)
    assert header(http_request("127.0.0.1", 8080, "/health"), "Content-Type")
