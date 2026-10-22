# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A domain can be switched off without being deleted (the Coolify/Dokploy gap in the parity audit).

Removing a name forgets it: its row, its place in the certificate, the DNS record Noust made. An
operator who only wants a name to stop answering for a while (a campaign that ended, a migration
in progress) had to delete it and add it back. What is pinned: the row is kept with its kind, port
and DNS, the site stops serving the name and the certificate is ordered again without it, switching
it on brings all of that back, the primary and the canonical pair cannot be switched off, a
configuration the web server refuses leaves the flag as it was, and the CLI and the API are the
translation of the same two functions.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.exceptions import DomainError, ValidationError
from noust.core.store import App, DomainRecord, NoustStore
from noust.deployers import domains
from noust.deployers.helpers.site import certificate_names
from noust.web.api import domains as domains_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth
from tests.test_domains import (
    NGINX_REJECTS,
    Machine,
    certs,
    machine,
    requested_names,
    store,
    web,
)

FIXTURES = (certs, machine, store, web)

LINEAGE = (
    "  Certificate Name: example.com\n"
    "    Domains: example.com shop.example.com\n"
    "    Expiry Date: 2026-11-30 12:00:00+00:00 (VALID: 60 days)\n"
    "    Certificate Path: /etc/letsencrypt/live/example.com/fullchain.pem\n"
    "    Private Key Path: /etc/letsencrypt/live/example.com/privkey.pem\n"
)


class TestTheStore:
    def test_a_domain_is_on_until_it_is_switched_off(self, store: NoustStore) -> None:
        store.create_app(App(domain="example.com", app_path="/srv/example"))
        store.add_domain("example.com", "shop.example.com", "alias")

        records = {r.domain: r for r in store.list_domains("example.com")}

        assert records["shop.example.com"].enabled is True
        assert records["example.com"].enabled is True

    def test_switching_off_keeps_the_row_its_port_and_its_kind(self, store: NoustStore) -> None:

        store.create_app(App(domain="example.com", app_path="/srv/example"))
        store.add_domain("example.com", "shop.example.com", "alias")
        store.set_domain_port("example.com", "shop.example.com", 9000)

        assert store.set_domain_enabled("example.com", "shop.example.com", False) is True

        everything = {r.domain: r for r in store.list_domains("example.com")}
        assert everything["shop.example.com"].enabled is False
        assert everything["shop.example.com"].port == 9000
        assert everything["shop.example.com"].kind == "alias"
        served = [r.domain for r in store.list_domains("example.com", include_disabled=False)]
        assert served == ["example.com"]

    def test_the_primary_cannot_be_switched_off(self, store: NoustStore) -> None:

        store.create_app(App(domain="example.com", app_path="/srv/example"))

        with pytest.raises(DomainError, match="primary"):
            store.set_domain_enabled("example.com", "example.com", False)

    def test_a_name_the_application_does_not_have_is_not_found(self, store: NoustStore) -> None:

        store.create_app(App(domain="example.com", app_path="/srv/example"))

        assert store.set_domain_enabled("example.com", "nope.example.com", False) is False

    def test_switching_on_again_is_how_it_comes_back(self, store: NoustStore) -> None:

        store.create_app(App(domain="example.com", app_path="/srv/example"))
        store.add_domain("example.com", "shop.example.com", "alias")
        store.set_domain_enabled("example.com", "shop.example.com", False)

        store.set_domain_enabled("example.com", "shop.example.com", True)

        assert [r.domain for r in store.list_domains("example.com", include_disabled=False)] == [
            "example.com",
            "shop.example.com",
        ]


