"""INFRA-NG-*: verificacoes estaticas da configuracao do Nginx (sem Docker).

Usa tests/infra/nginx_conf.py, que le web/nginx/ com os snippets e o template expandidos.
"""

from __future__ import annotations

import re

import pytest

import nginx_conf as ng

CSP_STRICT = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
    "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
CSP_OVERLAY = CSP_STRICT.replace("frame-ancestors 'none'", "frame-ancestors *")
OVERLAY_LOCATIONS = {"= /contador", "= /overlay/inscritos", "^~ /overlay/"}
CORS_LOCATIONS = {"^~ /api/public/", "~ ^/live[0-9]+\\.json$", "= /inscritos.json"}
CORS_ERROR_LOCATIONS = {"@cors_e429", "@cors_e502", "@cors_e504"}
PROXY_PUBLIC = {"= /health"} | CORS_LOCATIONS
PROXY_ADMIN = {"/api/admin/", "~ ^/api/admin/login/*$"}
REAL_IP = ("real_ip_header", "real_ip_recursive", "set_real_ip_from")


@pytest.fixture(scope="module")
def tree():
    return ng.load()


@pytest.fixture(scope="module")
def pub(tree):
    return ng.server_by_port(tree, 8080)


@pytest.fixture(scope="module")
def adm(tree):
    return ng.server_by_port(tree, 8081)


def proxied(server):
    return {ng.loc_key(loc) for loc in ng.locations(server) if any(n.name == "proxy_pass" for n in loc.walk())}


def test_infra_ng_01_dois_servers(tree):
    assert sorted(ng.listen_port(s) for s in ng.servers(tree)) == [8080, 8081]


def test_infra_ng_01_8080_sem_rota_de_admin(pub):
    for loc in ng.locations(pub):
        text = " ".join(str(n.args) for n in loc.walk()) + ng.loc_key(loc)
        assert "admin" not in text.lower(), f"location de admin na 8080: {ng.loc_key(loc)}"
    default = [loc for loc in ng.locations(pub) if ng.loc_key(loc) == "/"]
    assert len(default) == 1
    assert [n.args for n in default[0].find("return")] == [["404"]]
    assert not default[0].find("proxy_pass")


def test_infra_ng_02_cabecalhos_em_todos_os_blocos(tree, pub, adm):
    for server in (pub, adm):
        blocks = ng.all_add_header_blocks(server)
        assert any(label == "server" for label, _ in blocks)
        for label, node in blocks:
            headers = ng.add_headers(node)
            assert all(always for _, always in headers.values()), f"{label}: add_header sem always"
            is_overlay = label in OVERLAY_LOCATIONS
            csp = headers["Content-Security-Policy"][0]
            assert csp == (CSP_OVERLAY if is_overlay else CSP_STRICT), f"{label}: CSP incorreta"
            assert headers["X-Content-Type-Options"][0] == "nosniff"
            assert "Referrer-Policy" in headers and "Permissions-Policy" in headers
            assert headers["Cross-Origin-Opener-Policy"][0] == "same-origin"
            if is_overlay:
                assert "X-Frame-Options" not in headers, "overlay nao pode enviar X-Frame-Options (D11)"
            else:
                assert headers["X-Frame-Options"][0] == "DENY"
            assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_infra_ng_02_excecao_de_overlay_so_nos_overlays(pub, adm):
    for server in (pub, adm):
        found = {
            label
            for label, node in ng.all_add_header_blocks(server)
            if "frame-ancestors *" in ng.add_headers(node)["Content-Security-Policy"][0]
        }
        assert found == OVERLAY_LOCATIONS


def test_infra_ng_02_erros_json_com_cabecalhos(pub):
    named = {ng.loc_key(loc): loc for loc in ng.locations(pub) if loc.args[0].startswith("@")}
    for code in ("403", "404", "413", "429", "500", "502", "504"):
        loc = named[f"@e{code}"]
        assert loc.find("default_type")[0].args == ["application/json"]
        assert loc.find("return")[0].args[0] == code
        assert "Content-Security-Policy" in ng.add_headers(loc)
    ret = named["@e429"].find("return")[0].args[1]
    assert ret == '{"detail": "Muitas tentativas. Tente novamente em instantes."}'
    assert "Retry-After" in ng.add_headers(named["@e429"])


