"""
The lossless guarantee of the site analyser, over the whole corpus.

``render(parse(text)) == text`` has to hold for every file nginx or Apache
accepts: Noust's own templates (rendered here with representative contexts, and
committed under ``tests/fixtures/siteconf/templates`` so a reviewer can read
them), the Proggest site, and public configurations that use what the
structured model does not understand (Lua, ``map``, ``geo``, ``split_clients``,
``if``, glob includes, third-party modules).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader

import noust
from noust.managers.siteconf import parse, structure

FIXTURES = Path(__file__).parent / "fixtures" / "siteconf"
TEMPLATES = Path(noust.__file__).parent / "templates"
HSTS = "max-age=31536000; includeSubDomains"


def _context(domain: str, *, ssl: bool, **extra: Any) -> dict[str, Any]:
    """Mirror WebServerManager.build_context without touching the store."""
    served = str(extra.pop("server_names", None) or domain).split()
    ctx: dict[str, Any] = {
        "domain": domain,
        "port": 3000,
        "app_path": f"/var/www/apps/{domain.replace('.', '-')}/current",
        "app_name": domain.replace(".", "-"),
        "ssl": ssl,
        "ssl_certificate": f"/etc/letsencrypt/live/{domain}/fullchain.pem",
        "ssl_certificate_key": f"/etc/letsencrypt/live/{domain}/privkey.pem",
        "hsts": HSTS,
        "redirect_domains": [],
    }
    ctx.update(extra)
    ctx["server_names"] = " ".join(served)
    ctx["server_aliases"] = [name for name in served if name != domain]
    return ctx


ADVANCED = {
    "upstreams": {
        "upstream_web": {"name": "web", "port": 3001},
        "upstream_api": {"name": "api", "port": 3002},
    },
    "routes": [
        {
            "path": "/api/",
            "upstream_name": "api",
            "upstream_port": 3002,
            "websocket": False,
            "rate_limit": "10r/s",
            "rate_limit_burst": 20,
            "rate_limit_zone": "zone_api",
            "buffer_size": "",
            "timeout": 90,
            "strip_prefix": True,
        },
        {
            "path": "/socket.io",
            "upstream_name": "api",
            "upstream_port": 3002,
            "websocket": True,
            "rate_limit": None,
            "rate_limit_burst": 0,
            "rate_limit_zone": "",
            "buffer_size": "256k",
            "timeout": 86400,
            "strip_prefix": False,
        },
        {
            "path": "/",
            "upstream_name": "web",
            "upstream_port": 3001,
            "websocket": False,
            "rate_limit": None,
            "rate_limit_burst": 0,
            "rate_limit_zone": "",
            "buffer_size": "",
            "timeout": 60,
            "strip_prefix": False,
        },
    ],
    "rate_limit_zones": {"global": "50r/s", "zone_api": "10r/s"},
    "security_headers": {
        "X-Frame-Options": "SAMEORIGIN",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
    },
    "custom_directives": ["client_max_body_size 50m", "proxy_intercept_errors on"],
}

WORKSPACES = {
    "workspaces": [
        {"name": "web-app", "subdomain": "app", "port": 3001},
        {"name": "api", "subdomain": "api", "port": 3002},
    ],
    "primary_subdomain": "app",
}

FASTCGI = {
    "document_root": "/var/www/apps/blog-example-com/current/public",
    "max_upload": "64m",
    "deny_paths": ["wp-config.php", "xmlrpc.php"],
    "fastcgi_socket": "/run/php/noust-blog-example-com.sock",
}

#: (kind, template, variant, context): every Noust template, in the shapes it
#: actually takes in production.
TEMPLATE_CASES: list[tuple[str, str, str, dict[str, Any]]] = [
    ("nginx", "proxy", "http", _context("shop.example.com", ssl=False, health_check="/")),
    (
        "nginx",
        "proxy",
        "ssl-aliases-redirects",
        _context(
            "shop.example.com",
            ssl=True,
            server_names="shop.example.com www.shop.example.com",
            redirect_domains=["old-shop.example.com"],
            health_check="/health",
        ),
    ),
    (
        "nginx",
        "proxy",
        "bluegreen",
        _context(
            "shop.example.com",
            ssl=True,
            upstream_name="wasm_bg_shop-example-com",
            upstream_file="/etc/nginx/noust-upstreams/shop-example-com.conf",
        ),
    ),
    ("nginx", "static", "http", _context("docs.example.com", ssl=False)),
    (
        "nginx",
        "static",
        "ssl",
        _context(
            "docs.example.com",
            ssl=True,
            static_dir="/var/www/apps/docs-example-com/current/dist",
            redirect_domains=["www.docs.example.com"],
        ),
    ),
    ("nginx", "fastcgi", "http", _context("blog.example.com", ssl=False, **FASTCGI)),
    ("nginx", "fastcgi", "ssl", _context("blog.example.com", ssl=True, **FASTCGI)),
    ("nginx", "hooks", "http", _context("hooks.example.com", ssl=False, port=8080)),
    (
        "nginx",
        "hooks",
        "ssl-https-upstream",
        _context("hooks.example.com", ssl=True, port=8443, upstream_scheme="https"),
    ),
    ("nginx", "monorepo", "http", _context("mono.example.com", ssl=False, **WORKSPACES)),
    ("nginx", "monorepo", "ssl", _context("mono.example.com", ssl=True, **WORKSPACES)),
    ("nginx", "advanced", "http", _context("multi.example.com", ssl=False, **ADVANCED)),
    (
        "nginx",
        "advanced",
        "ssl",
        _context(
            "multi.example.com",
            ssl=True,
            redirect_domains=["multi.example.net"],
            **ADVANCED,
        ),
    ),
    ("apache", "proxy", "http", _context("shop.example.com", ssl=False)),
    (
        "apache",
        "proxy",
        "ssl-aliases-redirects",
        _context(
            "shop.example.com",
            ssl=True,
            server_names="shop.example.com www.shop.example.com",
            redirect_domains=["old-shop.example.com"],
        ),
    ),
    ("apache", "static", "http", _context("docs.example.com", ssl=False)),
    (
        "apache",
        "static",
        "ssl",
        _context("docs.example.com", ssl=True, static_dir="/srv/docs/dist"),
    ),
]


def render_template(kind: str, template: str, context: dict[str, Any]) -> str:
    """Render one Noust template the way WebServerManager does."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES / kind)),
        trim_blocks=True,
        lstrip_blocks=True,
        autoescape=False,  # noqa: S701 - web server configuration, not markup
    )
    return env.get_template(f"{template}.conf.j2").render(**context)


