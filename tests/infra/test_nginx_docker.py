"""INFRA-NG-* em container real (marcador `docker`).

Sobe o `web` do docker-compose.yml real (imagem do web/Dockerfile real) contra o upstream de teste
tests/infra/fixtures/fake_api.py em api:8000. Verifica o que o Nginx faz de fato: rotas, cabecalhos, CORS,
limit_req, IP real, metodos, limites de corpo e recuperacao quando a api e recriada.
"""

from __future__ import annotations

import json
import re

import pytest

import docker_helpers as dh
from docker_helpers import header, header_values

pytestmark = pytest.mark.docker

CSP_STRICT = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
CSP_OVERLAY = CSP_STRICT.replace("frame-ancestors 'none'", "frame-ancestors *")
BODY_429 = '{"detail": "Muitas tentativas. Tente novamente em instantes."}'
DATA_ROUTES = ["/live1.json", "/live4.json", "/inscritos.json", "/api/public/lives/1", "/api/public/subscribers"]
IMAGE = "livestats-t7test/web:test"


@pytest.fixture(scope="module")
def web_image():
    dh.require_docker()
    dh.build_web_image(IMAGE)
    yield IMAGE
    dh.docker("rmi", "-f", IMAGE, check=False)


@pytest.fixture(scope="module")
def stack_default(web_image):
    """TRUSTED_PROXY_CIDR e ADMIN_TRUSTED_PROXY_IP vazios (o padrao)."""
    stack = dh.Stack(web_image).up()
    yield stack
    stack.down()


@pytest.fixture(scope="module")
def stack_trusted(web_image):
    """cloudflared de teste = .10 (8080); Nginx Proxy Manager de teste = .11 (8081)."""
    stack = dh.Stack(web_image, trusted_octet=10, admin_octet=11).up()
    yield stack
    stack.down()


def entry(stack, path):
    matches = [e for e in stack.api_log() if e["path"] == path]
    assert len(matches) == 1, f"{path}: {len(matches)} requisicoes na api"
    return matches[0]


def statuses(results):
    return [r["status"] for r in results]


def no_cors(resp):
    bad = [k for k, _ in resp["headers"] if k.lower().startswith("access-control-")]
    assert not bad, f"CORS indevido: {bad}"


# ---------------------------------------------------------------------------------------------- imagem
def without_comments(conf):
    return "\n".join(line for line in conf.splitlines() if not line.lstrip().startswith("#"))


def run_nginx(image, env, *cmd):
    args = ["run", "--rm"]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    return dh.docker(*args, image, "nginx", *cmd, check=False)


def test_infra_ng_07_nginx_t_com_configuracao_renderizada(web_image):
    dh.require_docker()
    r = run_nginx(web_image, {"TRUSTED_PROXY_CIDR": "172.29.80.10/32", "ADMIN_TRUSTED_PROXY_IP": "172.29.80.11"}, "-t")
    assert r.returncode == 0, r.stderr
    assert "syntax is ok" in r.stderr and "test is successful" in r.stderr


def test_infra_ng_04_09_valores_validos_geram_as_diretivas(web_image):
    dh.require_docker()
    out = run_nginx(web_image, {"TRUSTED_PROXY_CIDR": " 172.29.80.10/32 ", "ADMIN_TRUSTED_PROXY_IP": "172.29.80.11"}, "-T")
    assert out.returncode == 0, out.stderr
    conf = without_comments(out.stdout)
    assert "real_ip_header CF-Connecting-IP; real_ip_recursive off; set_real_ip_from 172.29.80.10/32;" in conf
    assert "real_ip_header X-Forwarded-For; real_ip_recursive off; set_real_ip_from 172.29.80.11/32;" in conf
    assert conf.count("set_real_ip_from") == 2
    # cada diretiva no server certo
    first, second = conf.split("listen 8081", 1)
    assert "CF-Connecting-IP; real_ip_recursive" in first and "X-Forwarded-For; real_ip_recursive" not in first
    assert "X-Forwarded-For; real_ip_recursive" in second and "CF-Connecting-IP; real_ip_recursive" not in second


