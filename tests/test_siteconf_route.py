"""Which server and location answer a request: nginx's algorithm, explained step by step."""

from __future__ import annotations

from pathlib import Path

import pytest

from noust.managers.siteconf import parse, route, structure

FIXTURES = Path(__file__).parent / "fixtures" / "siteconf"


def model_of(kind, *parts):
    return structure(parse(FIXTURES.joinpath(*parts).read_text(encoding="utf-8"), kind))


def location(model, location_id):
    def walk(locations):
        for loc in locations:
            if loc.id == location_id:
                return loc
            found = walk(loc.locations)
            if found:
                return found
        return None

    for server in model.servers:
        found = walk(server.locations)
        if found:
            return found
    raise AssertionError(location_id)


@pytest.fixture(scope="module")
def proggest():
    return model_of("nginx", "proggest", "proggest.es")


def resolve(model, url_path, host="proggest.es", scheme="https", port=None):
    result = route(model, host=host, path=url_path, scheme=scheme, port=port)
    loc = location(model, result.location_id) if result.location_id else None
    return result, loc


class TestProggest:
    def test_exact_login_route_beats_the_api_catch_all(self, proggest):
        result, loc = resolve(proggest, "/api/v1/auth/login")
        assert result.server_id == "s1"
        assert (loc.modifier, loc.path) == ("", "/api/v1/auth/login")
        explanation = "\n".join(result.steps)
        assert "/api/" in explanation
        assert "longest" in explanation
        assert result.highlight == ["s1", result.location_id, "u:nestjs_upstream"]

    def test_exact_match_wins_over_its_prefix(self, proggest):
        result, loc = resolve(proggest, "/uaap/")
        assert (loc.modifier, loc.path) == ("=", "/uaap/")
        assert any("exact" in step for step in result.steps)

    def test_prefix_without_slash(self, proggest):
        assert resolve(proggest, "/uaap/dashboard")[1].path == "/uaap"
        assert resolve(proggest, "/uaap")[1].path == "/uaap"

    def test_regexes_in_file_order(self, proggest):
        result, loc = resolve(proggest, "/.env")
        assert (loc.modifier, loc.path) == ("~", "/\\.")
        assert any("regular expression" in step for step in result.steps)

    def test_query_string_is_not_part_of_the_match(self, proggest):
        assert resolve(proggest, "/socket.io/?EIO=4&transport=websocket")[1].path == "/socket.io"

    def test_http_server_and_acme(self, proggest):
        result, loc = resolve(proggest, "/.well-known/acme-challenge/token", scheme="http")
        assert result.server_id == "s0"
        assert loc.path == "/.well-known/acme-challenge/"

    def test_fallback_to_the_root_location(self, proggest):
        result, loc = resolve(proggest, "/dashboard/settings")
        assert loc.path == "/"
        assert result.highlight[-1] == "u:nextjs_upstream"

    def test_no_server_on_that_port(self, proggest):
        result = route(proggest, host="proggest.es", path="/", scheme="http", port=8080)
        assert result.server_id is None
        assert result.location_id is None
        assert "8080" in result.steps[0]


def test_caret_tilde_stops_the_regex_check():
    model = model_of("nginx", "templates", "nginx-static.ssl.conf")
    result = route(
        model, host="docs.example.com", path="/.well-known/security.txt", scheme="https", port=None
    )
    loc = location(model, result.location_id)
    assert (loc.modifier, loc.path) == ("^~", "/.well-known/")
    assert any("^~" in step for step in result.steps)
    # Without ^~ the hidden-files regex would have answered.
    other = route(model, host="docs.example.com", path="/.git/config", scheme="https", port=None)
    assert location(model, other.location_id).path == "/\\."


def test_case_insensitive_regex():
    model = model_of("nginx", "templates", "nginx-static.ssl.conf")
    result = route(model, host="docs.example.com", path="/APP.JS", scheme="https", port=None)
    assert location(model, result.location_id).modifier == "~*"


def test_nested_locations_and_named_ones_never_match():
    model = model_of("nginx", "nginx", "nextcloud.conf")
    result = route(
        model, host="cloud.example.com", path="/.well-known/carddav", scheme="https", port=None
    )
    loc = location(model, result.location_id)
    assert (loc.modifier, loc.path) == ("=", "/.well-known/carddav")
    assert result.location_id.count("/l") == 2
    other = route(
        model, host="cloud.example.com", path="/.well-known/webfinger", scheme="https", port=None
    )
    assert location(model, other.location_id).path == "/.well-known"
    php = route(
        model, host="cloud.example.com", path="/index.php/apps/files", scheme="https", port=None
    )
    assert location(model, php.location_id).path == "\\.php(?:$|/)"


