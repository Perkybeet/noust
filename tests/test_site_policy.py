# ruff: noqa: F811
# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a site's policy is, and how it reaches the file the web server loads.

The values land in a file nginx loads as root, so most of what is pinned here is
refusals: a header value that could end its directive, an address that is not
one, a list that would lock the operator out, a combination that cannot work.
The rest is that the policy is rendered by whoever writes the site (the same
text from a redeploy, an alias and a certificate order), and that a change the
web server refuses puts everything back.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import App, DomainRecord, NoustStore
from noust.deployers import access, domains
from noust.managers import site_policy
from noust.managers.nginx_manager import NginxManager
from noust.managers.site_policy import (
    SitePolicy,
    SitePolicyManager,
    canonical_host,
    hash_password,
    locks_out,
    permits,
    policy_context,
    response_headers,
    site_names,
    validated,
)
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

HASH = "$6$salt$" + "a" * 86


# -- validation -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("allow_ips", ["not-an-address"]),
        ("allow_ips", "10.0.0.1"),
        ("deny_ips", ["10.0.0.0/33"]),
        ("rate_limit", "fast"),
        ("rate_limit", "0r/s"),
        ("rate_limit", "10r/h"),
        ("rate_burst", -1),
        ("rate_burst", True),
        ("connection_limit", 1.5),
        ("realm", 'Say "hi"'),
        ("realm", ""),
        ("access", "maybe"),
        ("real_ip", "everyone"),
        ("canonical", "both"),
        ("hsts", "yes"),
        ("noindex", 1),
        ("basic_auth", "on"),
    ],
)
def test_a_value_that_is_not_what_its_directive_takes_is_refused_by_field(
    field: str, value: Any
) -> None:
    with pytest.raises(ValidationError) as raised:
        validated(SitePolicy(), {field: value})

    assert raised.value.field == field


def test_an_unknown_field_is_refused_and_named() -> None:
    with pytest.raises(ValidationError) as raised:
        validated(SitePolicy(), {"allow": ["10.0.0.1"]})

    assert raised.value.field == "allow"
    assert "allow_ips" in raised.value.details


@pytest.mark.parametrize(
    "value",
    ['a"b', "a\\b", "a$b", "50%", "line\nbreak", "", "x" * 1025],
)
def test_a_header_value_that_could_end_its_directive_is_refused(value: str) -> None:
    with pytest.raises(ValidationError) as raised:
        validated(SitePolicy(), {"headers": {"Content-Security-Policy": value}})

    assert raised.value.field == "headers"


@pytest.mark.parametrize("name", ["Content-Length", "host", "Set-Cookie", "bad name", "a;b"])
def test_a_header_the_web_server_owns_or_that_is_not_a_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError):
        validated(SitePolicy(), {"headers": {name: "x"}})


def test_addresses_are_normalised_and_deduplicated() -> None:
    policy = validated(
        SitePolicy(), {"allow_ips": ["203.0.113.9/32", "203.0.113.9", "10.0.0.5/8", "2001:db8::1"]}
    )

    assert policy.allow_ips == ("203.0.113.9", "10.0.0.0/8", "2001:db8::1")


def test_a_custom_real_ip_needs_the_networks_and_the_header() -> None:
    with pytest.raises(ValidationError) as raised:
        validated(SitePolicy(), {"real_ip": "custom"})
    assert raised.value.field == "trusted_proxies"

    with pytest.raises(ValidationError) as raised:
        validated(SitePolicy(), {"real_ip": "custom", "trusted_proxies": ["10.0.0.0/8"]})
    assert raised.value.field == "real_ip_header"

    policy = validated(
        SitePolicy(),
        {"real_ip": "custom", "trusted_proxies": ["10.0.0.0/8"], "real_ip_header": "X-Real-IP"},
    )
    assert policy.real_ip == "custom"


def test_a_trusted_network_cannot_be_the_whole_internet() -> None:
    with pytest.raises(ValidationError) as raised:
        validated(
            SitePolicy(),
            {"real_ip": "custom", "trusted_proxies": ["0.0.0.0/0"], "real_ip_header": "X-Real-IP"},
        )

    assert "too wide" in raised.value.message