class TestSwitchingOff:
    def test_the_site_stops_serving_the_name_and_the_row_stays(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")

        change = domains.disable_domain("example.com", "shop.example.com")

        assert "shop.example.com" not in machine.config()
        assert "server_name example.com;" in machine.config()
        assert machine.reloaded()
        assert ("shop.example.com", "alias") in machine.domains()
        disabled = [r for r in change.domains if r.domain == "shop.example.com"]
        assert disabled and disabled[0].enabled is False

    def test_a_redirect_stops_redirecting(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "old.example.org", "redirect")

        domains.disable_domain("example.com", "old.example.org")

        assert "old.example.org" not in machine.config()
        assert ("old.example.org", "redirect") in machine.domains()

    def test_the_certificate_is_ordered_again_without_it(self, machine: Machine) -> None:
        machine.deploy(tls=True)
        domains.add_domain("example.com", "shop.example.com")
        machine.runner.script(["certbot", "certificates"], stdout=LINEAGE)
        machine.runner.calls.clear()

        change = domains.disable_domain("example.com", "shop.example.com")

        (order,) = machine.certbot_orders()
        assert requested_names(order) == ["example.com"]
        assert change.certificate_reduced is True
        assert not [call for call in machine.runner.calls if call[:2] == ("certbot", "revoke")]

    def test_the_certificate_can_be_left_for_later(self, machine: Machine) -> None:
        machine.deploy(tls=True)
        domains.add_domain("example.com", "shop.example.com")
        machine.runner.calls.clear()

        domains.disable_domain("example.com", "shop.example.com", reduce_cert=False)

        assert machine.certbot_orders() == []

    def test_the_dns_record_is_not_released(
        self, machine: Machine, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The name is meant to come back: its record is exactly what makes that cheap."""
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")
        released: list[str] = []
        monkeypatch.setattr(domains, "_release_dns", lambda app, name, log: released.append(name))

        domains.disable_domain("example.com", "shop.example.com")

        assert released == []

    def test_a_name_that_is_off_is_not_in_the_certificate_names(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")
        domains.add_domain("example.com", "old.example.org", "redirect")

        domains.disable_domain("example.com", "shop.example.com")

        assert certificate_names(machine.store, "example.com") == ["example.com", "old.example.org"]

    def test_the_primary_is_refused(self, machine: Machine) -> None:
        machine.deploy()

        with pytest.raises(DomainError, match="primary"):
            domains.disable_domain("example.com", "example.com")

    def test_the_canonical_pair_is_refused(self, machine: Machine) -> None:
        machine.deploy()
        domains.set_canonical("example.com", "www", issue_cert=False)

        with pytest.raises(DomainError, match="canonical"):
            domains.disable_domain("example.com", "www.example.com")
        with pytest.raises(DomainError, match="canonical"):
            domains.disable_domain("example.com", "example.com")

    def test_a_name_the_application_does_not_have_is_refused(self, machine: Machine) -> None:
        machine.deploy()

        with pytest.raises(DomainError, match=r"not a domain of example\.com"):
            domains.disable_domain("example.com", "nope.example.com")

    def test_a_configuration_the_web_server_refuses_leaves_it_on(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")
        before = machine.config()
        machine.runner.script(["nginx", "-t"], stderr=NGINX_REJECTS, exit_code=1)

        with pytest.raises(ValidationError):
            domains.disable_domain("example.com", "shop.example.com")

        assert machine.config() == before
        enabled = {r.domain: r.enabled for r in machine.store.list_domains("example.com")}
        assert enabled["shop.example.com"] is True

    def test_switching_off_twice_is_a_retry_not_an_error(self, machine: Machine) -> None:
        machine.deploy(tls=True)
        domains.add_domain("example.com", "shop.example.com")
        domains.disable_domain("example.com", "shop.example.com", reduce_cert=False)

        again = domains.disable_domain("example.com", "shop.example.com", reduce_cert=False)

        assert [r.enabled for r in again.domains if r.domain == "shop.example.com"] == [False]

    def test_adding_a_name_that_is_off_says_to_switch_it_on(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")
        domains.disable_domain("example.com", "shop.example.com")

        with pytest.raises(DomainError, match="switched off"):
            domains.add_domain("example.com", "shop.example.com")


class TestSwitchingOn:
    def test_the_site_serves_the_name_again_with_its_port(self, machine: Machine) -> None:
        machine.deploy(app_type="nodejs")
        domains.add_domain("example.com", "admin.example.com", port=9000)
        domains.disable_domain("example.com", "admin.example.com")
        assert "admin.example.com" not in machine.config()

        change = domains.enable_domain("example.com", "admin.example.com")

        config = machine.config()
        assert "server_name admin.example.com;" in config
        assert "proxy_pass http://127.0.0.1:9000;" in config
        assert [r.enabled for r in change.domains if r.domain == "admin.example.com"] == [True]

    def test_the_certificate_is_extended_to_cover_it(self, machine: Machine) -> None:
        machine.deploy(tls=True)
        domains.add_domain("example.com", "shop.example.com", issue_cert=False)
        domains.disable_domain("example.com", "shop.example.com", reduce_cert=False)
        machine.runner.calls.clear()

        change = domains.enable_domain("example.com", "shop.example.com")

        (order,) = machine.certbot_orders()
        assert "--expand" in order
        assert requested_names(order) == ["example.com", "shop.example.com"]
        assert change.certificate_issued is True

    def test_a_name_that_is_already_on_is_a_retry(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")

        change = domains.enable_domain("example.com", "shop.example.com")

        assert [r.enabled for r in change.domains if r.domain == "shop.example.com"] == [True]

    def test_a_refused_site_leaves_it_off(self, machine: Machine) -> None:
        machine.deploy()
        domains.add_domain("example.com", "shop.example.com")
        domains.disable_domain("example.com", "shop.example.com")
        before = machine.config()
        machine.runner.script(["nginx", "-t"], stderr=NGINX_REJECTS, exit_code=1)

        with pytest.raises(ValidationError):
            domains.enable_domain("example.com", "shop.example.com")

        assert machine.config() == before
        enabled = {r.domain: r.enabled for r in machine.store.list_domains("example.com")}
        assert enabled["shop.example.com"] is False

    def test_a_name_the_application_does_not_have_is_refused(self, machine: Machine) -> None:
        machine.deploy()

        with pytest.raises(DomainError, match=r"not a domain of example\.com"):
            domains.enable_domain("example.com", "nope.example.com")


class TestTheCommands:
    def test_disable_and_enable_call_the_same_functions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.cli.commands import domain as domain_module

        seen: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        primary = DomainRecord(domain="example.com", kind="primary")
        shop = DomainRecord(domain="shop.example.com", kind="alias", enabled=False)
        change = domains.DomainChange(app="example.com", domains=(primary, shop), tls=False)

        def stub(name: str) -> Any:
            def fake(*args: Any, **kwargs: Any) -> Any:
                seen.append((name, args, kwargs))
                return change

            return fake

        monkeypatch.setattr(domain_module, "disable_domain", stub("disable"))
        monkeypatch.setattr(domain_module, "enable_domain", stub("enable"))

        off = CliRunner().invoke(root_cli, ["domain", "disable", "example.com", "shop.example.com"])
        on = CliRunner().invoke(root_cli, ["domain", "enable", "example.com", "shop.example.com"])

        assert off.exit_code == 0, off.output
        assert on.exit_code == 0, on.output
        assert [(name, args) for name, args, _ in seen] == [
            ("disable", ("example.com", "shop.example.com")),
            ("enable", ("example.com", "shop.example.com")),
        ]
        assert "off" in off.output

    def test_the_list_says_which_names_are_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import json

        from noust.cli.commands import domain as domain_module

        records = [
            DomainRecord(domain="example.com", kind="primary"),
            DomainRecord(domain="shop.example.com", kind="alias", enabled=False),
        ]
        monkeypatch.setattr(domain_module, "list_domains", lambda app: records)

        result = CliRunner().invoke(root_cli, ["domain", "list", "example.com", "--json"])

        items = {item["domain"]: item for item in json.loads(result.output)["items"]}
        assert items["shop.example.com"]["enabled"] is False
        assert items["example.com"]["enabled"] is True


class TestTheApi:
    @pytest.fixture
    def client(self, store: NoustStore) -> TestClient:

        store.create_app(App(domain="example.com", app_path="/var/www/apps/example.com"))
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(domains_api.router, prefix="/api/apps")
        app.dependency_overrides[require_auth] = lambda: {
            "sid": "token",
            "scope": "admin",
            "source": "bearer",
            "type": "api_token",
        }
        return TestClient(app)

    def test_switching_off_calls_the_function_and_queues_the_certificate(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        seen: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        primary = DomainRecord(domain="example.com", kind="primary")
        shop = DomainRecord(domain="shop.example.com", kind="alias", enabled=False)

        def fake(*args: Any, **kwargs: Any) -> Any:
            seen.append(("disable", args, kwargs))
            return domains.DomainChange(app="example.com", domains=(primary, shop), tls=False)

        monkeypatch.setattr(domains_api, "disable_domain", fake)

        response = client.put(
            "/api/apps/example.com/domains/shop.example.com/enabled", json={"enabled": False}
        )

        assert response.status_code == 200, response.text
        assert seen == [("disable", ("example.com", "shop.example.com"), {"reduce_cert": False})]
        listed = {d["domain"]: d for d in response.json()["domains"]}
        assert listed["shop.example.com"]["enabled"] is False
        assert listed["example.com"]["enabled"] is True

    def test_switching_on_calls_the_other_function(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        seen: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        primary = DomainRecord(domain="example.com", kind="primary")
        shop = DomainRecord(domain="shop.example.com", kind="alias")

        def fake(*args: Any, **kwargs: Any) -> Any:
            seen.append(("enable", args, kwargs))
            return domains.DomainChange(app="example.com", domains=(primary, shop), tls=False)

        monkeypatch.setattr(domains_api, "enable_domain", fake)

        response = client.put(
            "/api/apps/example.com/domains/shop.example.com/enabled", json={"enabled": True}
        )

        assert response.status_code == 200, response.text
        assert seen == [("enable", ("example.com", "shop.example.com"), {"issue_cert": False})]

    def test_a_refusal_is_a_400_with_the_words(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refuse(*args: Any, **kwargs: Any) -> Any:
            raise DomainError("example.com is the primary domain and cannot be switched off")

        monkeypatch.setattr(domains_api, "disable_domain", refuse)

        response = client.put(
            "/api/apps/example.com/domains/example.com/enabled", json={"enabled": False}
        )

        assert response.status_code == 400
        assert "primary" in response.text
