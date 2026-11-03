"""Leitor minimo de configuracao do Nginx para os testes estaticos de tests/infra.

Le web/nginx/nginx.conf, resolve os `include` que apontam para web/nginx/snippets/ e o template dos
`server` (substituindo os marcadores do envsubst), e devolve uma arvore de diretivas. Nao executa nada
e nao depende de Docker.
"""

from __future__ import annotations

import re
from pathlib import Path

NGINX_DIR = Path(__file__).resolve().parents[2] / "web" / "nginx"
PLACEHOLDER_PUBLIC = "${LIVESTATS_REALIP_PUBLIC}"
PLACEHOLDER_ADMIN = "${LIVESTATS_ADMIN_ALLOW}"  # allow ... (8081, D33)
PLACEHOLDER_ADMIN_GEO = "${LIVESTATS_ADMIN_GEO}"  # entradas do geo da lista de permissao


class Node:
    def __init__(self, name, args, children=None, parent=None):
        self.name = name
        self.args = args
        self.children = children  # None => diretiva simples; lista => bloco
        self.parent = parent

    def __repr__(self):
        return f"Node({self.name} {' '.join(self.args)})"

    @property
    def is_block(self):
        return self.children is not None

    def find(self, name):
        """Diretivas filhas diretas com este nome."""
        return [c for c in (self.children or []) if c.name == name]

    def walk(self):
        yield self
        for child in self.children or []:
            yield from child.walk()


def tokenize(text: str):
    tokens = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
        elif ch == "#":
            while i < n and text[i] != "\n":
                i += 1
        elif ch in ";{}":
            tokens.append((ch, False))
            i += 1
        elif ch in "'\"":
            quote, j = ch, i + 1
            buf = []
            while j < n and text[j] != quote:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            tokens.append(("".join(buf), True))
            i = j + 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in ";{}":
                if text[j] == "$" and j + 1 < n and text[j + 1] == "{":
                    j = text.index("}", j) + 1
                    continue
                j += 1
            tokens.append((text[i:j], False))
            i = j
    return tokens


def parse(text: str):
    root = Node("root", [], [])
    stack = [root]
    words = []
    for tok, quoted in tokenize(text):
        if not quoted and tok == ";":
            node = Node(words[0], words[1:], None, stack[-1])
            stack[-1].children.append(node)
            words = []
        elif not quoted and tok == "{":
            node = Node(words[0], words[1:], [], stack[-1])
            stack[-1].children.append(node)
            stack.append(node)
            words = []
        elif not quoted and tok == "}":
            stack.pop()
        else:
            words.append(tok)
    assert not words, f"diretiva sem ';' no fim: {words}"
    assert len(stack) == 1, "chaves desbalanceadas"
    return root


def _resolve_includes(node: Node, public: str, admin: str, geo: str = ""):
    out = []
    for child in node.children or []:
        if child.name == "include":
            target = child.args[0]
            if target.startswith("/etc/nginx/snippets/"):
                sub = parse((NGINX_DIR / "snippets" / target.rsplit("/", 1)[1]).read_text(encoding="utf-8"))
                _resolve_includes(sub, public, admin, geo)
                for item in sub.children:
                    item.parent = node
                    out.append(item)
            elif target == "/etc/nginx/conf.d/*.conf":
                template = (NGINX_DIR / "templates" / "default.conf.template").read_text(encoding="utf-8")
                template = template.replace(PLACEHOLDER_PUBLIC, public).replace(PLACEHOLDER_ADMIN, admin).replace(PLACEHOLDER_ADMIN_GEO, geo)
                sub = parse(template)
                _resolve_includes(sub, public, admin, geo)
                for item in sub.children:
                    item.parent = node
                    out.append(item)
            # mime.types e afins: ignorados
            continue
        if child.is_block:
            _resolve_includes(child, public, admin, geo)
        out.append(child)
    node.children = out


def load(public: str = "", admin: str = "", geo: str = "") -> Node:
    """Arvore completa de nginx.conf, com snippets e template expandidos.

    `public`/`admin`/`geo` substituem os marcadores do envsubst (vazio = IP real desligado e nenhuma entrada de lista).
    """
    root = parse((NGINX_DIR / "nginx.conf").read_text(encoding="utf-8"))
    _resolve_includes(root, public, admin, geo)
    return root


def http_block(root: Node) -> Node:
    return root.find("http")[0]


def servers(root: Node):
    return http_block(root).find("server")


def listen_port(server: Node) -> int:
    return int(server.find("listen")[0].args[0])


def server_by_port(root: Node, port: int) -> Node:
    return next(s for s in servers(root) if listen_port(s) == port)


def locations(server: Node):
    """Todas as `location` do server (inclusive nomeadas)."""
    return [c for c in server.children if c.name == "location"]


def loc_key(loc: Node) -> str:
    return " ".join(loc.args)


def add_headers(node: Node):
    """{nome: (valor, tem_always)} dos add_header DIRETOS do bloco."""
    result = {}
    for d in node.find("add_header"):
        result[d.args[0]] = (d.args[1], d.args[-1] == "always")
    return result


def all_add_header_blocks(server: Node):
    """(rotulo, bloco) de todo bloco (server, location, if) que declare add_header."""
    found = []
    if server.find("add_header"):
        found.append(("server", server))
    for loc in locations(server):
        for node in loc.walk():
            if node.find("add_header"):
                found.append((loc_key(loc), node))
    return found


def strip_comments(text: str) -> str:
    return re.sub(r"(?m)^\s*#.*$", "", text)
