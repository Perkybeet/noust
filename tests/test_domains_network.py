# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later
# ruff: noqa: F811

"""
The networking half of an application's names: wildcards, overlaps, which spelling is
served, a port per name, the certificate that follows the names, DNS, and the check
that asks the public DNS.

Run on the same harness as ``tests/test_domains.py`` - the real store, the real nginx
manager writing into a temporary tree, the real deployers - with only processes faked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DomainConflictError, DomainError, ValidationError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.deployers import domains
from noust.managers.dns import records as records_module
from noust.managers.dns.records import ManagedDns
from noust.managers.site_policy import SitePolicyManager
from tests.dns_support import dns_answer
from tests.test_domains import (  # noqa: F401 - the harness
    Machine,
    certs,
    machine,
    put_certificate,
    requested_names,
    store,
    web,
)

HERE = ("203.0.113.5",)
CERTIFICATES = (
    "  Certificate Name: example.com\n"
    "    Domains: {names}\n"
    "    Expiry Date: 2026-11-30 12:00:00+00:00 (VALID: 60 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/example.com/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/example.com/privkey.pem\n"
)


def other_site(web_manager: Any, name: str, text: str) -> None:
    """Write a site into the web server's tree as an operator would, and enable it."""
    web_manager.sites_available.mkdir(parents=True, exist_ok=True)
    web_manager.sites_enabled.mkdir(parents=True, exist_ok=True)
    (web_manager.sites_available / name).write_text(text)
    (web_manager.sites_enabled / name).symlink_to(web_manager.sites_available / name)


# -- wildcards --------------------------------------------------------------------


