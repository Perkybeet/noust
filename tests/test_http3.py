# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811

"""
HTTP/3 for the sites that ask for it.

A site's policy says ``http3``; nginx has to be new enough and built with the module, one small site
of Noust's own holds the one ``reuseport`` socket, and the firewall has to let UDP 443 in or browsers
fall back to HTTP/2. Pinned here: what each check says, that the socket's site is written with the
first site and taken away with the last, that a change nginx refuses leaves nothing behind, and what
the site's file looks like with and without it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.runner import FakeRunner
from noust.deployers import access
from noust.managers.http3 import ANCHOR_NAME, Http3, parse_nginx_v
from noust.managers.nginx_manager import NginxManager
from noust.managers.server.security_firewall import FirewallRule, FirewallState
from noust.managers.site_policy import SitePolicy, SitePolicyManager, validated
from tests.test_domains import (  # noqa: F401 - fixtures of the domains harness
    Machine,
    certs,
    machine,
    store,
    web,
)

NEW = "nginx version: nginx/1.26.2\nbuilt with OpenSSL 3.0.13\nconfigure arguments: --with-http_ssl_module --with-http_v3_module\n"
OLD = "nginx version: nginx/1.24.0 (Ubuntu)\nconfigure arguments: --with-http_ssl_module --with-http_v2_module\n"
NO_MODULE = "nginx version: nginx/1.26.2\nconfigure arguments: --with-http_ssl_module\n"


class FakeFirewall:
    """A firewall that says what it was told."""

    def __init__(self, state: FirewallState) -> None:
        self._state = state

    def state(self) -> FirewallState:
        return self._state


def udp_rule() -> FirewallRule:
    return FirewallRule(
        id="r",
        backend="ufw",
        action="allow",
        ports=((443, 443),),
        proto="udp",
        source="any",
        spec="allow 443/udp",
    )


# -- what nginx -V says ----------------------------------------------------------


def test_the_version_and_the_module_are_read_from_nginx_v() -> None:
    assert parse_nginx_v(NEW) == ((1, 26, 2), True)
    assert parse_nginx_v(OLD) == ((1, 24, 0), False)
    assert parse_nginx_v(NO_MODULE) == ((1, 26, 2), False)
    assert parse_nginx_v("nothing") == ((), False)


def support_of(web: NginxManager, runner: FakeRunner, output: str, firewall: Any = None) -> Any:
    runner.script(("nginx", "-V"), stderr=output)
    return Http3(web, firewall).support()


def test_a_new_nginx_with_the_module_can_serve_http3(web: NginxManager, runner: FakeRunner) -> None:
    support = support_of(web, runner, NEW)

    assert support.available and support.version == "1.26.2" and support.problem == ""


@pytest.mark.parametrize(
    ("output", "words"),
    [
        (OLD, "older than 1.25.0"),
        (NO_MODULE, "--with-http_v3_module"),
        ("junk", "could not be read"),
    ],
)
def test_an_nginx_that_cannot_says_why_and_how_to_fix_it(
    web: NginxManager, runner: FakeRunner, output: str, words: str
) -> None:
    support = support_of(web, runner, output)

    assert not support.available
    assert words in support.problem and support.hint


def test_apache_has_no_http3(tmp_path: Any, runner: FakeRunner) -> None:
    from dataclasses import replace

    from noust.managers.apache_manager import ApacheManager
    from noust.managers.webserver import APACHE_BACKEND

    manager = ApacheManager(
        backend=replace(
            APACHE_BACKEND, sites_available=tmp_path / "a", sites_enabled=tmp_path / "e"
        )
    )

    support = Http3(manager).support()

    assert not support.available and "Apache" in support.problem


def test_the_firewall_is_asked_about_udp_443(web: NginxManager, runner: FakeRunner) -> None:
    closed = FakeFirewall(FirewallState(backend="ufw", active=True))
    opened = FakeFirewall(FirewallState(backend="ufw", active=True, rules=[udp_rule()]))
    absent = FakeFirewall(FirewallState(backend="none", active=False))

    shut = support_of(web, runner, NEW, closed)
    allowed = support_of(web, runner, NEW, opened)
    nothing = support_of(web, runner, NEW, absent)

    assert (shut.udp_allowed, shut.firewall_hint) == (False, "ufw allow 443/udp")
    assert allowed.udp_allowed is True and allowed.firewall_hint == ""
    assert nothing.udp_allowed is None


def test_a_firewall_that_lets_everything_in_by_default_does_not_close_udp_443(
    web: NginxManager, runner: FakeRunner
) -> None:
    """
    nftables rules in place with a default of accept (Docker's, on a plain server) filter
    nothing, and the answer used to be "the firewall does not let UDP 443 in" with no command to
    open anything. Found on a real Debian 13 with Docker installed.
    """
    open_by_default = FakeFirewall(
        FirewallState(backend="nftables", active=True, default_incoming="allow")
    )
    closed = FakeFirewall(FirewallState(backend="nftables", active=True, default_incoming="drop"))

    wide_open = support_of(web, runner, NEW, open_by_default)
    shut = support_of(web, runner, NEW, closed)

    assert wide_open.udp_allowed is True and wide_open.firewall_hint == ""
    assert shut.udp_allowed is False
    assert "443/udp" in shut.firewall_hint  # even a firewall Noust cannot drive says what to open


def test_a_rule_that_refuses_udp_443_is_not_a_way_in(web: NginxManager, runner: FakeRunner) -> None:
    from dataclasses import replace

    refused = FakeFirewall(
        FirewallState(
            backend="ufw",
            active=True,
            default_incoming="allow",
            rules=[replace(udp_rule(), action="deny")],
        )
    )

    assert support_of(web, runner, NEW, refused).udp_allowed is False


# -- the socket's own site --------------------------------------------------------


def test_the_socket_site_holds_reuseport_once_and_refuses_unknown_names(
    web: NginxManager, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-V"), stderr=NEW)
    http3 = Http3(web, FakeFirewall(FirewallState(backend="none")))

    wrote = http3.ensure_anchor()
    again = http3.ensure_anchor()

    text = (web.backend.sites_available / ANCHOR_NAME).read_text()
    assert wrote is True and again is False
    assert text.count("reuseport") >= 2 and "listen 443 quic reuseport;" in text
    assert "ssl_reject_handshake on;" in text and "Generated by Noust" in text
    assert (web.backend.sites_enabled / ANCHOR_NAME).is_symlink()
    assert http3.anchored()


def test_the_socket_site_is_not_written_where_http3_is_not_available(
    web: NginxManager, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-V"), stderr=OLD)

    with pytest.raises(ValidationError) as raised:
        Http3(web, FakeFirewall(FirewallState(backend="none"))).ensure_anchor()

    assert raised.value.field == "http3" and "older than 1.25.0" in raised.value.message
    assert not (web.backend.sites_available / ANCHOR_NAME).exists()


def test_a_socket_site_nginx_refuses_is_put_away(web: NginxManager, runner: FakeRunner) -> None:
    runner.script(("nginx", "-V"), stderr=NEW)
    runner.script(("nginx", "-t"), stderr="nginx: [emerg] unknown directive quic\n", exit_code=1)

    with pytest.raises(ValidationError) as raised:
        Http3(web, FakeFirewall(FirewallState(backend="none"))).ensure_anchor()

    assert "unknown directive quic" in raised.value.details
    assert not (web.backend.sites_available / ANCHOR_NAME).exists()
    assert not (web.backend.sites_enabled / ANCHOR_NAME).is_symlink()


def test_the_socket_site_is_not_an_applications(web: NginxManager, runner: FakeRunner) -> None:
    runner.script(("nginx", "-V"), stderr=NEW)
    Http3(web, FakeFirewall(FirewallState(backend="none"))).ensure_anchor()

    assert web.list_sites() == []


# -- a site's policy --------------------------------------------------------------


def serve(
    machine: Machine, runner: FakeRunner, *, domain: str = "example.com", tls: bool = True
) -> None:
    runner.script(("nginx", "-V"), stderr=NEW)
    machine.deploy(domain, app_type="nodejs", tls=tls)


def test_a_site_over_http3_listens_on_quic_and_says_so(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)

    access.set_policy("example.com", {"http3": True})

    config = machine.config()
    assert "listen 443 quic;" in config and "listen [::]:443 quic;" in config
    assert "listen 443 quic reuseport" not in config  # the socket's site holds it, once
    assert """add_header Alt-Svc 'h3=":443"; ma=86400' always;""" in config
    assert "http3 on;" in config
    anchor = machine.web.backend.sites_available / ANCHOR_NAME
    assert "listen 443 quic reuseport;" in anchor.read_text()


def test_a_site_without_tls_has_nothing_to_serve_over_quic(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner, tls=False)

    access.set_policy("example.com", {"http3": True})

    assert "quic" not in machine.config() and "Alt-Svc" not in machine.config()


def test_a_site_without_the_policy_renders_nothing_of_it(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)

    for text in ("quic", "Alt-Svc", "http3"):
        assert text not in machine.config()


def test_the_socket_site_stays_until_the_last_site_stops(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)
    machine.deploy("other.example.org", app_type="nodejs", tls=True)
    anchor = machine.web.backend.sites_available / ANCHOR_NAME
    access.set_policy("example.com", {"http3": True})
    access.set_policy("other.example.org", {"http3": True})
    first_text = anchor.read_text()

    access.set_policy("example.com", {"http3": False})
    assert anchor.exists() and anchor.read_text() == first_text
    assert "quic" not in machine.config()

    access.set_policy("other.example.org", {"http3": False})
    assert not anchor.exists()
    assert not (machine.web.backend.sites_enabled / ANCHOR_NAME).is_symlink()


def reloads(runner: FakeRunner) -> int:
    return runner.calls.count(("systemctl", "reload", "nginx"))


def test_nginx_is_reloaded_after_the_socket_site_goes_so_the_socket_closes(
    machine: Machine, runner: FakeRunner
) -> None:
    """
    The site was reloaded without HTTP/3 while the socket's site was still there, and the socket's
    site was removed afterwards with no reload: on a real nginx 28 UDP 443 sockets stayed open
    (and the handshake-refusing server block stayed loaded) until some later reload.
    """
    serve(machine, runner)
    machine.deploy("other.example.org", app_type="nodejs", tls=True)
    access.set_policy("example.com", {"http3": True})
    access.set_policy("other.example.org", {"http3": True})

    runner.calls.clear()
    access.set_policy("example.com", {"http3": False})
    assert reloads(runner) == 1  # the site only: the socket is still wanted

    runner.calls.clear()
    access.set_policy("other.example.org", {"http3": False})
    assert reloads(runner) == 2  # the site, then the socket's own site going away
    anchor = machine.web.backend.sites_available / ANCHOR_NAME
    assert not anchor.exists()


def test_removing_the_last_site_over_http3_takes_the_socket_site_and_closes_the_socket(
    machine: Machine, runner: FakeRunner
) -> None:
    """The socket's site outlived every site that used it when the application was removed."""
    serve(machine, runner)
    access.set_policy("example.com", {"http3": True})
    anchor = machine.web.backend.sites_available / ANCHOR_NAME
    assert anchor.exists()
    runner.calls.clear()

    access.forget("example.com")

    assert not anchor.exists()
    assert not (machine.web.backend.sites_enabled / ANCHOR_NAME).is_symlink()
    assert reloads(runner) == 1


def test_removing_a_site_while_another_still_uses_http3_keeps_the_socket_site(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)
    machine.deploy("other.example.org", app_type="nodejs", tls=True)
    access.set_policy("example.com", {"http3": True})
    access.set_policy("other.example.org", {"http3": True})
    runner.calls.clear()

    access.forget("example.com")

    assert (machine.web.backend.sites_available / ANCHOR_NAME).exists()
    assert reloads(runner) == 0


def test_removing_a_site_that_never_used_http3_asks_nginx_nothing(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)
    runner.calls.clear()

    access.forget("example.com")

    assert reloads(runner) == 0


def test_resetting_the_policy_of_the_last_site_removes_the_socket_site(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)
    access.set_policy("example.com", {"http3": True})

    access.reset_policy("example.com")

    assert not (machine.web.backend.sites_available / ANCHOR_NAME).exists()


def test_a_server_that_cannot_refuses_the_policy_and_changes_nothing(
    machine: Machine, runner: FakeRunner
) -> None:
    runner.script(("nginx", "-V"), stderr=OLD)
    machine.deploy(app_type="nodejs", tls=True)
    before = machine.config()

    with pytest.raises(ValidationError) as raised:
        access.set_policy("example.com", {"http3": True})

    assert raised.value.field == "http3"
    assert machine.config() == before
    assert SitePolicyManager().get("example.com").http3 is False
    assert not (machine.web.backend.sites_available / ANCHOR_NAME).exists()


def test_a_site_nginx_refuses_takes_the_socket_site_with_it(
    machine: Machine, runner: FakeRunner
) -> None:
    serve(machine, runner)
    runner.script(("nginx", "-t"), stderr="nginx: [emerg] bad\n", exit_code=1)

    with pytest.raises(ValidationError):
        access.set_policy("example.com", {"http3": True})

    assert not (machine.web.backend.sites_available / ANCHOR_NAME).exists()
    assert SitePolicyManager().get("example.com").http3 is False


def test_the_policy_keeps_the_flag() -> None:
    policy = validated(SitePolicy(), {"http3": True})

    assert policy.http3 and policy.to_dict()["http3"] is True
    assert not policy.is_default
    with pytest.raises(ValidationError):
        validated(SitePolicy(), {"http3": "yes"})


def test_apache_is_refused_by_the_chokepoint(monkeypatch: pytest.MonkeyPatch) -> None:
    apache_app = SimpleNamespace(webserver="apache", domain="example.com", app_type="nodejs")
    monkeypatch.setattr(access, "_unsupported", lambda app, verbose: "")

    with pytest.raises(ValidationError) as raised:
        access._require_applicable(apache_app, SitePolicy(http3=True), verbose=False)  # type: ignore[arg-type]

    assert raised.value.field == "http3" and "Apache" in raised.value.message