def test_auto_redirect_for_a_proxied_prefix_with_slash():
    text = (
        "server {\n listen 80;\n server_name a.test;\n"
        " location / { return 200; }\n"
        " location /api/ { proxy_pass http://127.0.0.1:1; }\n"
        " location ~ ^/api { return 403; }\n}\n"
    )
    model = structure(parse(text, "nginx"))
    result = route(model, host="a.test", path="/api", scheme="http", port=None)
    assert location(model, result.location_id).path == "/api/"
    assert result.redirect == "/api/"


SERVERS = """
server { listen 80; server_name first.test; }
server { listen 80; server_name exact.example.com; }
server { listen 80; server_name *.example.com; }
server { listen 80; server_name www.example.*; }
server { listen 80; server_name ~^api(?<n>\\d+)\\.example\\.net$; }
server { listen 80 default_server; server_name _; }
server { listen 443 ssl; server_name exact.example.com; }
"""


@pytest.mark.parametrize(
    ("host", "port", "expected", "word"),
    [
        ("exact.example.com", None, "s1", "exact"),
        ("EXACT.example.com:80", None, "s1", "exact"),
        ("x.example.com", None, "s2", "wildcard"),
        ("www.example.org", None, "s3", "wildcard"),
        ("api42.example.net", None, "s4", "regular expression"),
        ("nobody.test", None, "s5", "default_server"),
        ("exact.example.com", 443, "s6", "exact"),
    ],
)
def test_server_selection(host, port, expected, word):
    model = structure(parse(SERVERS, "nginx"))
    result = route(
        model, host=host, path="/", scheme="http" if port is None else "https", port=port
    )
    assert result.server_id == expected
    assert any(word in step for step in result.steps)


def test_first_server_on_the_port_without_default_server():
    model = structure(parse(SERVERS.replace(" default_server", ""), "nginx"))
    result = route(model, host="nobody.test", path="/", scheme="http", port=None)
    assert result.server_id == "s0"
    assert any("first" in step for step in result.steps)


def test_leading_wildcard_beats_trailing_and_longest_wins():
    text = (
        "server { listen 80; server_name www.example.*; }\n"
        "server { listen 80; server_name *.example.com; }\n"
        "server { listen 80; server_name *.www.example.com; }\n"
    )
    model = structure(parse(text, "nginx"))
    assert (
        route(model, host="www.example.com", path="/", scheme="http", port=None).server_id == "s1"
    )
    assert (
        route(model, host="a.www.example.com", path="/", scheme="http", port=None).server_id == "s2"
    )


def test_dot_name_matches_the_domain_and_its_subdomains():
    model = structure(
        parse(
            "server { listen 80; server_name .example.org; }\nserver { listen 80 default_server; }\n",
            "nginx",
        )
    )
    assert route(model, host="example.org", path="/", scheme="http", port=None).server_id == "s0"
    assert (
        route(model, host="a.b.example.org", path="/", scheme="http", port=None).server_id == "s0"
    )


def test_route_result_serialises():
    import json

    model = structure(parse(SERVERS, "nginx"))
    payload = route(model, host="x.example.com", path="/", scheme="http", port=None).to_dict()
    json.dumps(payload)
    assert set(payload) >= {"server_id", "location_id", "steps", "trace", "highlight", "redirect"}
    assert all({"code", "params"} <= set(step) for step in payload["trace"])


@pytest.fixture(scope="module")
def model():
    return model_of("apache", "apache", "reverse-proxy.conf")


class TestApache:
    def by_id(self, model, entry_id):
        for server in model.servers:
            for loc in server.locations:
                if loc.id == entry_id:
                    return loc
        raise AssertionError(entry_id)

    def go(self, model, path, host="app.example.com"):
        return route(model, host=host, path=path, scheme="https", port=None)

    def test_first_matching_proxypass_wins(self, model):
        result = self.go(model, "/api/v2/users")
        assert result.server_id == "s1"
        assert self.by_id(model, result.location_id).path == "/api/v2/"
        assert self.by_id(model, self.go(model, "/api/v1/x").location_id).path == "/api/"

    def test_exclusion(self, model):
        result = self.go(model, "/static/app.css")
        entry = self.by_id(model, result.location_id)
        assert entry.path == "/static"
        assert any("not proxied" in step for step in result.steps)

    def test_proxypass_inside_a_location_comes_first(self, model):
        result = self.go(model, "/health")
        assert self.by_id(model, result.location_id).path == "/health"

    def test_location_sections_apply_in_order(self, model):
        result = self.go(model, "/admin/users")
        assert self.by_id(model, result.location_id).target.upstream == "appcluster"
        assert any("/admin" in step for step in result.steps)
        assert "u:appcluster" in result.highlight

    def test_unknown_host_falls_to_the_first_vhost_of_the_port(self, model):
        assert self.go(model, "/", host="other.test").server_id == "s1"
        http = route(model, host="x.app.example.net", path="/", scheme="http", port=None)
        assert http.server_id == "s0"