def test_a_wildcard_alias_is_served_beside_the_names_it_covers(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    change = domains.add_domain("example.com", "*.example.com", dns=False)

    assert machine.domains() == [("example.com", "primary"), ("*.example.com", "alias")]
    assert "server_name example.com *.example.com;" in machine.config()
    assert change.tls is False


def test_a_wildcard_is_served_not_redirected(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    with pytest.raises(DomainError):
        domains.add_domain("example.com", "*.example.com", "redirect", dns=False)


@pytest.mark.parametrize("name", ["*", "*.", "*.*.example.com", "a*.example.com", "*example.com"])
def test_a_name_with_a_misplaced_star_is_not_a_name(machine: Machine, name: str) -> None:
    machine.deploy(app_type="nodejs")

    with pytest.raises(DomainError):
        domains.add_domain("example.com", name, dns=False)


def test_a_wildcard_on_a_site_with_tls_asks_for_the_dns_challenge_not_a_port(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs", tls=True)

    change = domains.add_domain("example.com", "*.example.com", dns=False)

    assert ("*.example.com", "alias") in machine.domains()
    assert change.certificate_issued is False
    assert change.certificate_error is not None
    assert "noust cert use example.com --acme --dns NAME" in change.certificate_error
    assert machine.certbot_orders() == []


# -- overlaps ---------------------------------------------------------------------


def test_a_name_another_site_answers_on_is_a_conflict_until_forced(
    machine: Machine, web: Any
) -> None:
    machine.deploy(app_type="nodejs")
    other_site(web, "legacy", "server {\n    listen 80;\n    server_name shop.example.com;\n}\n")

    with pytest.raises(DomainConflictError) as raised:
        domains.add_domain("example.com", "shop.example.com", dns=False)

    assert "legacy" in raised.value.details and "force" in raised.value.details
    assert ("shop.example.com", "alias") not in machine.domains()

    domains.add_domain("example.com", "shop.example.com", force=True, dns=False)

    assert ("shop.example.com", "alias") in machine.domains()


def test_a_wildcard_another_site_holds_is_named_when_a_name_under_it_is_added(
    machine: Machine, web: Any
) -> None:
    machine.deploy(app_type="nodejs")
    other_site(web, "legacy", "server {\n    listen 80;\n    server_name *.example.com;\n}\n")

    with pytest.raises(DomainConflictError) as raised:
        domains.add_domain("example.com", "shop.example.com", dns=False)

    assert "*.example.com" in raised.value.details
    assert "takes those requests" in raised.value.details


def test_a_wildcard_over_a_neighbours_name_is_flagged_but_the_neighbour_keeps_it(
    machine: Machine, web: Any
) -> None:
    machine.deploy(app_type="nodejs")
    other_site(web, "legacy", "server {\n    listen 80;\n    server_name blog.example.com;\n}\n")

    with pytest.raises(DomainConflictError) as raised:
        domains.add_domain("example.com", "*.example.com", dns=False)

    assert "keeps its name" in raised.value.details


def test_a_site_that_is_not_enabled_or_not_overlapping_is_no_conflict(
    machine: Machine, web: Any
) -> None:
    machine.deploy(app_type="nodejs")
    other_site(web, "elsewhere", "server {\n    listen 80;\n    server_name other.org;\n}\n")
    web.sites_available.joinpath("off").write_text(
        "server {\n    server_name shop.example.com;\n}\n"
    )

    domains.add_domain("example.com", "shop.example.com", dns=False)

    assert ("shop.example.com", "alias") in machine.domains()


def test_retrying_a_name_the_application_already_has_is_not_a_conflict_with_itself(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs")
    domains.add_domain("example.com", "shop.example.com", dns=False)

    domains.add_domain("example.com", "shop.example.com", dns=False)

    assert machine.domains().count(("shop.example.com", "alias")) == 1


# -- which spelling is served ------------------------------------------------------


def test_choosing_www_serves_it_redirects_the_bare_name_and_records_both(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    change = domains.set_canonical("example.com", "www")

    assert machine.domains() == [
        ("example.com", "primary"),
        ("www.example.com", "alias"),
    ]
    config = machine.config()
    assert "server_name www.example.com;" in config
    assert "server_name example.com;" in config
    assert "return 301 http://www.example.com$request_uri;" in config
    assert SitePolicyManager().get("example.com").canonical == "www"
    assert change.canonical == "www"


def test_choosing_the_bare_name_on_a_www_application_does_the_mirror(machine: Machine) -> None:
    machine.deploy("www.example.com", app_type="nodejs")

    domains.set_canonical("www.example.com", "apex")

    assert machine.domains("www.example.com") == [
        ("www.example.com", "primary"),
        ("example.com", "alias"),
    ]
    assert "return 301 http://example.com$request_uri;" in machine.config("www.example.com")


def test_the_pair_is_in_the_certificate_and_the_redirect_is_served_over_tls(
    machine: Machine,
) -> None:
    machine.deploy(app_type="nodejs", tls=True)

    change = domains.set_canonical("example.com", "www")

    (order,) = machine.certbot_orders()
    assert requested_names(order) == ["example.com", "www.example.com"]
    assert change.certificate_issued is True
    config = machine.config()
    assert "return 301 https://www.example.com$request_uri;" in config


def test_choosing_none_serves_both_names_as_recorded(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    domains.set_canonical("example.com", "www")

    domains.set_canonical("example.com", "none")

    assert SitePolicyManager().get("example.com").canonical == ""
    assert "server_name example.com www.example.com;" in machine.config()


def test_the_canonical_host_cannot_be_removed_from_under_the_site(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    domains.set_canonical("example.com", "www")

    with pytest.raises(DomainError) as raised:
        domains.remove_domain("example.com", "www.example.com")

    assert "noust domain canonical" in raised.value.details


def test_a_refused_site_puts_the_rows_and_the_policy_back(
    machine: Machine, runner: FakeRunner
) -> None:
    machine.deploy(app_type="nodejs")
    before = machine.config()
    runner.script(["nginx", "-t"], stderr="nginx: [emerg] boom\n", exit_code=1)

    with pytest.raises(ValidationError):
        domains.set_canonical("example.com", "www")

    assert machine.domains() == [("example.com", "primary")]
    assert SitePolicyManager().get("example.com").canonical == ""
    assert machine.config() == before


def test_a_name_with_no_www_spelling_is_refused(machine: Machine, store: NoustStore) -> None:
    machine.deploy("intranet", app_type="nodejs")

    with pytest.raises(ValidationError) as raised:
        domains.set_canonical("intranet", "www")

    assert raised.value.field == "canonical"


# -- a port for a name -------------------------------------------------------------


def test_a_name_can_be_sent_to_another_port_of_the_application(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    domains.add_domain("example.com", "admin.example.com", port=9000, dns=False)

    config = machine.config()
    assert "server_name example.com;" in config
    assert "server_name admin.example.com;" in config
    assert "proxy_pass http://127.0.0.1:3000;" in config
    assert "proxy_pass http://127.0.0.1:9000;" in config
    assert [(r.domain, r.port) for r in machine.store.list_domains("example.com")][-1] == (
        "admin.example.com",
        9000,
    )


def test_the_port_of_a_name_is_changed_and_cleared_later(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")
    domains.add_domain("example.com", "admin.example.com", dns=False)

    domains.set_domain_port("example.com", "admin.example.com", 9100)
    assert "proxy_pass http://127.0.0.1:9100;" in machine.config()

    domains.set_domain_port("example.com", "admin.example.com", None)
    assert "9100" not in machine.config()
    assert "server_name example.com admin.example.com;" in machine.config()


def test_a_refused_port_goes_back_to_the_one_before(machine: Machine, runner: FakeRunner) -> None:
    machine.deploy(app_type="nodejs")
    domains.add_domain("example.com", "admin.example.com", port=9000, dns=False)
    runner.script(["nginx", "-t"], stderr="nginx: [emerg] boom\n", exit_code=1)

    with pytest.raises(ValidationError):
        domains.set_domain_port("example.com", "admin.example.com", 9100)

    assert {r.domain: r.port for r in machine.store.list_domains("example.com")}[
        "admin.example.com"
    ] == 9000


@pytest.mark.parametrize("port", [0, 70000, -1])
def test_a_port_that_is_not_one_is_refused(machine: Machine, port: int) -> None:
    machine.deploy(app_type="nodejs")

    with pytest.raises(ValidationError):
        domains.add_domain("example.com", "admin.example.com", port=port, dns=False)


def test_an_application_that_proxies_nothing_has_no_port_to_choose(machine: Machine) -> None:
    machine.deploy(app_type="static")

    with pytest.raises(ValidationError) as raised:
        domains.add_domain("example.com", "admin.example.com", port=9000, dns=False)

    assert "does not proxy" in raised.value.message


def test_a_redirect_has_no_port_of_its_own(machine: Machine) -> None:
    machine.deploy(app_type="nodejs")

    with pytest.raises(DomainError):
        domains.add_domain("example.com", "old.example.org", "redirect", port=9000, dns=False)


# -- DNS follows the names ----------------------------------------------------------


class Recorded:
    def __init__(self) -> None:
        self.ensured: list[tuple[str, str]] = []
        self.released: list[tuple[str, str | None]] = []


@pytest.fixture
def dns_calls(monkeypatch: pytest.MonkeyPatch) -> Recorded:
    recorded = Recorded()

    def ensure(self: ManagedDns, app: str, hostname: str, **kwargs: Any) -> list[dict[str, Any]]:
        recorded.ensured.append((app, hostname))
        return [
            {
                "hostname": hostname,
                "type": "A",
                "content": "8.8.4.4",
                "account": "cf",
                "outcome": "created",
            }
        ]

    def release(
        self: ManagedDns, app: str, *, hostname: str | None = None, delete: bool = True
    ) -> list[Any]:
        recorded.released.append((app, hostname))
        return []

    monkeypatch.setattr(ManagedDns, "ensure_for_domain", ensure)
    monkeypatch.setattr(ManagedDns, "release", release)
    return recorded


def test_adding_a_name_points_it_here_and_removing_it_lets_the_record_go(
    machine: Machine, dns_calls: Recorded
) -> None:
    machine.deploy(app_type="nodejs")

    added = domains.add_domain("example.com", "shop.example.com")
    removed = domains.remove_domain("example.com", "shop.example.com")

    assert dns_calls.ensured == [("example.com", "shop.example.com")]
    assert added.dns[0]["outcome"] == "created"
    assert dns_calls.released == [("example.com", "shop.example.com")]
    assert removed.dns == ()


def test_a_name_added_without_dns_touches_no_provider(
    machine: Machine, dns_calls: Recorded
) -> None:
    machine.deploy(app_type="nodejs")

    domains.add_domain("example.com", "shop.example.com", dns=False)

    assert dns_calls.ensured == []


def test_a_dns_failure_never_fails_the_addition(
    machine: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from noust.core.exceptions import NoustError

    machine.deploy(app_type="nodejs")

    def boom(self: ManagedDns, *args: Any, **kwargs: Any) -> Any:
        raise NoustError("the provider is down")

    monkeypatch.setattr(ManagedDns, "ensure_for_domain", boom)

    change = domains.add_domain("example.com", "shop.example.com")

    assert ("shop.example.com", "alias") in machine.domains()
    assert change.dns == ()
    assert records_module is not None


# -- the check ---------------------------------------------------------------------


def answering(*addresses: str, calls: list[str] | None = None) -> Any:
    def transport(server: str, packet: bytes, timeout: float) -> bytes:
        if calls is not None:
            calls.append(server)
        return dns_answer(
            int.from_bytes(packet[:2], "big"), int.from_bytes(packet[-4:-2], "big"), list(addresses)
        )

    return transport


def check(*resolved: str, local: tuple[str, ...] = HERE, **kwargs: Any) -> Any:
    return domains.check_dns(
        "shop.example.com",
        local_addresses=lambda: local,
        resolvers=kwargs.pop("resolvers", ("1.1.1.1",)),
        transport=answering(*resolved),
        **kwargs,
    )


def test_the_public_dns_is_asked_not_the_machines_cache() -> None:
    asked: list[str] = []

    result = domains.check_dns(
        "shop.example.com",
        local_addresses=lambda: HERE,
        resolvers=("1.1.1.1", "8.8.8.8"),
        transport=answering(*HERE, calls=asked),
    )

    assert asked[0] == "1.1.1.1"
    assert (result.verdict, result.points_here, result.resolver) == ("here", True, "1.1.1.1")


def test_the_machines_resolver_is_the_fallback_when_none_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def nobody(server: str, packet: bytes, timeout: float) -> bytes:
        raise OSError("timed out")

    monkeypatch.setattr("socket.getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("203.0.113.5", 0))])

    result = domains.check_dns(
        "shop.example.com",
        local_addresses=lambda: HERE,
        resolvers=("1.1.1.1",),
        transport=nobody,
    )

    assert (result.verdict, result.resolver) == ("here", "")


def test_a_name_behind_cloudflares_proxy_is_not_a_mismatch() -> None:
    result = check("104.21.1.1", "172.67.2.2")

    assert (result.verdict, result.cdn, result.points_here) == ("cdn", "cloudflare", True)


def test_one_address_of_a_cdn_and_one_of_somebody_else_is_still_elsewhere() -> None:
    result = check("104.21.1.1", "198.51.100.9")

    assert (result.verdict, result.points_here) == ("elsewhere", False)


def test_a_record_that_does_not_exist_is_none() -> None:
    result = check()

    assert (result.verdict, result.points_here, result.resolved_addresses) == ("none", False, ())


def test_a_server_behind_nat_does_not_call_a_correct_record_wrong() -> None:
    result = check("93.184.216.34", local=("10.0.0.5",))

    assert result.verdict == "unknown"
    assert result.points_here is False


def test_declaring_the_public_address_turns_unknown_into_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from noust.core import config as config_module

    real = config_module.Config.get

    def get(self: Any, key: str, default: Any = None) -> Any:
        return ["93.184.216.34"] if key == "dns.public_addresses" else real(self, key, default)

    monkeypatch.setattr(config_module.Config, "get", get)

    assert check("93.184.216.34", local=("10.0.0.5",)).verdict == "here"
    assert check("198.51.100.9", local=("10.0.0.5",)).verdict == "elsewhere"


def test_an_aaaa_record_elsewhere_keeps_the_name_from_pointing_here() -> None:
    result = check(*HERE, "2001:4860:4860::8888")

    assert (result.verdict, result.points_here) == ("elsewhere", False)


def test_a_failed_challenge_is_not_blamed_on_dns_when_the_server_cannot_see_its_address(
    machine: Machine,
) -> None:
    from noust.managers.cert_manager import _diagnose_domain_dns

    result = domains.DnsCheck(
        "x.example.com", ("10.0.0.5",), ("93.184.216.34",), False, verdict="unknown"
    )

    original = domains.check_dns
    domains.check_dns = lambda *a, **k: result  # type: ignore[assignment]
    try:
        assert _diagnose_domain_dns("x.example.com") is None
    finally:
        domains.check_dns = original  # type: ignore[assignment]
    assert Path is not None and put_certificate is not None