def fixture_name(kind: str, template: str, variant: str) -> str:
    """The committed file a rendered template case lives in."""
    return f"{kind}-{template}.{variant}.conf"


def corpus() -> Iterator[tuple[str, Path]]:
    """Every committed corpus file, with the dialect it is written in."""
    for path in sorted((FIXTURES / "nginx").iterdir()):
        yield "nginx", path
    for path in sorted((FIXTURES / "proggest").iterdir()):
        yield "nginx", path
    for path in sorted((FIXTURES / "apache").iterdir()):
        yield "apache", path
    for path in sorted((FIXTURES / "templates").iterdir()):
        yield ("apache" if path.name.startswith("apache-") else "nginx"), path


CORPUS = list(corpus())


def read(path: Path) -> str:
    """Read a fixture exactly, line endings included."""
    with path.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def test_corpus_is_the_size_the_plan_asks_for():
    names = {path.name for _, path in CORPUS}
    assert {"proggest.es", "modulos.proggest.es"} <= names
    assert len([1 for kind, path in CORPUS if path.parent.name == "nginx"]) >= 12
    assert len([1 for kind, path in CORPUS if path.parent.name == "apache"]) >= 4
    every_template = {
        (kind, path.name.removesuffix(".conf.j2"))
        for kind in ("nginx", "apache")
        for path in (TEMPLATES / kind).glob("*.conf.j2")
    }
    assert every_template <= {(kind, template) for kind, template, _, _ in TEMPLATE_CASES}


@pytest.mark.parametrize(("kind", "path"), CORPUS, ids=[p.name for _, p in CORPUS])
def test_round_trip_is_byte_for_byte(kind, path):
    text = read(path)
    tree = parse(text, kind)
    assert tree.render() == text


@pytest.mark.parametrize(("kind", "path"), CORPUS, ids=[p.name for _, p in CORPUS])
def test_every_corpus_file_has_a_structure(kind, path):
    model = structure(parse(read(path), kind))
    payload = model.to_dict()
    assert payload["kind"] == kind


@pytest.mark.parametrize(
    ("kind", "template", "variant", "context"),
    TEMPLATE_CASES,
    ids=[f"{k}-{t}-{v}" for k, t, v, _ in TEMPLATE_CASES],
)
def test_templates_as_they_are_today_round_trip(kind, template, variant, context):
    # Rendered live, so a template changed after the fixtures were written is
    # still held to the guarantee.
    text = render_template(kind, template, context)
    assert parse(text, kind).render() == text


@pytest.mark.parametrize(
    ("kind", "template", "variant", "context"),
    TEMPLATE_CASES,
    ids=[f"{k}-{t}-{v}" for k, t, v, _ in TEMPLATE_CASES],
)
def test_template_fixture_exists(kind, template, variant, context):
    assert (FIXTURES / "templates" / fixture_name(kind, template, variant)).is_file()


def test_unknown_constructs_round_trip():
    # Review focus 2: what the model does not understand is kept exactly,
    # and it is all still there, in the raw directives of its element.
    text = read(FIXTURES / "nginx" / "openresty-lua.conf")
    text += read(FIXTURES / "nginx" / "map-geo-split.conf")
    tree = parse(text, "nginx")
    assert tree.render() == text
    model = structure(tree).to_dict()
    top = [directive["name"] for directive in model["directives"]]
    for name in ("init_by_lua_block", "map", "geo", "split_clients", "lua_shared_dict"):
        assert name in top
    lua = next(d for d in model["directives"] if d["name"] == "init_by_lua_block")
    assert '"quote \\" and brace }"' in lua["text"]
    assert lua["block"] is True
