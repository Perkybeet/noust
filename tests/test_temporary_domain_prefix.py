# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A temporary name for each application: ``shop.203-0-113-10.sslip.io``.

The name of a server alone (``203-0-113-10.sslip.io``) is one name, and a server runs many
applications, so the first deploy of a person with no domain needs a name per application.
sslip.io answers any label before the address with that address. What is pinned here:

- the prefix is one valid host label, and one that cannot be read as part of the address;
- the name is unique among the names and the *application names* of the store (the unit, the
  directory and the user come from the domain with its dots turned to dashes);
- the API and the command line say the same name, and without a prefix nothing changed.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.exceptions import DomainError
from noust.core.store import App, NoustStore
from noust.deployers import temporary_domain
from noust.deployers.temporary_domain import is_temporary, name_for, suggest
from noust.validators.domain import is_valid_domain
from noust.web.api import domains as domains_api
from noust.web.api.deps import install_error_handlers
from noust.web.auth import require_auth


@pytest.mark.parametrize(
    ("prefix", "label"),
    [
        ("shop", "shop"),
        ("Uptime Kuma", "uptime-kuma"),
        ("shop.example.com", "shop-example-com"),
        ("--weird__name--", "weird-name"),
        ("", "app"),
        ("!!!", "app"),
        # A label of digits and dashes would read as part of an address to sslip.io.
        ("1-2-3-4", "app-1-2-3-4"),
        ("9", "app-9"),
        ("a" * 90, "a" * 40),
    ],
)
def test_a_prefix_becomes_one_host_label(prefix: str, label: str) -> None:
    assert temporary_domain.label_of(prefix) == label


def test_a_name_with_a_prefix_is_the_prefix_before_the_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 203.0.113.0/24 and 2001:db8::/32 are documentation ranges: the shape is pinned with them.
    monkeypatch.setattr(temporary_domain, "is_public_address", lambda _address: True)

    assert name_for("203.0.113.10", "shop") == "shop.203-0-113-10.sslip.io"
    assert name_for("2001:db8::1", "Blog") == "blog.2001-db8--1.sslip.io"
    assert name_for("203.0.113.10", None) == "203-0-113-10.sslip.io"


def test_a_name_with_a_prefix_is_a_valid_host_name_and_stays_temporary() -> None:
    name = name_for("8.8.8.8", "Uptime Kuma")

    assert is_valid_domain(name)[0], name
    assert is_temporary(name)
    assert name == "uptime-kuma.8-8-8-8.sslip.io"


def test_a_prefix_does_not_make_a_private_address_reachable() -> None:
    with pytest.raises(DomainError):
        name_for("10.0.0.5", "shop")


@pytest.fixture
def declared(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: ["8.8.4.4"])
    )


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
    app.dependency_overrides[require_auth] = lambda: {
        "sid": "t",
        "scope": "admin",
        "source": "bearer",
        "type": "api_token",
    }
    return TestClient(app)


def test_the_first_application_called_shop_gets_shop(declared: None, store: Any) -> None:
    assert suggest("shop").domain == "shop.8-8-4-4.sslip.io"


def test_a_second_application_with_the_same_prefix_gets_the_next_number(
    declared: None, store: Any
) -> None:
    store.create_app(App(domain="shop.8-8-4-4.sslip.io", app_path="/b", port=3001))

    assert suggest("shop").domain == "shop-2.8-8-4-4.sslip.io"

    store.create_app(App(domain="shop-2.8-8-4-4.sslip.io", app_path="/c", port=3002))

    assert suggest("shop").domain == "shop-3.8-8-4-4.sslip.io"


def test_a_name_an_alias_already_answers_on_is_not_given_again(declared: None, store: Any) -> None:
    store.add_domain("shop.example.com", "shop.8-8-4-4.sslip.io", "alias")

    assert suggest("shop").domain == "shop-2.8-8-4-4.sslip.io"


def test_two_names_that_make_one_application_name_are_one_name(declared: None, store: Any) -> None:
    # The unit, the directory and the user come from the domain with its dots turned to
    # dashes, so a different domain that spells the same name would share all three.
    store.create_app(App(domain="shop-8-8-4-4.sslip.io", app_path="/b", port=3001))

    assert suggest("shop").domain == "shop-2.8-8-4-4.sslip.io"


def test_a_numbered_name_is_still_a_temporary_name_of_the_same_address(
    declared: None, store: Any
) -> None:
    store.create_app(App(domain="shop.8-8-4-4.sslip.io", app_path="/b", port=3001))

    found = suggest("shop")

    assert is_valid_domain(found.domain)[0] and is_temporary(found.domain)
    assert (found.address, found.declared) == ("8.8.4.4", True)


def test_without_a_prefix_the_name_is_the_server_name_it_always_was(
    declared: None, store: Any
) -> None:
    store.create_app(App(domain="8-8-4-4.sslip.io", app_path="/b", port=3001))

    assert suggest().domain == "8-8-4-4.sslip.io"


def test_the_api_gives_the_name_of_an_application(declared: None, client: TestClient) -> None:
    response = client.get("/api/domains/temporary", params={"prefix": "Shop"})

    assert response.status_code == 200, response.text
    assert response.json()["domain"] == "shop.8-8-4-4.sslip.io"
    assert response.json()["address"] == "8.8.4.4"


def test_the_api_numbers_a_prefix_that_is_taken(
    declared: None, client: TestClient, store: Any
) -> None:
    store.create_app(App(domain="shop.8-8-4-4.sslip.io", app_path="/b", port=3001))

    response = client.get("/api/domains/temporary", params={"prefix": "shop"})

    assert response.json()["domain"] == "shop-2.8-8-4-4.sslip.io"


def test_the_api_without_a_prefix_is_what_it_always_was(declared: None, client: TestClient) -> None:
    response = client.get("/api/domains/temporary")

    assert response.json() == {
        "address": "8.8.4.4",
        "domain": "8-8-4-4.sslip.io",
        "service": "sslip.io",
        "declared": True,
    }


def test_a_prefix_that_is_too_long_for_the_api_is_refused_before_anything_is_looked_up(
    declared: None, client: TestClient
) -> None:
    response = client.get("/api/domains/temporary", params={"prefix": "a" * 300})

    assert response.status_code == 422