def test_either_way_in_needs_both_ways() -> None:
    with pytest.raises(ValidationError) as raised:
        validated(SitePolicy(), {"access": "any"})

    assert raised.value.field == "access"
    policy = validated(
        SitePolicy(), {"basic_auth": True, "allow_ips": ["10.0.0.0/8"], "access": "any"}
    )
    assert policy.access == "any"


# -- lists and the operator -----------------------------------------------------


def test_the_lists_are_evaluated_as_the_web_server_does() -> None:
    policy = validated(
        SitePolicy(), {"deny_ips": ["10.0.0.7"], "allow_ips": ["10.0.0.0/24", "203.0.113.0/24"]}
    )

    assert permits(policy, "10.0.0.8")
    assert not permits(policy, "10.0.0.7")  # denied before it can be allowed
    assert not permits(policy, "198.51.100.1")  # allow entries refuse everyone else
    assert permits(SitePolicy(), "198.51.100.1")


def test_a_list_that_refuses_the_operator_is_a_lockout_unless_a_password_is_a_way_in() -> None:
    lists = validated(SitePolicy(), {"allow_ips": ["203.0.113.0/24"]})
    either = validated(lists, {"basic_auth": True, "access": "any"})
    both = validated(lists, {"basic_auth": True})

    assert locks_out(lists, ["198.51.100.4", "203.0.113.9"]) == ["198.51.100.4"]
    assert locks_out(either, ["198.51.100.4"]) == []
    assert locks_out(both, ["198.51.100.4"]) == ["198.51.100.4"]
    assert locks_out(SitePolicy(), ["198.51.100.4"]) == []


# -- names ----------------------------------------------------------------------


def _record(domain: str, kind: str, port: int | None = None) -> DomainRecord:
    return DomainRecord(domain=domain, kind=kind, port=port)


def test_canonical_www_serves_www_and_redirects_the_bare_name() -> None:
    records = [_record("example.com", "primary"), _record("old.example.org", "redirect")]

    names = site_names("example.com", records, "www")

    assert names.served == ("www.example.com",)
    assert names.redirects == ("old.example.org", "example.com")
    assert names.target == "www.example.com"


def test_canonical_apex_on_a_www_primary_serves_the_bare_name() -> None:
    records = [_record("www.example.com", "primary"), _record("example.com", "redirect")]

    names = site_names("www.example.com", records, "apex")

    assert names.served == ("example.com",)
    assert names.redirects == ("www.example.com",)
    assert names.target == "example.com"


def test_without_a_canonical_spelling_the_names_are_as_recorded() -> None:
    records = [
        _record("example.com", "primary"),
        _record("shop.example.com", "alias"),
        _record("www.example.com", "redirect"),
    ]

    names = site_names("example.com", records, "")

    assert names.served == ("example.com", "shop.example.com")
    assert names.redirects == ("www.example.com",)
    assert names.target == "example.com"
    assert canonical_host("example.com", "") == "example.com"


def test_a_name_with_its_own_port_is_reported_apart() -> None:
    records = [_record("example.com", "primary"), _record("admin.example.com", "alias", 9000)]

    assert site_names("example.com", records).ports == {"admin.example.com": 9000}


# -- headers --------------------------------------------------------------------


def test_the_default_headers_are_overridden_removed_and_added_to() -> None:
    policy = validated(
        SitePolicy(),
        {
            "headers": {
                "x-frame-options": "DENY",
                "Referrer-Policy": None,
                "Permissions-Policy": "geolocation=()",
            },
            "noindex": True,
        },
    )

    headers = dict(response_headers(policy, ssl=False))

    assert headers["X-Frame-Options"] == "DENY"
    assert "Referrer-Policy" not in headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Permissions-Policy"] == "geolocation=()"
    assert headers["X-Robots-Tag"] == "noindex, nofollow"


def test_hsts_follows_the_server_setting_unless_the_site_decides() -> None:
    server_wide = "max-age=31536000"

    assert "Strict-Transport-Security" in dict(
        response_headers(SitePolicy(), hsts=server_wide, ssl=True)
    )
    assert "Strict-Transport-Security" not in dict(
        response_headers(SitePolicy(), hsts=server_wide, ssl=False)
    )
    assert "Strict-Transport-Security" not in dict(
        response_headers(SitePolicy(hsts=False), hsts=server_wide, ssl=True)
    )
    assert dict(response_headers(SitePolicy(hsts=True), hsts="", ssl=True))[
        "Strict-Transport-Security"
    ] == ("max-age=31536000")