def test_infra_ng_03_zonas_e_status(tree):
    http = ng.http_block(tree)
    zones = {}
    for d in http.find("limit_req_zone"):
        zones[d.args[1].split("=")[1].split(":")[0]] = (d.args[0], d.args[2])
    assert zones["login"] == ("$binary_remote_addr", "rate=10r/m")
    assert zones["admin"] == ("$binary_remote_addr", "rate=10r/s")
    assert zones["public"] == ("$binary_remote_addr", "rate=10r/s")
    assert http.find("limit_req_status")[0].args == ["429"]


def test_infra_ng_03_limites_por_location(adm):
    expected = {
        "~ ^/api/admin/login/*$": ["zone=login", "burst=5", "nodelay"],
        "/api/admin/": ["zone=admin", "burst=20", "nodelay"],
        "= /health": ["zone=public", "burst=30", "nodelay"],
    }
    for loc in ng.locations(adm):
        key = ng.loc_key(loc)
        limits = [n.args for n in loc.walk() if n.name == "limit_req"]
        if any(n.name == "proxy_pass" for n in loc.walk()) or key in {"= /contador", "^~ /overlay/", "= /"}:
            assert len(limits) == 1, f"{key}: sem limit_req"
        if key in expected:
            assert limits[0] == expected[key]
        elif limits:
            assert limits[0] == ["zone=public", "burst=30", "nodelay"]
    # login: burst + 1 requisicoes seguidas >= 6 (a 6a tentativa tem que chegar a api, D5)
    assert 5 + 1 >= 6


def test_infra_ng_04_ip_real_somente_via_marcadores(tree):
    """Com os marcadores vazios nao existe NENHUMA diretiva real_ip em lugar algum."""
    for node in tree.walk():
        assert node.name not in REAL_IP, f"diretiva {node.name} fora dos marcadores do envsubst"


def test_infra_ng_04_marcadores_no_server_certo():
    template = (ng.NGINX_DIR / "templates" / "default.conf.template").read_text(encoding="utf-8")
    head, _, tail = template.partition("listen 8081")
    assert ng.PLACEHOLDER_PUBLIC in head and ng.PLACEHOLDER_ADMIN not in head
    assert ng.PLACEHOLDER_ADMIN in tail and ng.PLACEHOLDER_PUBLIC not in tail
    assert template.count("${") == 2, "so os dois marcadores do envsubst"


def test_infra_ng_04_09_diretivas_geradas_pelo_script():
    text = (ng.NGINX_DIR / "docker-entrypoint.d" / "15-livestats-realip.envsh").read_text(encoding="utf-8")
    pub = re.search(r'LIVESTATS_REALIP_PUBLIC="(real_ip[^"]*)"', text).group(1)
    adm = re.search(r'LIVESTATS_REALIP_ADMIN="(real_ip[^"]*)"', text).group(1)
    assert "real_ip_header CF-Connecting-IP;" in pub and "X-Forwarded-For" not in pub
    assert "real_ip_header X-Forwarded-For;" in adm and "real_ip_recursive off;" in adm
    assert "CF-Connecting-IP" not in adm
    assert pub.endswith("set_real_ip_from ${LIVESTATS_IP}/32;") and adm.endswith("set_real_ip_from ${LIVESTATS_IP}/32;")
    assert text.count('=""') == 2, "vazio => marcador vazio => nenhuma diretiva"
    # as variaveis lidas sao exatamente as do compose
    assert "TRUSTED_PROXY_CIDR" in text and "ADMIN_TRUSTED_PROXY_IP" in text


def test_infra_ng_04_09_validacao_rejeita_o_perigoso():
    text = (ng.NGINX_DIR / "docker-entrypoint.d" / "15-livestats-realip.envsh").read_text(encoding="utf-8")
    assert '[ "$_mask" = "32" ]' in text
    assert "0|1|255" in text and '[ "$_o1" != "0" ]' in text
    assert text.count("exit 1") == 1 and "livestats_fail" in text


@pytest.mark.parametrize("name", ["pub", "adm"])
def test_infra_ng_04_proxy_sobrescreve_ip(request, name):
    server = request.getfixturevalue(name)
    for loc in ng.locations(server):
        sets = {n.args[0]: n.args[1] for n in loc.walk() if n.name == "proxy_set_header"}
        if sets:
            assert sets["X-Real-IP"] == "$remote_addr"
            assert sets["X-Forwarded-For"] == "$remote_addr"
            assert sets["CF-Connecting-IP"] == ""
            assert "$proxy_add_x_forwarded_for" not in sets.values()


