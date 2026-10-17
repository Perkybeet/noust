# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What happens at a prefix of a site: a password asked or lifted, headers, another service, a redirect.

A route's values are written into a file the web server loads as root, so most of what is
pinned here is what is refused (a path outside the closed alphabet, a target that could end
its directive, a route that would shadow the certificate authority's path, one that nests
with a proxy or a redirect). The rest is that the routes are rendered by whoever writes the
site, in nginx and in Apache, and that they survive a redeploy.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.deployers import access, domains
from noust.managers.apache_manager import ApacheManager
from noust.managers.site_policy import (
    SitePolicy,
    SitePolicyManager,
    policy_context,
    response_headers,
    validated,
)
from noust.managers.site_routes import SiteRoute, normal_path, regex_of, route_from, routes_from
from noust.managers.webserver import APACHE_BACKEND
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

HASH = "$6$salt$" + "a" * 86


# -- paths ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("given", "path"),
    [
        ("/admin", "/admin"),
        ("/admin/", "/admin"),
        (" /api/v2.1 ", "/api/v2.1"),
        ("/a_b-c~d", "/a_b-c~d"),
    ],
)
def test_a_path_is_given_its_canonical_spelling(given: str, path: str) -> None:
    assert normal_path(given) == path


@pytest.mark.parametrize(
    "given",
    [
        "",
        "/",
        "admin",
        "//admin",
        "/a//b",
        "/a/../b",
        "/a/./b",
        "/ad min",
        "/admin;",
        "/admin{",
        '/ad"min',
        "/adm$in",
        "/adm%in",
        "/a\nb",
        "/*",
        "/x" * 150,
        None,
        5,
    ],
)
def test_a_path_outside_the_closed_alphabet_is_refused(given: Any) -> None:
    with pytest.raises(ValidationError) as raised:
        normal_path(given)

    assert raised.value.field == "routes"


@pytest.mark.parametrize("given", ["/.well-known", "/.well-known/acme-challenge", "/.well-known/x"])
def test_the_certificate_authoritys_path_cannot_be_taken_over(given: str) -> None:
    with pytest.raises(ValidationError) as raised:
        normal_path(given)

    assert "certificates are validated" in raised.value.message


def test_a_dot_never_reaches_the_configuration_as_a_backslash() -> None:
    assert regex_of("/v1.2") == "^/v1[.]2(?:/|$)"


# -- one route ------------------------------------------------------------------


def test_a_route_of_kind_site_must_change_something() -> None:
    with pytest.raises(ValidationError) as raised:
        route_from({"path": "/admin"})

    assert "must change something" in raised.value.message
    assert route_from({"path": "/admin", "auth": "password"}).auth == "password"
    assert route_from({"path": "/admin", "headers": {"X-A": "b"}}).headers == {"X-A": "b"}


def test_a_proxy_needs_a_local_port() -> None:
    for port in (None, 0, 65536, "80", True):
        with pytest.raises(ValidationError):
            route_from({"path": "/api", "kind": "proxy", "port": port})
    route = route_from({"path": "/api", "kind": "proxy", "port": 4000, "strip": True})
    assert (route.port, route.strip, route.auth) == (4000, True, "inherit")


@pytest.mark.parametrize(
    "target",
    [
        "https://docs.example.com/guide",
        "http://example.org",
        "https://example.org:8443/a/b?x=1&y=2",
        "/new",
        "/new/page.html",
    ],
)
def test_a_redirect_goes_to_an_address_or_a_path(target: str) -> None:
    keep = "?" not in target
    route = route_from({"path": "/old", "kind": "redirect", "to": target, "keep_path": keep})

    assert (route.to, route.code) == (target, 301)


@pytest.mark.parametrize(
    "target",
    [
        "",
        "ftp://example.org",
        "//example.org",
        "example.org/x",
        "https://example.org/a b",
        "https://example.org/a;b",
        'https://example.org/"',
        "https://example.org/$1",
        "https://example.org/#frag",
        "https://exa mple.org",
        "/new\nreturn 200",
        "javascript:alert(1)",
        "https://" + "a" * 300 + ".org",
    ],
)
def test_a_target_that_could_end_its_directive_is_refused(target: str) -> None:
    with pytest.raises(ValidationError):
        route_from({"path": "/old", "kind": "redirect", "to": target})