@pytest.mark.parametrize("env", [{}, {"TRUSTED_PROXY_CIDR": "", "ADMIN_TRUSTED_PROXY_IP": ""}, {"TRUSTED_PROXY_CIDR": "   "}])
def test_infra_ng_04_09_vazio_nao_emite_diretiva_real_ip(web_image, env):
    dh.require_docker()
    out = run_nginx(web_image, env, "-T")
    assert out.returncode == 0, out.stderr
    for forbidden in ("set_real_ip_from", "real_ip_header", "real_ip_recursive"):
        assert forbidden not in without_comments(out.stdout)


BAD_VALUES = [
    "172.29.80.0/24",      # sub-rede
    "0.0.0.0/0",           # todo mundo
    "0.0.0.0",
    "10.0.0.0/8",
    "172.29.80.10/31",     # mascara diferente de /32
    "172.29.80.10/0",
    "172.29.80.1",         # gateway da bridge
    "172.29.80.1/32",
    "172.29.80.0",
    "172.29.80.255",
    "127.0.0.1",
    "::1",                 # IPv6
    "2001:db8::1/128",
    "abc",
    "172.29.80",
    "172.29.80.10.5",
    "172.29.80.256",
    "172.29.080.10",       # zero a esquerda
    "172.29.80.10; allow all",   # injecao de diretiva
    "172.29.80.10/32 10.0.0.0/8",
    "172.29.80.10,172.29.80.11",
    "$remote_addr",
]


@pytest.mark.parametrize("variable", ["TRUSTED_PROXY_CIDR", "ADMIN_TRUSTED_PROXY_IP"])
@pytest.mark.parametrize("value", BAD_VALUES)
def test_infra_ng_04_09_valor_perigoso_aborta_a_subida(web_image, variable, value):
    dh.require_docker()
    r = run_nginx(web_image, {variable: value}, "-t")
    assert r.returncode != 0, f"{variable}={value!r} deveria abortar"
    assert "15-livestats-realip: ERRO" in r.stderr
    assert variable in r.stderr
    assert "syntax is ok" not in r.stderr


# ---------------------------------------------------------------------------------------------- compose real
def test_infra_cfg_02_runtime_servico_web_endurecido(stack_default):
    cid = stack_default.compose("ps", "-q", "web").stdout.strip()
    info = json.loads(dh.docker("inspect", cid).stdout)[0]
    assert info["Config"]["User"] == "101:101"
    assert info["HostConfig"]["ReadonlyRootfs"] is True
    assert info["HostConfig"]["CapDrop"] == ["ALL"] and not info["HostConfig"]["CapAdd"]
    assert "no-new-privileges:true" in info["HostConfig"]["SecurityOpt"]
    assert info["State"]["Health"]["Status"] == "healthy"
    assert not info["Config"]["Env"] or not any(re.search(r"SECRET|PASSWORD|TOKEN", e) for e in info["Config"]["Env"])


# ---------------------------------------------------------------------------------------------- rotas
ADMIN_ON_PUBLIC = [
    "/", "/admin", "/admin/", "/admin-assets/index.html", "/admin-assets/js/main.js", "/admin-assets/",
    "/api/admin", "/api/admin/", "/api/admin/session", "/api/admin/slots", "/api/admin/cookies",
    "/api/admin/workers/status", "/api/admin/login", "/api/admin/login/", "/api/admin/nao-existe",
    "//api/admin/session", "/api//admin/session", "/api/%61dmin/session", "/%61pi/admin/session",
    "/api/public/../admin/session", "/api/public/..%2fadmin%2fsession", "/api/public/%2e%2e/admin/session",
    "/api/public/lives/1/../../admin/session", "/api/public/lives/1/%2e%2e/%2e%2e/admin/session", "/API/ADMIN/session",
]


def test_infra_ng_01_8080_nao_tem_admin_e_a_api_nao_e_alcancada(stack_default):
    results = stack_default.client("20", [{"path": p} for p in ADMIN_ON_PUBLIC] +
                                   [{"path": "/api/admin/login", "method": "POST", "body": "{}"},
                                    {"path": "/api/admin/slots", "method": "PUT", "body": "{}"},
                                    {"path": "/api/admin/cookies", "method": "POST", "body": "[]"}])
    for path, resp in zip(ADMIN_ON_PUBLIC, results):
        assert resp["status"] == 404, f"{path} -> {resp['status']}"
        assert json.loads(resp["body"]) == {"detail": "Not Found"}
    assert results[-3]["status"] == 404 and results[-2]["status"] == 404 and results[-1]["status"] == 404
    reached = [e["path"] for e in stack_default.api_log()]
    assert not [p for p in reached if "admin" in p.lower()], f"a api recebeu rota de admin pela 8080: {reached}"