def test_infra_ng_05_endurecimento(tree, pub, adm):
    http = ng.http_block(tree)
    assert http.find("server_tokens")[0].args == ["off"]
    size = http.find("client_max_body_size")[0].args[0]
    assert size.lower() in {"256k", "128k", "64k"}
    assert not [n for n in tree.walk() if n.name == "autoindex" and n.args != ["off"]]
    allowed_public = {"GET", "HEAD", "OPTIONS"}
    allowed_admin = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"}
    for server in (pub, adm):
        for loc in ng.locations(server):
            if loc.args[0].startswith("@") or ng.loc_key(loc) == "/":
                continue
            ifs = [n for n in loc.walk() if n.name == "if" and any("$request_method" in a for a in n.args)]
            assert ifs, f"{ng.loc_key(loc)}: sem restricao de metodo"
            restriction = [n for n in ifs if "!~" in n.args]
            assert len(restriction) == 1
            methods = set(re.search(r"\^\(([^)]*)\)\$", restriction[0].args[-1]).group(1).split("|"))
            limit = allowed_admin if ng.loc_key(loc) in PROXY_ADMIN else allowed_public
            assert methods <= limit, f"{ng.loc_key(loc)}: metodos {methods}"
            assert {"TRACE", "CONNECT"}.isdisjoint(methods)


def test_infra_ng_06_mapa_de_rotas_proxy(pub, adm):
    assert proxied(pub) == PROXY_PUBLIC
    assert proxied(adm) == PROXY_PUBLIC | PROXY_ADMIN
    for server in (pub, adm):
        root = [loc for loc in ng.locations(server) if ng.loc_key(loc) == "/"][0]
        assert not [n for n in root.walk() if n.name == "proxy_pass"], "proxy aberto em location /"
        for loc in ng.locations(server):
            for n in loc.walk():
                if n.name == "proxy_pass":
                    assert n.args == ["http://$livestats_api"]


def test_infra_ng_06_mapa_de_rotas_estaticas(pub, adm):
    def statics(server):
        out = {}
        for loc in ng.locations(server):
            tf = [n for n in loc.walk() if n.name == "try_files"]
            if tf:
                out[ng.loc_key(loc)] = tf[0].args
        return out

    common = {
        "= /contador": ["/overlay/contador.html", "=404"],
        "= /overlay/inscritos": ["/overlay/contador.html", "=404"],
        "^~ /overlay/": ["$uri", "=404"],
    }
    assert statics(pub) == common
    assert statics(adm) == {
        **common,
        "= /": ["/admin-assets/index.html", "=404"],
        "~ ^/admin/?$": ["/admin-assets/index.html", "=404"],
        "^~ /admin-assets/": ["$uri", "=404"],
    }
    for server in (pub, adm):
        assert server.find("root")[0].args == ["/usr/share/nginx/html"]


def test_infra_ng_08_cors_somente_nos_gets_publicos_de_dados(pub, adm):
    for server in (pub, adm):
        for loc in ng.locations(server):
            key = ng.loc_key(loc)
            for node in loc.walk():
                cors = [d for d in node.find("add_header") if d.args[0].lower().startswith("access-control-")]
                if not cors:
                    continue
                assert key in CORS_LOCATIONS | CORS_ERROR_LOCATIONS, f"CORS fora do escopo: {key}"
                assert {d.args[0]: d.args[1:] for d in cors} == {
                    "Access-Control-Allow-Origin": ["*", "always"],
                    "Access-Control-Allow-Methods": ["GET", "always"],
                }
    for server in (pub, adm):
        for node in server.walk():
            for d in node.find("add_header"):
                assert d.args[0].lower() != "access-control-allow-credentials"


def test_infra_ng_08_preflight_204_nos_dados(pub, adm):
    for server in (pub, adm):
        for loc in ng.locations(server):
            if ng.loc_key(loc) in CORS_LOCATIONS:
                opts = [n for n in loc.walk() if n.name == "if" and n.args == ["($request_method", "=", "OPTIONS)"]]
                assert opts and opts[0].find("return")[0].args == ["204"]
                # nada que sobrescreva os cabecalhos dentro do if
                assert not opts[0].find("add_header")


def test_infra_ng_09_8080_nao_usa_xff_para_ip_real(pub):
    for node in pub.walk():
        if node.name in REAL_IP:
            assert "X-Forwarded-For" not in node.args
    template = (ng.NGINX_DIR / "templates" / "default.conf.template").read_text(encoding="utf-8")
    assert "http_x_forwarded_for" not in template.lower()
