# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A temporary name for a server that has none: sslip.io, made of the public address.

Only the name is worked out here; giving it to an application is the ordinary add of a domain.
What is pinned is that the name is the address sslip.io answers, that a private or shared address
makes no name, that a server behind NAT is told how to declare its address, and that the surfaces
mark the name temporary and add it through the one implementation.
"""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.cli.commands import domain as domain_cli
from noust.core.exceptions import DomainError
from noust.core.store import App, NoustStore
from noust.deployers import temporary_domain
from noust.deployers.temporary_domain import is_temporary, name_for, suggest
from noust.validators.domain import is_valid_domain
from noust.web.api import domains as domains_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth


@pytest.mark.parametrize(
    ("address", "name"),
    [
        ("203.0.113.10", "203-0-113-10.sslip.io"),
        ("8.8.8.8", "8-8-8-8.sslip.io"),
        ("2606:4700:4700::1111", "2606-4700-4700--1111.sslip.io"),
        ("2001:db8:0:0:0:0:0:1", "2001-db8--1.sslip.io"),
        ("::ffff:8.8.8.8", "8-8-8-8.sslip.io"),
    ],
)
def test_the_name_is_the_address_written_as_a_host_name(address: str, name: str) -> None:
    # 203.0.113.0/24 and 2001:db8::/32 are documentation ranges: the name is made all the same
    # when the address is declared, so the shape is pinned with them.
    assert name_for_declared(address) == name


def name_for_declared(address: str) -> str:
    original = temporary_domain.is_public_address
    temporary_domain.is_public_address = lambda _address: True  # type: ignore[assignment]
    try:
        return name_for(address)
    finally:
        temporary_domain.is_public_address = original  # type: ignore[assignment]


@pytest.mark.parametrize(
    "address",
    ["10.0.0.5", "192.168.1.9", "127.0.0.1", "100.64.0.1", "169.254.1.1", "fe80::1", "::1"],
)
def test_an_address_nobody_can_reach_makes_no_name(address: str) -> None:
    with pytest.raises(DomainError) as raised:
        name_for(address)

    assert "not a public address" in raised.value.message


@pytest.mark.parametrize("address", ["", "example.com", "300.1.1.1", "1.2.3", "1.2.3.4; id"])
def test_something_that_is_not_an_address_makes_no_name(address: str) -> None:
    with pytest.raises(DomainError):
        name_for(address)


def test_every_name_made_is_a_valid_host_name() -> None:
    for address in (
        "8.8.8.8",
        "2606:4700:4700::1111",
        "2a00:1450:4001:81b::200e",
        "::ffff:8.8.8.8",
    ):
        if not temporary_domain.is_public_address(address):
            continue
        name = name_for(address)
        assert is_valid_domain(name)[0], name
        assert re.fullmatch(r"[a-z0-9-]+\.sslip\.io", name)
        assert not name.split(".")[0].startswith("-") and not name.split(".")[0].endswith("-")


def test_a_name_made_here_is_temporary_and_no_other_is() -> None:
    assert is_temporary("8-8-8-8.sslip.io") and is_temporary("8-8-8-8.SSLIP.IO")
    assert not is_temporary("sslip.io.example.com") and not is_temporary("shop.example.com")


def test_the_declared_address_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: ["8.8.4.4"])
    )
    monkeypatch.setattr(temporary_domain, "machine_addresses", lambda: ("8.8.8.8", "10.0.0.2"))

    found = suggest()

    assert (found.address, found.domain, found.declared) == ("8.8.4.4", "8-8-4-4.sslip.io", True)


def test_an_interface_with_a_public_address_gives_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: []))
    monkeypatch.setattr(
        temporary_domain,
        "machine_addresses",
        lambda: ("10.0.0.2", "2606:4700:4700::1111", "8.8.8.8"),
    )

    found = suggest()

    # IPv4 first: it is what a certificate authority and most visitors reach.
    assert found.address == "8.8.8.8" and found.declared is False


def test_a_server_behind_nat_is_told_how_to_declare_its_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: []))
    monkeypatch.setattr(temporary_domain, "machine_addresses", lambda: ("10.0.0.2", "192.168.1.5"))

    with pytest.raises(DomainError) as raised:
        suggest()

    assert "dns.public_addresses" in raised.value.details


# -- the API ----------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Any) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    instance.create_app(App(domain="shop.example.com", app_path="/a", port=3000))
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client(store: Any) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(domains_api.dns_router, prefix="/api/domains")
    app.include_router(domains_api.router, prefix="/api/apps")
    app.dependency_overrides[require_auth] = lambda: {
        "sid": "t",
        "scope": "admin",
        "source": "bearer",
        "type": "api_token",
    }
    return TestClient(app)


def test_the_api_says_the_name_without_adding_it(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: ["8.8.4.4"])
    )

    response = client.get("/api/domains/temporary")

    assert response.status_code == 200, response.text
    assert response.json() == {
        "address": "8.8.4.4",
        "domain": "8-8-4-4.sslip.io",
        "service": "sslip.io",
        "declared": True,
    }


def test_the_api_refuses_with_the_way_to_declare_when_there_is_no_public_address(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: []))
    monkeypatch.setattr(temporary_domain, "machine_addresses", lambda: ("10.0.0.2",))

    response = client.get("/api/domains/temporary")

    assert response.status_code == 400
    assert "dns.public_addresses" in response.text


def test_the_domains_of_an_application_mark_the_temporary_ones(
    client: TestClient, store: Any
) -> None:
    store.add_domain("shop.example.com", "8-8-4-4.sslip.io", "alias")

    response = client.get("/api/apps/shop.example.com/domains")

    assert response.status_code == 200, response.text
    flags = {item["domain"]: item["temporary"] for item in response.json()["domains"]}
    assert flags == {"shop.example.com": False, "8-8-4-4.sslip.io": True}


# -- the command line -------------------------------------------------------------


def test_the_command_adds_the_name_through_the_ordinary_add(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: ["8.8.4.4"])
    )
    added: list[tuple[Any, ...]] = []

    def fake_add(app: str, name: str, kind: str, **kwargs: Any) -> Any:
        added.append((app, name, kind, kwargs["issue_cert"]))
        return SimpleNamespace(
            app=app,
            domains=[],
            adopted=(),
            certificate_issued=False,
            certificate_reduced=False,
            certificate_error=None,
            canonical="",
            tls=False,
        )

    monkeypatch.setattr(domain_cli, "add_domain", fake_add)

    result = CliRunner().invoke(root_cli, ["domain", "temporary", "shop.example.com", "--no-cert"])

    assert result.exit_code == 0, result.output
    assert added == [("shop.example.com", "8-8-4-4.sslip.io", "alias", False)]


def test_show_only_says_the_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: ["8.8.4.4"])
    )
    monkeypatch.setattr(
        domain_cli, "add_domain", lambda *a, **k: pytest.fail("--show added the name")
    )

    result = CliRunner().invoke(root_cli, ["domain", "temporary", "--show", "--json"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["domain"] == "8-8-4-4.sslip.io"