# -- the passwords --------------------------------------------------------------


def test_a_password_reaches_openssl_on_its_input_and_never_in_its_arguments(
    runner: FakeRunner,
) -> None:
    runner.script(["openssl", "passwd", "-6", "-stdin"], stdout=HASH + "\n")

    assert hash_password("correct horse battery", runner) == HASH

    (call,) = runner.calls
    assert call == ("openssl", "passwd", "-6", "-stdin")
    assert "correct horse battery" not in " ".join(call)


@pytest.mark.parametrize("password", ["short", "x" * 257, "two\nlines", "tab\there1"])
def test_a_password_that_cannot_be_one_is_refused_before_openssl(
    runner: FakeRunner, password: str
) -> None:
    with pytest.raises(ValidationError):
        hash_password(password, runner)

    assert runner.calls == []


# -- rendering ------------------------------------------------------------------


def _store_users(policies: SitePolicyManager, domain: str, *names: str) -> None:
    for name in names:
        policies.records.set_user(domain, name, HASH, created_by="test")


def test_a_policy_is_rendered_into_the_site_and_survives_a_rewrite(
    machine: Machine, store: NoustStore
) -> None:
    machine.deploy(app_type="nodejs")
    policies = SitePolicyManager()
    _store_users(policies, "example.com", "ana")
    policies.save(
        "example.com",
        validated(
            SitePolicy(),
            {
                "basic_auth": True,
                "realm": "Staging",
                "allow_ips": ["203.0.113.0/24"],
                "rate_limit": "10r/s",
                "rate_burst": 30,
                "connection_limit": 20,
                "headers": {"X-Frame-Options": "DENY"},
                "noindex": True,
            },
        ),
        actor="test",
    )

    domains.refresh_site(store.get_app("example.com"))
    first = machine.config()
    domains.add_domain("example.com", "shop.example.com", "alias")
    again = machine.config()

    for config in (first, again):
        assert 'auth_basic "Staging";' in config
        assert "auth_basic_user_file" in config
        assert "satisfy all;" in config
        assert "allow 203.0.113.0/24;" in config
        assert "deny all;" in config
        assert "limit_req zone=example_com_site burst=30 nodelay;" in config
        assert "limit_conn example_com_conn 20;" in config
        assert 'add_header X-Frame-Options "DENY" always;' in config
        assert 'add_header X-Robots-Tag "noindex, nofollow" always;' in config
    assert "limit_req_zone $binary_remote_addr zone=example_com_site:10m rate=10r/s;" in first
    assert "limit_conn_zone $binary_remote_addr zone=example_com_conn:10m;" in first
    # An alias added afterwards is served with the same policy.
    assert "server_name example.com shop.example.com;" in again


def test_the_accounts_file_is_derived_from_the_store_and_readable_only_by_the_web_server(
    machine: Machine, store: NoustStore, web: NginxManager, ownership_changes: list[Any]
) -> None:
    machine.deploy(app_type="nodejs")
    policies = SitePolicyManager()
    _store_users(policies, "example.com", "ana", "ben")
    policies.save("example.com", SitePolicy(basic_auth=True), actor="test")

    domains.refresh_site(store.get_app("example.com"))

    path = web.sites_available.parent / "noust-auth" / "example-com.users"
    assert path.read_text() == f"ana:{HASH}\nben:{HASH}\n"
    assert path.stat().st_mode & 0o777 == 0o640
    assert [(c.path, c.user, c.mode) for c in ownership_changes if c.path == path] == [
        (path, "root", 0o640)
    ]
    assert str(path) in machine.config()

    policies.save("example.com", SitePolicy(), actor="test")
    domains.refresh_site(store.get_app("example.com"))

    assert not path.exists()
    assert "auth_basic" not in machine.config()


def test_a_site_that_asks_for_a_password_and_has_no_accounts_is_not_written(
    machine: Machine, store: NoustStore
) -> None:
    machine.deploy(app_type="nodejs")
    before = machine.config()
    SitePolicyManager().save("example.com", SitePolicy(basic_auth=True), actor="test")

    with pytest.raises(ValidationError) as raised:
        domains.refresh_site(store.get_app("example.com"))

    assert raised.value.field == "basic_auth"
    assert machine.config() == before