def test_infra_ng_06_8080_so_encaminha_as_rotas_publicas(stack_default):
    forwarded = ["/health", "/api/public/lives/1", "/api/public/subscribers", "/live1.json", "/live9.json", "/inscritos.json"]
    refused = ["/foo", "/live.json", "/liveX.json", "/live1.json/", "/api/", "/api/public", "/inscritos", "/health/x",
               "/docs", "/openapi.json", "/redoc", "/__requests", "/overlay/", "/overlay/nao-existe.js"]
    res = stack_default.client("21", [{"path": f"{p}?r=6"} for p in forwarded + refused])
    for path, resp in zip(forwarded, res):
        assert resp["status"] == 200, path
    for path, resp in zip(refused, res[len(forwarded):]):
        assert resp["status"] == 404, f"{path} -> {resp['status']}"
    reached = {e["path"].split("?")[0] for e in stack_default.api_log() if e["path"].endswith("?r=6")}
    assert reached == set(forwarded)


def test_infra_ng_06_8081_encaminha_admin_e_serve_o_painel(stack_default):
    verbs = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    res = stack_default.client(
        "22",
        [{"path": f"/api/admin/x{n}?r=61", "method": v, "body": "{}" if v != "GET" else None} for n, v in enumerate(verbs)]
        + [{"path": "/"}, {"path": "/admin"}, {"path": "/admin/"}, {"path": "/admin-assets/js/main.js"},
           {"path": "/admin-assets/"}, {"path": "/admin-assets/js"}, {"path": "/admin/outra"}, {"path": "/api/admin"}],
        port=8081,
    )
    assert statuses(res[:5]) == [200] * 5
    index = (dh.STATIC_FIXTURE / "admin" / "index.html").read_text(encoding="utf-8")
    for resp in res[5:8]:
        assert resp["status"] == 200 and resp["body"] == index
        assert header(resp, "Content-Type").startswith("text/html")
    assert res[8]["status"] == 200 and res[8]["body"].startswith("// fixture admin script")
    assert statuses(res[9:]) == [404, 404, 404, 404]
    reached = [(e["method"], e["path"]) for e in stack_default.api_log() if e["path"].endswith("?r=61")]
    assert reached == [(v, f"/api/admin/x{n}?r=61") for n, v in enumerate(verbs)]


def test_infra_ng_06_overlays_nas_duas_portas(stack_default):
    html = (dh.STATIC_FIXTURE / "overlay" / "contador.html").read_text(encoding="utf-8")
    for port in (8080, 8081):
        res = stack_default.client(
            "23",
            [{"path": "/contador"}, {"path": "/overlay/inscritos"}, {"path": "/overlay/contador.html"},
             {"path": "/overlay/contador.js"}, {"path": "/overlay/contador.css"}, {"path": "/overlay/"},
             {"path": "/overlay/inscritos/"}, {"path": "/overlay/../admin-assets/index.html"}, {"path": "/overlay/x/../../etc/passwd"}],
            port=port,
        )
        assert [r["body"] for r in res[:3]] == [html, html, html]
        assert header(res[3], "Content-Type").startswith("application/javascript")
        assert header(res[4], "Content-Type").startswith("text/css")
        # ../ e normalizado pelo Nginx: na 8080 cai fora do mapa (404); na 8081 e o painel legitimo
        assert statuses(res[5:]) == [404, 404, 404 if port == 8080 else 200, 404]


# ---------------------------------------------------------------------------------------------- cabecalhos
def check_security(resp, overlay=False, label=""):
    assert header(resp, "Content-Security-Policy") == (CSP_OVERLAY if overlay else CSP_STRICT), label
    assert header(resp, "X-Content-Type-Options") == "nosniff", label
    assert header(resp, "Referrer-Policy") == "no-referrer", label
    assert header(resp, "Permissions-Policy"), label
    assert header(resp, "Cross-Origin-Opener-Policy") == "same-origin", label
    if overlay:
        assert header(resp, "X-Frame-Options") is None, label
    else:
        assert header(resp, "X-Frame-Options") == "DENY", label
    assert header(resp, "Server") == "nginx", label


