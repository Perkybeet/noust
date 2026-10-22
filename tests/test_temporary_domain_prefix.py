# Copyright (c) 2024-2026 Yago López Prado
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

import json
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


def test_the_api_names_the_application_after_what_it_is_deployed_from(
    declared: None, client: TestClient
) -> None:
    # The console passes what was typed; one implementation reads the name out of it.
    from_repository = client.get(
        "/api/domains/temporary", params={"source": "https://github.com/acme/shop.git"}
    )
    from_image = client.get("/api/domains/temporary", params={"image": "ghcr.io/acme/blog:1.2"})
    from_local = client.get("/api/domains/temporary", params={"source": "/srv/src/my-site"})

    assert from_repository.json()["domain"] == "shop.8-8-4-4.sslip.io"
    assert from_image.json()["domain"] == "blog.8-8-4-4.sslip.io"
    assert from_local.json()["domain"] == "my-site.8-8-4-4.sslip.io"


def test_a_prefix_wins_over_what_it_is_deployed_from(declared: None, client: TestClient) -> None:
    response = client.get(
        "/api/domains/temporary",
        params={"prefix": "demo", "source": "https://github.com/acme/shop.git"},
    )

    assert response.json()["domain"] == "demo.8-8-4-4.sslip.io"


def test_a_source_with_no_name_in_it_is_an_application_called_app(
    declared: None, client: TestClient
) -> None:
    response = client.get("/api/domains/temporary", params={"source": "."})

    assert response.json()["domain"] == "app.8-8-4-4.sslip.io"


def test_the_api_without_a_prefix_is_what_it_always_was(declared: None, client: TestClient) -> None:
    response = client.get("/api/domains/temporary")

    assert response.json() == {
        "address": "8.8.4.4",
        "domain": "8-8-4-4.sslip.io",
        "service": "sslip.io",
        "declared": True,
    }


def test_the_command_says_the_name_of_an_application_without_adding_it(
    declared: None, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli
    from noust.cli.commands import domain as domain_cli

    store.create_app(App(domain="shop.8-8-4-4.sslip.io", app_path="/b", port=3001))
    monkeypatch.setattr(
        domain_cli, "add_domain", lambda *a, **k: pytest.fail("--show added the name")
    )

    result = CliRunner().invoke(
        root_cli, ["domain", "temporary", "--show", "--prefix", "shop", "--json"]
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["domain"] == "shop-2.8-8-4-4.sslip.io"


def test_the_command_adds_the_name_of_an_application_to_it(
    declared: None, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from click.testing import CliRunner

    from noust.cli.app import cli as root_cli
    from noust.cli.commands import domain as domain_cli

    added: list[tuple[str, str]] = []

    def fake_add(app: str, name: str, kind: str, **kwargs: Any) -> Any:
        added.append((app, name))
        return SimpleNamespace(
            app=app,
            domains=[],
            adopted=(),
            certificate_issued=False,
            certificate_reduced=False,
            lineage_removed=False,
            certificate_error=None,
            canonical="",
            tls=False,
        )

    monkeypatch.setattr(domain_cli, "add_domain", fake_add)

    result = CliRunner().invoke(
        root_cli, ["domain", "temporary", "shop.example.com", "--prefix", "blog", "--no-cert"]
    )

    assert result.exit_code == 0, result.output
    assert added == [("shop.example.com", "blog.8-8-4-4.sslip.io")]


def test_a_prefix_that_is_too_long_for_the_api_is_refused_before_anything_is_looked_up(
    declared: None, client: TestClient
) -> None:
    response = client.get("/api/domains/temporary", params={"prefix": "a" * 300})

    assert response.status_code == 422