def test_the_certificate_challenge_stays_open_to_the_authority(
    machine: Machine, store: NoustStore
) -> None:
    machine.deploy(app_type="nodejs")
    policies = SitePolicyManager()
    _store_users(policies, "example.com", "ana")
    policies.save("example.com", SitePolicy(basic_auth=True), actor="test")

    domains.refresh_site(store.get_app("example.com"))

    config = machine.config()
    challenge = config.split("location /.well-known/acme-challenge/ {")[1].split("}")[0]
    assert "auth_basic off;" in challenge
    assert "allow all;" in challenge


def test_the_client_address_is_believed_only_from_cloudflare_when_asked(
    machine: Machine, store: NoustStore
) -> None:
    machine.deploy(app_type="nodejs")
    SitePolicyManager().save("example.com", SitePolicy(real_ip="cloudflare"), actor="test")

    domains.refresh_site(store.get_app("example.com"))

    config = machine.config()
    assert "set_real_ip_from 173.245.48.0/20;" in config
    assert "set_real_ip_from 2606:4700::/32;" in config
    assert "real_ip_header CF-Connecting-IP;" in config
    assert "real_ip_recursive" not in config


def test_the_redirect_goes_to_the_canonical_host_and_the_certificate_covers_both(
    machine: Machine, store: NoustStore
) -> None:
    machine.deploy(app_type="nodejs")
    SitePolicyManager().save("example.com", SitePolicy(canonical="www"), actor="test")

    domains.add_domain("example.com", "www.example.com", "alias")

    config = machine.config()
    assert "server_name www.example.com;" in config
    assert "server_name example.com;" in config
    assert "return 301 http://www.example.com$request_uri;" in config


def test_a_policy_renders_nothing_for_a_site_without_one(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    config = machine.config()

    for text in ("auth_basic", "limit_req", "limit_conn", "set_real_ip_from", "satisfy"):
        assert text not in config
    assert 'add_header X-Frame-Options "SAMEORIGIN" always;' in config


def test_apache_gets_the_password_and_the_lists_in_one_location(tmp_path: Path) -> None:
    policy = validated(
        SitePolicy(),
        {"basic_auth": True, "allow_ips": ["203.0.113.0/24"], "deny_ips": ["203.0.113.66"]},
    )

    context = policy_context(policy, domain="example.com", conf_root=tmp_path, kind="apache")

    block = context["policy"]
    assert block["auth"]["file"] == str(tmp_path / "noust-auth" / "example-com.users")
    assert block["require"] == [
        "<RequireAll>",
        "    Require valid-user",
        "    <RequireAny>",
        "        Require ip 203.0.113.0/24",
        "    </RequireAny>",
        "    Require not ip 203.0.113.66",
        "</RequireAll>",
    ]
    assert context["policy_zones"] == []


def test_limits_are_nginx_only(tmp_path: Path) -> None:
    policy = validated(SitePolicy(), {"rate_limit": "5r/s", "connection_limit": 3})

    nginx = policy_context(policy, domain="a.example.com", conf_root=tmp_path, kind="nginx")
    apache = policy_context(policy, domain="a.example.com", conf_root=tmp_path, kind="apache")

    assert [zone["name"] for zone in nginx["policy_zones"]] == [
        "a_example_com_site",
        "a_example_com_conn",
    ]
    assert "rate" not in apache["policy"]
    assert "conn" not in apache["policy"]


# -- changing a policy ----------------------------------------------------------


@pytest.fixture
def served(machine: Machine, store: NoustStore) -> NoustStore:
    machine.deploy(app_type="nodejs")
    return store


def test_setting_a_policy_renders_the_site_and_is_audited(
    machine: Machine, served: NoustStore, runner: FakeRunner
) -> None:
    view = access.set_policy("example.com", {"rate_limit": "10r/s", "noindex": True})

    assert view.policy.rate_limit == "10r/s"
    assert view.applicable
    assert "limit_req zone=example_com_site" in machine.config()
    assert machine.reloaded()
    assert SitePolicyManager().get("example.com").noindex is True


def test_the_web_server_refusing_the_site_puts_the_policy_back(
    machine: Machine, served: NoustStore, runner: FakeRunner
) -> None:
    before = machine.config()
    runner.script(["nginx", "-t"], stderr="nginx: [emerg] unknown directive\n", exit_code=1)

    with pytest.raises(ValidationError):
        access.set_policy("example.com", {"rate_limit": "10r/s"})

    assert SitePolicyManager().get("example.com").is_default
    assert machine.config() == before


def test_a_list_that_refuses_the_operator_is_refused_unless_they_accept(
    machine: Machine, served: NoustStore
) -> None:
    with pytest.raises(ValidationError) as raised:
        access.set_policy(
            "example.com", {"allow_ips": ["203.0.113.0/24"]}, operator_addresses=["198.51.100.4"]
        )

    assert "198.51.100.4" in raised.value.message
    assert SitePolicyManager().get("example.com").is_default

    access.set_policy(
        "example.com",
        {"allow_ips": ["203.0.113.0/24"]},
        operator_addresses=["198.51.100.4"],
        accept_lockout=True,
    )

    assert SitePolicyManager().get("example.com").allow_ips == ("203.0.113.0/24",)


def test_a_password_needs_an_account_first(machine: Machine, served: NoustStore) -> None:
    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"basic_auth": True})

    assert raised.value.field == "basic_auth"
    assert "app policy user add" in raised.value.details