def test_infra_ng_02_cabecalhos_em_200_404_405_401_503_e_429(stack_default):
    flood = {"path": "/health?flood=1", "repeat": 80}
    for port, ip in ((8080, "30"), (8081, "31")):
        cases = [
            ({"path": "/health"}, 200),
            ({"path": "/nao-existe"}, 404),
            ({"path": "/health", "method": "TRACE"}, 405),
            ({"path": "/live1.json", "headers": {"X-Fake-Status": "503"}}, 503),
            ({"path": "/live1.json", "headers": {"X-Fake-Cors": "1", "X-Fake-Status": "404"}}, 404),
        ]
        if port == 8081:
            cases += [({"path": "/api/admin/session", "headers": {"X-Fake-Status": "401"}}, 401),
                      ({"path": "/api/admin/session", "headers": {"X-Fake-Cors": "1"}}, 200),
                      ({"path": "/", }, 200), ({"path": "/nao-existe"}, 404)]
        res = stack_default.client(ip, [c for c, _ in cases] + [flood], port=port)
        for (spec, code), resp in zip(cases, res):
            assert resp["status"] == code, (port, spec)
            check_security(resp, label=f"{port} {spec} {code}")
        limited = [r for r in res[len(cases):] if r["status"] == 429]
        assert limited, f"nenhum 429 na {port}"
        check_security(limited[0], label=f"{port} 429")
        assert limited[0]["body"] == BODY_429
        assert header(limited[0], "Retry-After") == "1"
        assert header(limited[0], "Content-Type").startswith("application/json")


def test_infra_ng_02_overlays_embutiveis_e_o_resto_nao(stack_default):
    for port in (8080, 8081):
        res = stack_default.client(
            "32",
            [{"path": p} for p in ("/contador", "/overlay/inscritos", "/overlay/contador.js", "/overlay/contador.css", "/health")]
            + [{"path": "/overlay/nao-existe.js"}],
            port=port,
        )
        for resp in res[:4]:
            check_security(resp, overlay=True, label=f"{port} overlay")
            assert header(resp, "Cache-Control") == "no-cache"
        check_security(res[4], label=f"{port} health")
        check_security(res[5], label=f"{port} overlay 404 (erro do Nginx e estrito)")
    admin = stack_default.client("33", [{"path": "/"}, {"path": "/admin-assets/js/main.js"}], port=8081)
    for resp in admin:
        check_security(resp, label="painel")


# ---------------------------------------------------------------------------------------------- limit_req
def test_infra_ng_03_login_deixa_passar_6_e_bloqueia_a_7a(stack_default):
    res = stack_default.client("40", [{"path": "/api/admin/login", "method": "POST", "body": "{}", "repeat": 12}], port=8081)
    assert statuses(res) == [200] * 6 + [429] * 6
    blocked = res[6]
    assert blocked["body"] == BODY_429
    assert header(blocked, "Retry-After") == "6"
    assert header(blocked, "Content-Type").startswith("application/json")
    check_security(blocked)
    other = stack_default.client("41", [{"path": "/api/admin/login", "method": "POST", "body": "{}"}], port=8081)
    assert other[0]["status"] == 200, "o limite de um IP nao pode afetar outro"


def test_infra_ng_03_variantes_do_caminho_de_login_dividem_o_mesmo_limite(stack_default):
    paths = ["/api/admin/login"] * 3 + ["/api/admin/login/"] * 2 + ["//api/admin//login", "/api/admin/%6cogin", "/api/admin/login"]
    res = stack_default.client("42", [{"path": p, "method": "POST", "body": "{}"} for p in paths], port=8081)
    assert statuses(res) == [200] * 6 + [429] * 2


def test_infra_ng_03_zona_admin_e_publica(stack_default):
    res = stack_default.client("43", [{"path": "/api/admin/session", "repeat": 60}], port=8081)
    ok = statuses(res).count(200)
    assert 21 <= ok <= 28, f"burst 20 + 1 esperado, vieram {ok}"
    assert statuses(res)[ok:] == [429] * (60 - ok)
    assert header(res[-1], "Retry-After") == "1" and res[-1]["body"] == BODY_429

    res = stack_default.client("44", [{"path": "/live1.json", "repeat": 100}], port=8080)
    first_429 = statuses(res).index(429)
    assert 30 <= first_429 <= 45, first_429
    assert statuses(res).count(429) >= 50
    assert header(res[-1], "Access-Control-Allow-Origin") == "*", "o 429 das rotas de dados tem CORS"
    assert header(res[-1], "Access-Control-Allow-Credentials") is None
    assert header(res[-1], "Retry-After") == "1"
    assert stack_default.client("45", [{"path": "/live1.json"}], port=8080)[0]["status"] == 200