def test_a_redirect_that_keeps_the_path_cannot_carry_its_own_query() -> None:
    with pytest.raises(ValidationError) as raised:
        route_from({"path": "/old", "kind": "redirect", "to": "/new?a=1"})

    assert "query" in raised.value.message


def test_a_redirect_answers_with_a_redirect_status_only() -> None:
    for code in (200, 300, 303, 404, True, "301"):
        with pytest.raises(ValidationError):
            route_from({"path": "/old", "kind": "redirect", "to": "/new", "code": code})
    assert route_from({"path": "/old", "kind": "redirect", "to": "/new", "code": 308}).code == 308


@pytest.mark.parametrize(
    "extra",
    [{"auth": "open"}, {"headers": {"X-A": "b"}}, {"port": 80}, {"strip": True}],
)
def test_a_redirect_has_nothing_else_to_say(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        route_from({"path": "/old", "kind": "redirect", "to": "/new", **extra})


def test_a_field_of_another_kind_is_refused() -> None:
    for data in (
        {"path": "/a", "auth": "open", "port": 4000},
        {"path": "/a", "auth": "open", "strip": True},
        {"path": "/a", "auth": "open", "to": "/b"},
        {"path": "/a", "kind": "proxy", "port": 4000, "to": "/b"},
        {"path": "/a", "kind": "proxy", "port": 4000, "nonsense": 1},
        {"path": "/a", "kind": "teleport"},
        {"path": "/a", "auth": "sometimes"},
    ):
        with pytest.raises(ValidationError):
            route_from(data)


@pytest.mark.parametrize("value", ['a"b', "a$b", "line\nbreak", "50%", ""])
def test_a_route_header_value_is_held_to_the_same_rules_as_the_sites(value: str) -> None:
    with pytest.raises(ValidationError):
        route_from({"path": "/a", "headers": {"X-A": value}})


@pytest.mark.parametrize("name", ["Host", "Content-Length", "bad name", "a;b"])
def test_a_route_header_the_web_server_owns_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        route_from({"path": "/a", "headers": {name: "x"}})


def test_a_route_cannot_remove_a_default_header_it_has_no_value_for() -> None:
    with pytest.raises(ValidationError):
        route_from({"path": "/a", "headers": {"X-Frame-Options": None}})


# -- the list -------------------------------------------------------------------


def test_the_routes_come_back_longest_path_first() -> None:
    routes = routes_from(
        [
            {"path": "/a", "auth": "password"},
            {"path": "/a/public", "auth": "open"},
            {"path": "/b/c/d", "kind": "proxy", "port": 4000},
        ]
    )

    assert [route.path for route in routes] == ["/a/public", "/b/c/d", "/a"]


def test_a_path_is_routed_once() -> None:
    with pytest.raises(ValidationError) as raised:
        routes_from([{"path": "/a", "auth": "open"}, {"path": "/a/", "auth": "password"}])

    assert "routed twice" in raised.value.message


def test_a_proxy_or_a_redirect_cannot_nest_with_another_route() -> None:
    proxy = {"path": "/api", "kind": "proxy", "port": 4000}
    for other in (
        {"path": "/api/v2", "auth": "open"},
        {"path": "/api/v2", "kind": "redirect", "to": "/x"},
    ):
        with pytest.raises(ValidationError):
            routes_from([proxy, other])
        with pytest.raises(ValidationError):
            routes_from([other, proxy])
    # Prefixes that only share letters are not nested.
    assert len(routes_from([proxy, {"path": "/apix", "auth": "open"}])) == 2
    # Routes that only change the password or the headers nest as they like.
    assert (
        len(routes_from([{"path": "/a", "auth": "password"}, {"path": "/a/b", "auth": "open"}]))
        == 2
    )


def test_a_site_has_a_bounded_number_of_routes() -> None:
    many = [{"path": f"/p{index}", "auth": "open"} for index in range(41)]

    with pytest.raises(ValidationError):
        routes_from(many)
    assert len(routes_from(many[:40])) == 40


def test_the_policy_keeps_routes_in_its_document_and_forgets_them_with_reset() -> None:
    policy = validated(SitePolicy(), {"routes": [{"path": "/admin", "auth": "password"}]})

    assert policy.routes == (SiteRoute(path="/admin", auth="password"),)
    assert SitePolicy.from_document(__import__("json").dumps(policy.to_dict())) == policy
    assert not policy.is_default
    assert validated(policy, {"routes": []}).is_default


def test_a_stored_document_with_a_route_the_rules_no_longer_accept_falls_back() -> None:
    document = '{"routes": [{"path": "/x y", "auth": "open"}]}'

    assert SitePolicy.from_document(document) == SitePolicy()


# -- nginx ----------------------------------------------------------------------


def _users(domain: str, *names: str) -> SitePolicyManager:
    policies = SitePolicyManager()
    for name in names:
        policies.records.set_user(domain, name, HASH, created_by="test")
    return policies


def test_a_password_route_asks_for_the_password_only_there(
    machine: Machine, store: NoustStore
) -> None:
    machine.deploy(app_type="nodejs")
    _users("example.com", "ana")

    access.set_policy("example.com", {"routes": [{"path": "/admin", "auth": "password"}]})

    config = machine.config()
    assert "map $uri $noust_auth_example_com {" in config
    block = config.split("map $uri $noust_auth_example_com {")[1].split("}")[0]
    assert "default off;" in block
    assert '"~^/admin(?:/|$)" "Restricted";' in block
    assert "auth_basic $noust_auth_example_com;" in config
    assert "auth_basic_user_file" in config
    # The accounts file exists although the site as a whole asks for no password.
    assert (machine.web.sites_available.parent / "noust-auth" / "example-com.users").exists()
    # The certificate authority is still let in without it.
    challenge = config.split("location /.well-known/acme-challenge/ {")[1].split("}")[0]
    assert "auth_basic off;" in challenge


def test_an_open_route_lifts_the_sites_password_there(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    _users("example.com", "ana")

    access.set_policy(
        "example.com",
        {
            "basic_auth": True,
            "realm": "Staff",
            "routes": [
                {"path": "/hooks", "auth": "open"},
                {"path": "/hooks/private", "auth": "password"},
            ],
        },
    )

    block = machine.config().split("map $uri $noust_auth_example_com {")[1].split("}")[0]
    assert 'default "Staff";' in block
    # The longer path is tried first, so a password route inside an open one still asks.
    assert block.index("/hooks/private") < block.index('"~^/hooks(?:/|$)"')
    assert '"~^/hooks(?:/|$)" off;' in block


def test_without_routes_the_password_is_asked_with_a_literal_realm(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    _users("example.com", "ana")

    access.set_policy("example.com", {"basic_auth": True})

    config = machine.config()
    assert 'auth_basic "Restricted";' in config
    assert "map $uri" not in config


def test_a_password_route_without_accounts_is_refused_and_nothing_changes(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs")
    before = machine.config()

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"routes": [{"path": "/admin", "auth": "password"}]})

    assert "has no accounts" in raised.value.message
    assert machine.config() == before
    assert SitePolicyManager().get("example.com").is_default


def test_the_last_account_cannot_go_while_a_route_needs_it(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    _users("example.com", "ana")
    access.set_policy("example.com", {"routes": [{"path": "/admin", "auth": "password"}]})

    with pytest.raises(ValidationError) as raised:
        access.remove_user("example.com", "ana")

    assert "only account" in raised.value.message


def test_a_route_header_replaces_the_sites_under_its_prefix(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    access.set_policy(
        "example.com",
        {
            "routes": [
                {"path": "/embed", "headers": {"X-Frame-Options": "ALLOWALL"}},
                {
                    "path": "/api",
                    "headers": {"Access-Control-Allow-Origin": "https://app.example.org"},
                },
            ]
        },
    )

    config = machine.config()
    frame = config.split("map $uri $noust_hdr_example_com_1 {")[1].split("}")[0]
    assert 'default "SAMEORIGIN";' in frame
    assert '"~^/embed(?:/|$)" "ALLOWALL";' in frame
    cors = config.split("map $uri $noust_hdr_example_com_2 {")[1].split("}")[0]
    assert 'default "";' in cors
    # One header of each name, whose value the path decides; the others are untouched.
    assert "add_header X-Frame-Options $noust_hdr_example_com_1 always;" in config
    assert "add_header Access-Control-Allow-Origin $noust_hdr_example_com_2 always;" in config
    assert config.count("add_header X-Frame-Options") == 1
    assert 'add_header X-Content-Type-Options "nosniff" always;' in config


def test_a_service_behind_a_prefix_loses_the_prefix_when_asked(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    access.set_policy(
        "example.com",
        {
            "routes": [
                {"path": "/api", "kind": "proxy", "port": 4000, "strip": True},
                {"path": "/blog", "kind": "proxy", "port": 5000},
            ]
        },
    )

    config = machine.config()
    api = config.split("location ^~ /api/ {")[1].split("\n    }")[0]
    assert "proxy_pass http://127.0.0.1:4000/;" in api
    assert "proxy_set_header X-Forwarded-Prefix /api;" in api
    assert "proxy_set_header Host $host;" in api
    api_exact = config.split("location = /api {")[1].split("\n    }")[0]
    assert "proxy_pass http://127.0.0.1:4000/;" in api_exact
    blog = config.split("location ^~ /blog/ {")[1].split("\n    }")[0]
    assert "proxy_pass http://127.0.0.1:5000;" in blog
    assert "X-Forwarded-Prefix" not in blog
    # The site itself is still served by its own location, after these.
    assert config.index("location ^~ /blog/") < config.index("proxy_pass http://127.0.0.1:3000;")


def test_a_redirect_carries_the_rest_of_the_path_unless_told_not_to(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    access.set_policy(
        "example.com",
        {
            "routes": [
                {
                    "path": "/docs",
                    "kind": "redirect",
                    "to": "https://docs.example.org/",
                    "code": 308,
                },
                {"path": "/old.page", "kind": "redirect", "to": "/new", "keep_path": False},
            ]
        },
    )

    config = machine.config()
    assert "location ~ ^/docs(/.*)?$ {" in config
    assert "return 308 https://docs.example.org$1$is_args$args;" in config
    assert "location ~ ^/old[.]page(/.*)?$ {" in config
    assert "return 301 /new;" in config


def test_the_routes_survive_an_alias_and_a_redeploy(machine: Machine, store: NoustStore) -> None:
    machine.deploy(app_type="nodejs")
    access.set_policy(
        "example.com",
        {
            "routes": [
                {"path": "/api", "kind": "proxy", "port": 4000},
                {"path": "/old", "kind": "redirect", "to": "/new"},
            ]
        },
    )

    domains.add_domain("example.com", "shop.example.com", "alias")
    domains.refresh_site(store.get_app("example.com"))

    config = machine.config()
    assert "server_name example.com shop.example.com;" in config
    assert config.count("location ^~ /api/ {") == 1
    assert config.count("location ~ ^/old(/.*)?$ {") == 1


def test_a_static_site_carries_routes_too(machine: Machine) -> None:
    machine.deploy(app_type="static")
    _users("example.com", "ana")

    access.set_policy(
        "example.com",
        {
            "routes": [
                {"path": "/private", "auth": "password"},
                {"path": "/api", "kind": "proxy", "port": 4000, "strip": True},
            ]
        },
    )

    config = machine.config()
    assert "auth_basic $noust_auth_example_com;" in config
    assert "location ^~ /api/ {" in config
    assert config.index("location ^~ /api/ {") < config.index("try_files $uri $uri/ /index.html;")


def test_the_web_server_refusing_the_routes_puts_everything_back(
    machine: Machine, runner: Any
) -> None:
    machine.deploy(app_type="nodejs")
    before = machine.config()
    runner.script(
        ["nginx", "-t"], stderr='nginx: [emerg] duplicate location "/api/"\n', exit_code=1
    )

    with pytest.raises(ValidationError):
        access.set_policy(
            "example.com", {"routes": [{"path": "/api", "kind": "proxy", "port": 4000}]}
        )

    assert machine.config() == before
    assert SitePolicyManager().get("example.com").routes == ()


def test_a_policy_without_routes_renders_nothing_of_them(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    access.set_policy("example.com", {"noindex": True})

    config = machine.config()

    for text in ("map $uri", "auth_basic", "location ^~", "location ~ ", "noust_hdr"):
        assert text not in config


# -- apache ---------------------------------------------------------------------


@pytest.fixture
def apache(tmp_path: Path, runner: Any, store: NoustStore) -> ApacheManager:
    return ApacheManager(
        backend=replace(
            APACHE_BACKEND,
            sites_available=tmp_path / "apache/sites-available",
            sites_enabled=tmp_path / "apache/sites-enabled",
        )
    )


def _apache_site(apache: ApacheManager, policy: SitePolicy, template: str = "proxy") -> str:
    SitePolicyManager().save("example.com", policy, actor="test")
    return apache.render_config("example.com", template, {"port": 3000})


def test_apache_asks_for_the_password_in_a_location_of_the_prefix(
    apache: ApacheManager, store: NoustStore
) -> None:
    _users("example.com", "ana")
    policy = validated(
        SitePolicy(),
        {"routes": [{"path": "/admin", "auth": "password"}, {"path": "/hooks", "auth": "open"}]},
    )

    config = _apache_site(apache, policy)

    admin = config.split('<Location "/admin">')[1].split("</Location>")[0]
    assert "AuthType Basic" in admin
    assert "Require valid-user" in admin
    hooks = config.split('<Location "/hooks">')[1].split("</Location>")[0]
    assert "AuthType" not in hooks
    assert "Require all granted" in hooks
    # The site as a whole asks for nothing.
    assert "<Location />" not in config


def test_apache_hands_a_prefix_to_a_service_and_redirects_another(
    apache: ApacheManager, store: NoustStore
) -> None:
    policy = validated(
        SitePolicy(),
        {
            "routes": [
                {"path": "/api", "kind": "proxy", "port": 4000, "strip": True},
                {"path": "/blog", "kind": "proxy", "port": 5000},
                {
                    "path": "/docs",
                    "kind": "redirect",
                    "to": "https://docs.example.org/",
                    "code": 302,
                },
            ]
        },
    )

    config = _apache_site(apache, policy)

    assert 'ProxyPassMatch "^/api(?:/(.*))?$" "http://127.0.0.1:4000/$1"' in config
    assert 'ProxyPassMatch "^(/blog(?:/.*)?)$" "http://127.0.0.1:5000$1"' in config
    assert 'RedirectMatch 302 "^/docs(/.*)?$" "https://docs.example.org$1"' in config
    assert config.index("ProxyPassMatch") < config.index("ProxyPass / http://127.0.0.1:3000/")


def test_apache_headers_per_prefix(apache: ApacheManager, store: NoustStore) -> None:
    policy = validated(SitePolicy(), {"routes": [{"path": "/api", "headers": {"X-Cors": "yes"}}]})

    config = _apache_site(apache, policy)

    api = config.split('<Location "/api">')[1].split("</Location>")[0]
    assert 'Header always set X-Cors "yes"' in api


def test_the_policy_context_of_a_site_without_routes_has_no_maps(tmp_path: Path) -> None:
    context = policy_context(SitePolicy(), domain="a.example.com", conf_root=tmp_path, kind="nginx")

    assert context["policy_maps"] == []
    assert response_headers(SitePolicy())


# -- the ports a prefix may be handed to ----------------------------------------


@pytest.mark.parametrize("port", [80, 443, 8080])
def test_a_prefix_is_never_handed_to_the_machines_own_services(machine: Machine, port: int) -> None:
    machine.deploy(app_type="nodejs")
    before = machine.config()

    with pytest.raises(ValidationError) as raised:
        access.set_policy(
            "example.com", {"routes": [{"path": "/x", "kind": "proxy", "port": port}]}
        )

    assert str(port) in raised.value.message
    assert raised.value.field == "routes"
    assert machine.config() == before
    assert SitePolicyManager().get("example.com").routes == ()


def test_ssh_is_one_of_those_services(machine: Machine, runner: Any) -> None:
    machine.deploy(app_type="nodejs")
    runner.script(
        ("ss", "-ltnpH"),
        stdout='LISTEN 0 128 0.0.0.0:2222 0.0.0.0:* users:(("sshd",pid=7,fd=3))\n',
    )

    with pytest.raises(ValidationError) as raised:
        access.set_policy(
            "example.com", {"routes": [{"path": "/x", "kind": "proxy", "port": 2222}]}
        )

    assert "SSH's" in raised.value.message
