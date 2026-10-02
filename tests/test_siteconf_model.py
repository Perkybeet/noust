"""The structured model: what the Structure view and the diagram are drawn from."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from noust.managers.siteconf import parse, structure

FIXTURES = Path(__file__).parent / "fixtures" / "siteconf"


def load(kind: str, *parts: str, **kwargs):
    path = FIXTURES.joinpath(*parts)
    with path.open(encoding="utf-8", newline="") as handle:
        return structure(parse(handle.read(), kind), **kwargs)


def by_path(server, path, modifier=""):
    return next(loc for loc in server.locations if loc.path == path and loc.modifier == modifier)


@pytest.fixture(scope="module")
def proggest():
    return load("nginx", "proggest", "proggest.es")


class TestProggest:
    def test_servers_upstreams_and_locations(self, proggest):
        assert [server.id for server in proggest.servers] == ["s0", "s1"]
        assert [up.id for up in proggest.upstreams] == ["u:nextjs_upstream", "u:nestjs_upstream"]
        assert len(proggest.servers[0].locations) == 2
        assert len(proggest.servers[1].locations) == 25

    def test_listens_names_and_tls(self, proggest):
        http, https = proggest.servers
        assert [(entry.port, entry.ssl, entry.ipv6) for entry in http.listens] == [
            (80, False, False),
            (80, False, True),
        ]
        assert [(entry.port, entry.ssl, entry.ipv6) for entry in https.listens] == [
            (443, True, False),
            (443, True, True),
        ]
        assert https.http2 is True
        assert https.names == ["proggest.es", "www.proggest.es"]
        assert https.tls is not None
        assert https.tls.certificate == "/etc/letsencrypt/live/proggest.es/fullchain.pem"
        assert https.tls.key == "/etc/letsencrypt/live/proggest.es/privkey.pem"
        assert https.tls.protocols == ["TLSv1.2", "TLSv1.3"]
        assert http.tls is None
        assert https.gzip is True
        assert ("Strict-Transport-Security", "max-age=63072000; includeSubDomains") in [
            (h.name, h.value) for h in https.headers
        ]

    def test_targets_are_classified(self, proggest):
        https = proggest.servers[1]
        assets = by_path(https, "/assets/")
        assert assets.target.kind == "static"
        assert assets.target.alias == "/var/www/proggest/apps/web-gateway/public/assets/"
        assert assets.settings.expires == "1y"

        uaap = by_path(https, "/uaap/", "=")
        assert uaap.target.kind == "return"
        assert uaap.target.code == 302
        assert uaap.target.destination == "/uaap/dashboard"

        socket = by_path(https, "/socket.io")
        assert socket.target.kind == "proxy"
        assert socket.target.upstream == "nestjs_upstream"
        assert socket.settings.websocket is True
        assert socket.settings.read_timeout == "86400s"

        login = by_path(https, "/api/v1/auth/login")
        assert login.id == "s1/l2"
        assert login.settings.limit_req == {"zone": "auth_limit", "burst": "20", "nodelay": True}
        assert login.settings.websocket is False

        files = by_path(https, "/api/files/")
        assert files.settings.buffering is False
        assert files.settings.client_max_body_size == "10G"

        health = by_path(https, "/health")
        assert health.target.kind == "return"
        assert health.target.code == 200

        hidden = by_path(https, "/\\.", "~")
        assert hidden.target.kind == "other"
        assert hidden.settings.deny is True

        acme = by_path(proggest.servers[0], "/.well-known/acme-challenge/")
        assert acme.target.kind == "static"
        assert acme.target.root == "/var/www/certbot"

        root = by_path(https, "/")
        assert root.target.kind == "proxy"
        assert root.target.upstream == "nextjs_upstream"
        assert root.target.url == "http://nextjs_upstream"

    def test_upstreams(self, proggest):
        nextjs = proggest.upstreams[0]
        assert nextjs.name == "nextjs_upstream"
        assert [(s.address, s.source) for s in nextjs.servers] == [("127.0.0.1:3001", None)]
        assert nextjs.keepalive == 64
        assert sorted(nextjs.used_by) == sorted(
            loc.id
            for loc in proggest.servers[1].locations
            if loc.target.upstream == "nextjs_upstream"
        )

    def test_evaluation_order_is_nginx_order(self, proggest):
        https = proggest.servers[1]
        order = https.evaluation_order
        locs = {loc.id: loc for loc in https.locations}
        assert len(order) == 25
        assert locs[order[0]].modifier == "="
        prefixes = [locs[i] for i in order if locs[i].modifier in ("", "^~")]
        assert [len(p.path) for p in prefixes] == sorted(
            (len(p.path) for p in prefixes), reverse=True
        )
        regexes = [locs[i].path for i in order if locs[i].modifier in ("~", "~*")]
        assert regexes == ["/\\.", "^/(\\.env|\\.git|docker-compose|Dockerfile)"]
        assert order[-2:] == [locs_id for locs_id in order if locs[locs_id].modifier == "~"]

    def test_comments_are_attached_or_kept_as_notes(self, proggest):
        https = proggest.servers[1]
        uaap = by_path(https, "/uaap/", "=")
        assert any("Cortacircuito" in comment for comment in uaap.comments)
        assert any("Páginas del módulo UAAP" in comment for comment in uaap.comments)
        settings_loc = by_path(https, "/api/settings/")
        assert settings_loc.comments == []
        assert any("Next.js API route handlers" in note.text for note in https.notes)
        assert any(note.after == "s1/l4" for note in https.notes)
        uaap_prefix = by_path(https, "/uaap")
        body_size = next(d for d in uaap_prefix.directives if d.name == "client_max_body_size")
        assert any("adjuntos de formaciones" in comment for comment in body_size.comments)
        assert proggest.servers[0].comments == ["HTTP server (redirect to HTTPS)"]
        assert proggest.servers[1].comments == ["HTTPS server"]

    def test_every_directive_is_listed_raw(self, proggest):
        https = proggest.servers[1]
        socket = by_path(https, "/socket.io")
        names = [d.name for d in socket.directives]
        assert names.count("proxy_set_header") == 6
        assert all(d.id.startswith(socket.id + "/d") for d in socket.directives)
        upgrade = next(d for d in socket.directives if d.args[:1] == ["Upgrade"])
        assert upgrade.text == "proxy_set_header Upgrade $http_upgrade;"
        assert upgrade.modeled is True
        cache = next(
            d for d in by_path(https, "/api/").directives if d.name == "proxy_cache_bypass"
        )
        assert cache.modeled is False
        server_level = [d.name for d in https.directives]
        assert "ssl_ciphers" in server_level and "location" not in server_level

    def test_ids_are_stable(self):
        first = load("nginx", "proggest", "proggest.es").to_dict()
        second = load("nginx", "proggest", "proggest.es").to_dict()
        assert first == second
        json.dumps(first)

    def test_lines_point_into_the_file(self, proggest):
        uaap = by_path(proggest.servers[1], "/uaap/", "=")
        text = (FIXTURES / "proggest" / "proggest.es").read_text(encoding="utf-8")
        assert text.splitlines()[uaap.line - 1].strip() == "location = /uaap/ {"
        assert text.splitlines()[uaap.end_line - 1].strip() == "}"


def test_modulos_keeps_the_if_as_a_raw_block():
    model = load("nginx", "proggest", "modulos.proggest.es")
    https = model.servers[1]
    block = next(d for d in https.directives if d.name == "if")
    assert block.block is True
    assert block.args == ["($host", "=", "partestrabajo.proggest.es)"]
    assert "return 302" in block.text
    assert model.upstreams == []
    socket = by_path(https, "/socket.io")
    assert socket.target.kind == "proxy"
    # The upstream lives in the other site file: named, but not resolved here.
    assert socket.target.upstream is None
    assert socket.target.host == "nestjs_upstream"
    assert socket.target.port is None


class TestTemplates:
    def test_proxy_template(self):
        model = load("nginx", "templates", "nginx-proxy.ssl-aliases-redirects.conf")
        names = [server.names for server in model.servers]
        assert ["old-shop.example.com"] in names
        main = next(
            s
            for s in model.servers
            if s.names == ["shop.example.com", "www.shop.example.com"] and s.tls
        )
        root = by_path(main, "/")
        assert root.target.kind == "proxy"
        assert root.target.url == "http://127.0.0.1:3000"
        assert root.settings.websocket is True
        assert root.settings.read_timeout == "60s"
        assert root.settings.buffering is True
        assert main.http2 is True
        redirect = next(s for s in model.servers if s.names == ["old-shop.example.com"] and s.tls)
        assert redirect.returns is not None
        assert redirect.returns.code == 301

    def test_static_template_uses_inherited_root(self):
        model = load("nginx", "templates", "nginx-static.ssl.conf")
        main = model.servers[-1]
        spa = by_path(main, "/")
        assert spa.target.kind == "static"
        assert spa.target.root == "/var/www/apps/docs-example-com/current/dist"
        assert spa.target.inherited is True

    def test_fastcgi_template(self):
        model = load("nginx", "templates", "nginx-fastcgi.ssl.conf")
        php = by_path(model.servers[-1], "\\.php$", "~")
        assert php.target.kind == "fastcgi"
        assert php.target.address == "unix:/run/php/noust-blog-example-com.sock"
        assert php.settings.read_timeout == "300"

    def test_monorepo_named_locations(self):
        model = load("nginx", "templates", "nginx-monorepo.http.conf")
        named = [loc for loc in model.servers[0].locations if loc.modifier == "@"]
        assert [loc.path for loc in named] == ["web_app", "api"]
        assert named[0].target.upstream == "web_app_backend"

    def test_advanced_template_rate_limit_and_rewrite(self):
        model = load("nginx", "templates", "nginx-advanced.ssl.conf")
        api = by_path(model.servers[-1], "/api/")
        assert api.settings.limit_req == {"zone": "zone_api", "burst": "20", "nodelay": True}
        assert api.target.upstream == "api"
        names = [d.name for d in model.directives]
        assert names.count("limit_req_zone") == 2


class TestIncludes:
    def reader(self, files):
        def read(pattern):
            if pattern not in files:
                raise FileNotFoundError(pattern)
            return files[pattern]

        return read

    def test_upstream_servers_from_a_noust_file(self):
        reader = self.reader(
            {
                "/etc/nginx/noust-upstreams/shop/web.servers": [
                    (
                        "/etc/nginx/noust-upstreams/shop/web.servers",
                        "server 127.0.0.1:3005;\nserver 127.0.0.1:3006 backup;\n",
                    )
                ],
            }
        )
        model = load("nginx", "nginx", "load-balancer.conf", read_include=reader)
        shop = next(up for up in model.upstreams if up.name == "wasm_bg_shop_web")
        assert [(s.address, s.params, s.source) for s in shop.servers] == [
            ("127.0.0.1:3005", [], "/etc/nginx/noust-upstreams/shop/web.servers"),
            ("127.0.0.1:3006", ["backup"], "/etc/nginx/noust-upstreams/shop/web.servers"),
        ]
        location = by_path(model.servers[0], "/shop/")
        assert location.target.upstream == "wasm_bg_shop_web"

    def test_glob_includes_and_failures_are_recorded_not_raised(self):
        reader = self.reader(
            {
                "snippets/*.conf": [
                    ("/etc/nginx/snippets/a.conf", "add_header X-A 1;\n"),
                    ("/etc/nginx/snippets/b.conf", "add_header X-B 2;\n"),
                ],
            }
        )
        model = load("nginx", "nginx", "load-balancer.conf", read_include=reader)
        by_pattern = {inc.pattern: inc for inc in model.includes}
        assert [f.path for f in by_pattern["snippets/*.conf"].files] == [
            "/etc/nginx/snippets/a.conf",
            "/etc/nginx/snippets/b.conf",
        ]
        assert by_pattern["snippets/*.conf"].parent == "s0"
        assert by_pattern["/etc/nginx/conf.d/*.conf"].error
        assert by_pattern["snippets/headers with space.conf"].files == []

    def test_an_unparsable_include_is_an_error_on_the_include(self):
        reader = self.reader(
            {"/etc/nginx/conf.d/*.conf": [("/etc/nginx/conf.d/x.conf", "server {")]}
        )
        model = load("nginx", "nginx", "load-balancer.conf", read_include=reader)
        inc = next(i for i in model.includes if i.pattern == "/etc/nginx/conf.d/*.conf")
        assert "line 1" in inc.error

    def test_upstreams_defined_in_an_included_file(self):
        reader = self.reader(
            {
                "/etc/nginx/noust-upstreams/shop-example-com.conf": [
                    (
                        "/etc/nginx/noust-upstreams/shop-example-com.conf",
                        "upstream wasm_bg_shop-example-com {\n    server 127.0.0.1:3001;\n}\n",
                    )
                ]
            }
        )
        model = load("nginx", "templates", "nginx-proxy.bluegreen.conf", read_include=reader)
        (upstream,) = model.upstreams
        assert upstream.source == "/etc/nginx/noust-upstreams/shop-example-com.conf"
        assert upstream.servers[0].address == "127.0.0.1:3001"
        root = by_path(model.servers[-1], "/")
        assert root.target.upstream == "wasm_bg_shop-example-com"


def test_full_nginx_conf_finds_servers_inside_http_only():
    model = load("nginx", "nginx", "full-nginx.conf")
    assert [server.names for server in model.servers] == [["status.local"]]
    assert model.servers[0].id == "s0"
    stub = by_path(model.servers[0], "/stub_status", "=")
    assert stub.target.kind == "other"


def test_edge_case_server_names_and_locations():
    model = load("nginx", "nginx", "edge-cases.conf")
    (server,) = model.servers
    assert server.names[:4] == ["example.com", ".example.org", "*.example.net", "mail.*"]
    assert server.names[-1] == ""
    mods = [(loc.modifier, loc.path) for loc in server.locations]
    assert ("=", "/exact") in mods
    assert ("^~", "/images/") in mods
    assert ("@", "fallback") in mods
    assert ("~", "^/a{2}/(?<rest>.*)$") in mods
    assert server.listens[1].ipv6 is True


class TestApache:
    def test_reverse_proxy(self):
        model = load("apache", "apache", "reverse-proxy.conf")
        assert [s.id for s in model.servers] == ["s0", "s1"]
        http, https = model.servers
        assert [(entry.address, entry.port) for entry in http.listens] == [("*", 80)]
        assert http.names == ["app.example.com", "www.app.example.com", "*.app.example.net"]
        assert https.tls is not None and https.listens[0].ssl is True
        assert https.client_max_body_size == "10485760"
        (cluster,) = model.upstreams
        assert cluster.id == "u:appcluster"
        assert [s.address for s in cluster.servers] == [
            "http://10.0.0.21:8080",
            "http://10.0.0.22:8080",
        ]
        kinds = [(loc.modifier, loc.path, loc.target.kind) for loc in https.locations]
        assert ("", "/static", "other") in kinds
        assert ("", "/api/v2/", "proxy") in kinds
        assert ("~", "^/ws/(.*)$", "proxy") in kinds
        assert ("", "/admin", "other") in kinds
        assert ("~", "^/api/v[0-9]+/internal/", "other") in kinds
        health = next(loc for loc in https.locations if loc.path == "/health")
        assert health.target.kind == "proxy"
        assert health.id.startswith("s1/l")
        root = next(
            loc for loc in https.locations if loc.path == "/" and loc.target.kind == "proxy"
        )
        assert root.target.upstream == "appcluster"
        assert root.id.startswith("s1/d")

    def test_ifmodule_wrapped_vhost(self):
        model = load("apache", "apache", "default-ssl.conf")
        (server,) = model.servers
        assert server.listens[0].port == 443
        assert server.listens[0].ssl is True
        assert server.tls.certificate == "/etc/ssl/certs/ssl-cert-snakeoil.pem"
        assert server.root == "/var/www/html"

    def test_template(self):
        model = load("apache", "templates", "apache-proxy.ssl-aliases-redirects.conf")
        main = next(s for s in model.servers if s.tls and s.names[0] == "shop.example.com")
        assert main.names == ["shop.example.com", "www.shop.example.com"]
        proxy = next(loc for loc in main.locations if loc.target.kind == "proxy")
        assert proxy.target.url == "http://127.0.0.1:3000/"