def test_infra_ng_03_overlay_polling_a_cada_5s_nunca_e_limitado(stack_default):
    res = stack_default.client("46", [{"path": "/api/public/subscribers"}, {"path": "/contador"}], port=8080)
    assert statuses(res) == [200, 200]


# ---------------------------------------------------------------------------------------------- IP real
def test_infra_ng_04_ip_real_8080_so_do_32_confiavel(stack_trusted):
    s = stack_trusted
    base = s.base
    # a) o "cloudflared" (.10) informa o IP do visitante
    s.client("10", [{"path": "/live1.json?t=a", "headers": {"CF-Connecting-IP": "198.51.100.1"}}])
    e = entry(s, "/live1.json?t=a")
    assert e["headers"]["x-real-ip"] == "198.51.100.1"
    assert e["headers"]["x-forwarded-for"] == "198.51.100.1"
    assert "cf-connecting-ip" not in e["headers"]
    # b) limites separados por visitante real
    res = s.client("10", [{"path": "/live1.json", "headers": {"CF-Connecting-IP": "198.51.100.1"}, "repeat": 100}])
    assert statuses(res).count(429) > 30
    other = s.client("10", [{"path": "/live1.json", "headers": {"CF-Connecting-IP": "198.51.100.2"}}])
    assert other[0]["status"] == 200
    # c) qualquer outra origem na edge tem o cabecalho ignorado: os dois valores dividem UM limite
    forged = [{"path": "/live1.json", "headers": {"CF-Connecting-IP": f"198.51.100.{100 + i % 2}"}} for i in range(100)]
    res = s.client("12", forged)
    assert statuses(res).count(200) <= 50, "valores forjados nao podem ter limites separados"
    assert 429 in statuses(res)
    s.client("12", [{"path": "/live1.json?t=c", "headers": {"CF-Connecting-IP": "198.51.100.200"}}] * 1)
    # (pode ja estar limitado; usa outro IP de origem para ler o que a api recebe)
    s.client("13", [{"path": "/live1.json?t=c2", "headers": {"CF-Connecting-IP": "198.51.100.200"}}])
    assert entry(s, "/live1.json?t=c2")["headers"]["x-real-ip"] == f"{base}.13"
    # d) o host (porta publicada => IP do gateway) tambem e ignorado
    r = s.host_request(s.port_public, "/live1.json?t=d", headers={"CF-Connecting-IP": "198.51.100.77"})
    assert r["status"] == 200
    seen = entry(s, "/live1.json?t=d")["headers"]["x-real-ip"]
    assert seen != "198.51.100.77" and seen != f"{base}.10"
    # e) X-Forwarded-For nunca vale como IP real na 8080 (nem do .10)
    s.client("10", [{"path": "/live1.json?t=e", "headers": {"X-Forwarded-For": "198.51.100.5"}}])
    assert entry(s, "/live1.json?t=e")["headers"]["x-real-ip"] == f"{base}.10"
    # o log de acesso do Nginx usa o IP real
    assert "198.51.100.1 " in s.logs()