def test_accounts_are_hashed_listed_and_the_last_one_cannot_go_while_a_password_is_asked(
    machine: Machine, served: NoustStore, runner: FakeRunner
) -> None:
    runner.script(["openssl", "passwd", "-6", "-stdin"], stdout=HASH + "\n")

    access.set_user("example.com", "ana", "correct horse battery")
    view = access.set_policy("example.com", {"basic_auth": True})

    assert view.users == ("ana",)
    assert (
        "ana:" in (machine.web.sites_available.parent / "noust-auth/example-com.users").read_text()
    )
    with pytest.raises(ValidationError) as raised:
        access.remove_user("example.com", "ana")
    assert "only account" in raised.value.message

    access.set_policy("example.com", {"basic_auth": False})
    assert access.remove_user("example.com", "ana").users == ()


def test_the_password_is_never_stored_or_audited(
    machine: Machine, served: NoustStore, runner: FakeRunner
) -> None:
    runner.script(["openssl", "passwd", "-6", "-stdin"], stdout=HASH + "\n")

    access.set_user("example.com", "ana", "correct horse battery")

    (user,) = SitePolicyManager().users("example.com")
    assert user.password_hash == HASH
    assert "correct horse battery" not in repr(user)


def test_a_site_the_operator_wrote_is_not_given_a_policy_that_would_do_nothing(
    machine: Machine, served: NoustStore
) -> None:
    path = machine.web.config_path("example.com")
    path.write_text("server { listen 80; server_name example.com; }\n")

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"noindex": True})

    assert "written by hand" in raised.value.message
    assert access.describe("example.com").applicable is False


def test_apache_cannot_limit_requests(machine: Machine, served: NoustStore) -> None:
    app = served.get_app("example.com")
    assert app is not None
    app.webserver = "apache"
    served.update_app(app)

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"rate_limit": "10r/s"})

    assert raised.value.field == "rate_limit"
    assert "nginx" in raised.value.message


def test_a_bare_name_with_no_www_spelling_cannot_be_made_canonical(
    machine: Machine, served: NoustStore
) -> None:
    served.create_app(App(domain="intranet", app_type="static", app_path="/x", source="/x"))

    with pytest.raises(ValidationError) as raised:
        site_policy.check_canonical_possible("intranet")

    assert raised.value.field == "canonical"


def test_resetting_goes_back_to_the_defaults_and_keeps_the_accounts(
    machine: Machine, served: NoustStore
) -> None:
    policies = SitePolicyManager()
    _store_users(policies, "example.com", "ana")
    access.set_policy("example.com", {"noindex": True, "rate_limit": "5r/s"})

    view = access.reset_policy("example.com")

    assert view.policy.is_default
    assert view.users == ("ana",)
    assert "limit_req" not in machine.config()
