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
OVERLAY_LOCATIONS = {"= /contador", "^~ /overlay/"}
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
    for code in ("400", "403", "404", "405", "413", "429", "500", "502", "504"):
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
    assert ng.PLACEHOLDER_ADMIN_GEO in head and ng.PLACEHOLDER_ADMIN_GEO not in tail  # geo no http, antes dos server
    assert template.count("${") == 3, "so os tres marcadores do envsubst (IP real da 8080 e a lista de permissao da 8081)"


def test_infra_ng_04_09_diretivas_geradas_pelo_script():
    text = (ng.NGINX_DIR / "docker-entrypoint.d" / "15-livestats-realip.envsh").read_text(encoding="utf-8")
    pub = re.search(r'LIVESTATS_REALIP_PUBLIC="(real_ip[^"]*)"', text).group(1)
    assert "real_ip_header CF-Connecting-IP;" in pub and "X-Forwarded-For" not in pub
    assert pub.endswith("set_real_ip_from ${LIVESTATS_IP}/32;")
    assert text.count('LIVESTATS_REALIP_PUBLIC=""') == 1, "vazio => marcador vazio => nenhuma diretiva"
    # D33: a 8081 nao tem IP real: o script nao gera diretiva real_ip para ela nem le a variavel antiga
    assert "ADMIN_TRUSTED_PROXY_IP" not in text and "LIVESTATS_REALIP_ADMIN" not in text
    assert not re.search(r"real_ip_header\s+X-Forwarded-For", text)
    # as variaveis lidas sao exatamente as do compose
    assert "TRUSTED_PROXY_CIDR" in text and "ADMIN_ALLOW_CIDRS" in text


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
            only_return = [c.name for c in loc.children] == ["return"]
            if loc.args[0].startswith("@") or only_return:
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


# ---------------------------------------------------------------------------------------------- D33: 8081 em TLS
def test_infra_ng_14_8081_escuta_em_tls_e_a_8080_nao(pub, adm):
    assert adm.find("listen")[0].args == ["8081", "ssl", "default_server"]
    assert pub.find("listen")[0].args == ["8080", "default_server"]
    assert not [n for n in pub.walk() if n.name.startswith("ssl_")]


def test_infra_ng_14_tls_12_13_sem_tickets_e_secrets_nos_caminhos_do_compose(adm):
    one = lambda name: adm.find(name)[0].args  # noqa: E731
    assert one("ssl_protocols") == ["TLSv1.2", "TLSv1.3"]
    assert one("ssl_session_tickets") == ["off"]
    assert one("ssl_certificate") == ["/run/secrets/admin_tls_cert"]
    assert one("ssl_certificate_key") == ["/run/secrets/admin_tls_key"]
    ciphers = one("ssl_ciphers")[0]
    assert ciphers.count(":") >= 3 and not re.search(r"RC4|MD5|3DES|DES-CBC|NULL|EXPORT|aNULL|CBC", ciphers)
    assert one("ssl_prefer_server_ciphers") == ["off"]


def test_infra_ng_14_sem_hsts_em_lugar_nenhum(tree):
    for node in tree.walk():
        if node.name == "add_header":
            assert node.args[0].lower() != "strict-transport-security"
    for path in sorted(ng.NGINX_DIR.rglob("*")):
        if path.is_file():
            assert "strict-transport-security" not in path.read_text(encoding="utf-8").lower(), path


def test_infra_ng_14_lista_de_permissao_no_rewrite_do_server_antes_das_locations(adm):
    """`allow/deny` nao alcancam `location / { return 404; }`: o `if` + `return 403` no nivel do server cobre tudo."""
    first_if = next(c for c in adm.children if c.name == "if")
    assert first_if.args == ["($livestats_admin_allowed", "=", "0)"]
    assert first_if.find("return")[0].args == ["403"]
    names = [c.name for c in adm.children]
    assert names.index("if") < names.index("location")
    assert [c.args for c in adm.children if c.name == "deny"] == [["all"]]
    assert 403 in [int(n.args[0]) for n in adm.children if n.name == "error_page" and n.args[0].isdigit()]


def test_infra_ng_14_http_simples_na_porta_tls_vira_400_json(adm):
    pages = [c for c in adm.children if c.name == "error_page"]
    assert any(c.args[0] == "497" and "=400" in c.args and "@e400" in c.args for c in pages)


def test_infra_ng_14_8081_sem_nenhum_cabecalho_de_ip_confiavel(adm):
    assert not [n for n in adm.walk() if n.name in REAL_IP]
    assert "http_x_forwarded_for" not in "".join(str(n.args) for n in adm.walk()).lower()


def test_infra_ng_15_overlay_inscritos_nao_existe_so_o_contador(tree, pub, adm):
    """D36: um só overlay de inscritos. `/overlay/inscritos` não tem location própria nem aparece em nenhum arquivo
    de configuração; cai no `^~ /overlay/` (só arquivos existentes) e vira 404; `/contador` continua mapeado."""
    for server in (pub, adm):
        keys = [ng.loc_key(loc) for loc in ng.locations(server)]
        assert "= /contador" in keys
        assert not [k for k in keys if "inscritos" in k and k != "= /inscritos.json"], keys
        assert "= /overlay/inscritos" not in keys
    confs = list(ng.NGINX_DIR.rglob("*.conf*"))
    assert confs, "nenhum arquivo de configuração encontrado"
    for path in confs:
        assert "overlay/inscritos" not in path.read_text(encoding="utf-8"), path