def test_infra_ng_09_ip_real_8081_ultimo_salto_so_do_npm(stack_trusted):
    s = stack_trusted
    base = s.base
    xff = "203.0.113.7, 198.51.100.9"
    s.client("11", [{"path": "/live1.json?t=f", "headers": {"X-Forwarded-For": xff}}], port=8081)
    e = entry(s, "/live1.json?t=f")
    assert e["headers"]["x-real-ip"] == "198.51.100.9"
    assert e["headers"]["x-forwarded-for"] == "198.51.100.9"
    # limite de login por IP real: 198.51.100.9 estoura no 7o; 198.51.100.10 ainda passa
    login = {"path": "/api/admin/login", "method": "POST", "body": "{}"}
    res = s.client("11", [{**login, "headers": {"X-Forwarded-For": xff}, "repeat": 9}], port=8081)
    assert statuses(res) == [200] * 6 + [429] * 3
    ok = s.client("11", [{**login, "headers": {"X-Forwarded-For": "203.0.113.7, 198.51.100.10"}}], port=8081)
    assert ok[0]["status"] == 200
    # outra origem na edge: XFF ignorado, variar o cabecalho nao escapa do limite
    varied = [{**login, "headers": {"X-Forwarded-For": f"198.51.100.{50 + i}"}} for i in range(9)]
    res = s.client("14", varied, port=8081)
    assert statuses(res) == [200] * 6 + [429] * 3
    s.client("15", [{"path": "/live1.json?t=g", "headers": {"X-Forwarded-For": xff}}], port=8081)
    assert entry(s, "/live1.json?t=g")["headers"]["x-real-ip"] == f"{base}.15"
    # o cloudflared (.10) nao e confiavel na 8081, e o NPM (.11) nao e na 8080
    s.client("10", [{"path": "/live1.json?t=h", "headers": {"X-Forwarded-For": xff, "CF-Connecting-IP": "198.51.100.3"}}], port=8081)
    assert entry(s, "/live1.json?t=h")["headers"]["x-real-ip"] == f"{base}.10"
    s.client("11", [{"path": "/live1.json?t=i", "headers": {"CF-Connecting-IP": "198.51.100.3", "X-Forwarded-For": xff}}], port=8080)
    assert entry(s, "/live1.json?t=i")["headers"]["x-real-ip"] == f"{base}.11"
    # host (gateway) com XFF forjado
    s.host_request(s.port_admin, "/live1.json?t=j", headers={"X-Forwarded-For": xff})
    assert entry(s, "/live1.json?t=j")["headers"]["x-real-ip"] not in {"198.51.100.9", f"{base}.11"}


def test_infra_ng_04_09_padrao_vazio_ignora_os_dois_cabecalhos(stack_default):
    s = stack_default
    s.client("10", [{"path": "/live1.json?t=k", "headers": {"CF-Connecting-IP": "198.51.100.1", "X-Forwarded-For": "198.51.100.2"}}])
    assert entry(s, "/live1.json?t=k")["headers"]["x-real-ip"] == f"{s.base}.10"
    s.client("11", [{"path": "/live1.json?t=l", "headers": {"X-Forwarded-For": "203.0.113.7, 198.51.100.9"}}], port=8081)
    assert entry(s, "/live1.json?t=l")["headers"]["x-real-ip"] == f"{s.base}.11"
    forged = [{"path": "/live1.json", "headers": {"CF-Connecting-IP": f"198.51.100.{i % 2}"}} for i in range(100)]
    res = s.client("16", forged)
    assert statuses(res).count(200) <= 50


# ---------------------------------------------------------------------------------------------- metodos e corpo
def test_infra_ng_05_metodos_e_corpo(stack_default):
    s = stack_default
    res = s.client(
        "50",
        [{"path": "/live1.json?m=1", "method": "TRACE"}, {"path": "/live1.json?m=2", "method": "PUT", "body": "x"},
         {"path": "/health?m=3", "method": "POST", "body": "x"}, {"path": "/api/public/lives/1?m=4", "method": "POST", "body": "x"},
         {"path": "/inscritos.json?m=5", "method": "DELETE"}, {"path": "/contador", "method": "POST", "body": "x"},
         {"path": "/live1.json", "method": "GET"}],
    )
    assert statuses(res) == [405] * 6 + [200]
    for index, resp in enumerate(res[:6]):
        assert json.loads(resp["body"]) == {"detail": "Method Not Allowed"}
        check_security(resp, overlay=(index == 5), label=f"405 #{index}")
    assert not [e for e in s.api_log() if "?m=" in e["path"]], "metodo recusado nao pode chegar a api"
    res = s.client(
        "51",
        [{"path": "/api/admin/slots?m=6", "method": "TRACE"}, {"path": "/api/admin/session?m=7", "method": "OPTIONS"},
         {"path": "/api/admin/cookies?m=8", "method": "POST", "body_bytes": 262144},
         {"path": "/api/admin/cookies?m=9", "method": "POST", "body_bytes": 262145}],
        port=8081,
    )
    assert statuses(res) == [405, 405, 200, 413]
    no_cors(res[1])
    assert json.loads(res[3]["body"]) == {"detail": "Payload Too Large"}
    check_security(res[3])
    assert entry(s, "/api/admin/cookies?m=8")["body_len"] == 262144
    assert not [e for e in s.api_log() if e["path"].endswith(("?m=6", "?m=7", "?m=9"))]


# ---------------------------------------------------------------------------------------------- CORS
@pytest.mark.parametrize("port", [8080, 8081])
def test_infra_ng_08_cors_nos_dados_publicos(stack_default, port):
    origin = {"Origin": "https://qualquer.example"}
    specs = []
    for path in DATA_ROUTES:
        specs += [
            {"path": path, "headers": origin},
            {"path": path, "headers": {**origin, "X-Fake-Status": "404"}},
            {"path": path, "headers": {**origin, "X-Fake-Status": "503"}},
            {"path": path, "headers": {**origin, "X-Fake-Cors": "1"}},
            {"path": f"{path}?pf=1", "method": "OPTIONS",
             "headers": {**origin, "Access-Control-Request-Method": "GET"}},
        ]
    res = stack_default.client("52" if port == 8080 else "53", specs, port=port)
    for spec, resp in zip(specs, res):
        label = f"{port} {spec['method'] if 'method' in spec else 'GET'} {spec['path']}"
        assert header_values(resp, "Access-Control-Allow-Origin") == ["*"], label
        assert header_values(resp, "Access-Control-Allow-Methods") == ["GET"], label
        assert header(resp, "Access-Control-Allow-Credentials") is None, label
        assert header(resp, "Access-Control-Allow-Headers") is None, label
        assert header(resp, "Content-Security-Policy") == CSP_STRICT, label
        assert header(resp, "X-Powered-By") is None, label
        if spec.get("method") == "OPTIONS":
            assert resp["status"] == 204 and resp["body"] == "", label
    assert not [e for e in stack_default.api_log() if e["path"].endswith("?pf=1")], "preflight nao chega a api"


@pytest.mark.parametrize("port", [8080, 8081])
def test_infra_ng_08_sem_cors_nas_demais_rotas(stack_default, port):
    origin = {"Origin": "https://qualquer.example", "X-Fake-Cors": "1"}
    paths = ["/health", "/contador", "/overlay/inscritos", "/overlay/contador.js", "/", "/admin", "/admin-assets/js/main.js",
             "/api/admin/session", "/api/admin/login", "/api/admin/nao-existe", "/nao-existe", "/api/public"]
    specs = []
    for p in paths:
        specs += [{"path": p, "headers": origin},
                  {"path": p, "method": "OPTIONS", "headers": {**origin, "Access-Control-Request-Method": "POST"}},
                  {"path": p, "method": "POST", "body": "{}", "headers": origin}]
    res = stack_default.client("54" if port == 8080 else "55", specs, port=port)
    for spec, resp in zip(specs, res):
        no_cors(resp)
        assert header(resp, "Content-Security-Policy") in {CSP_STRICT, CSP_OVERLAY}
        assert header(resp, "X-Powered-By") is None
    # na porta publica a rota de admin e 404 mesmo com Origin
    if port == 8080:
        assert res[paths.index("/api/admin/session") * 3]["status"] == 404


# ---------------------------------------------------------------------------------------------- resiliencia
def test_infra_ng_extra_api_parada_da_502_json_e_o_proxy_acompanha_a_recriacao(web_image):
    dh.require_docker()
    s = dh.Stack(web_image).up()
    try:
        assert s.client("60", [{"path": "/live1.json"}])[0]["status"] == 200
        s.compose("stop", "api")
        res = s.client(
            "61",
            [{"path": "/live1.json", "headers": {"Origin": "https://x.example"}}, {"path": "/health"}, {"path": "/api/admin/session"}],
            port=8081,
        )
        # DNS sem a api: 502 (nome nao resolve) ou 504 (resolvedor demora); nunca HTML nem 200
        assert all(c in {502, 504} for c in statuses(res)), statuses(res)
        assert json.loads(res[0]["body"])["detail"] in {"Bad Gateway", "Gateway Timeout"}
        assert header(res[0], "Access-Control-Allow-Origin") == "*", "o 502 das rotas de dados tem CORS"
        no_cors(res[1])
        no_cors(res[2])
        check_security(res[1])
        s.compose("up", "-d", "--no-deps", "--force-recreate", "--wait", "api")
        dh.wait_until(lambda: s.client("62", [{"path": "/live1.json?ret=1"}])[0]["status"] == 200, timeout=60)
    finally:
        s.down()
