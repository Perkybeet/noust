# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust create --temporary``: deploy an application on a name of this server's address.

The console's wizard offers a temporary address for the first deploy of a person with no
domain, and the command line says the same thing with the same word. What is pinned here:
the name is the one :func:`noust.deployers.temporary_domain.suggest` gives (so the console, the
API and the command agree), it is made of what the application is called, and it cannot be
combined with a domain typed by hand.
"""

# The CLI fixtures are imported rather than replicated, so there stays one fake machine.
# ruff: noqa: F811

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.commands import webapp
from noust.core.store import App, NoustStore
from noust.deployers import temporary_domain
from noust.deployers.temporary_domain import prefix_of_image, prefix_of_source
from tests.test_cli_webapp import cli_runner, deployer  # noqa: F401


@pytest.mark.parametrize(
    ("source", "prefix"),
    [
        ("https://github.com/acme/shop.git", "shop"),
        ("https://github.com/acme/shop", "shop"),
        ("https://github.com/acme/shop/", "shop"),
        ("https://github.com/acme/shop.git#main", "shop"),
        ("git@github.com:acme/shop.git", "shop"),
        ("ssh://git@git.example.com:2222/team/Shop-Front.git", "Shop-Front"),
        ("https://example.com/releases/shop-1.4.tar.gz", "shop-1.4"),
        ("https://example.com/releases/shop.zip", "shop"),
        ("/srv/src/my-shop", "my-shop"),
        ("./my-shop/", "my-shop"),
        (".", None),
        ("", None),
        (None, None),
    ],
)
def test_the_name_of_a_repository_is_its_last_part(source: str | None, prefix: str | None) -> None:
    assert prefix_of_source(source) == prefix


@pytest.mark.parametrize(
    ("image", "prefix"),
    [
        ("ghcr.io/acme/shop:1.2", "shop"),
        ("ghcr.io/acme/shop@sha256:" + "a" * 64, "shop"),
        ("nginx", "nginx"),
        ("nginx:1.27-alpine", "nginx"),
        ("localhost:5000/team/app:latest", "app"),
        ("", None),
    ],
)
def test_the_name_of_an_image_has_no_registry_and_no_tag(image: str, prefix: str | None) -> None:
    assert prefix_of_image(image) == prefix


@pytest.fixture
def store(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    monkeypatch.setattr(
        temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: ["8.8.4.4"])
    )
    yield instance
    NoustStore.reset_instance()


def create(runner: CliRunner, *arguments: str) -> Any:
    return runner.invoke(webapp.cli.commands["create"], list(arguments))


def test_the_application_takes_the_name_of_its_repository(
    cli_runner: CliRunner, deployer: Any, store: Any
) -> None:
    result = create(
        cli_runner, "--temporary", "-s", "https://github.com/acme/shop.git", "-t", "nextjs"
    )

    assert result.exit_code == 0, result.output
    assert deployer.configured["domain"] == "shop.8-8-4-4.sslip.io"
    assert deployer.deployed is True


def test_a_second_application_of_the_same_repository_is_numbered(
    cli_runner: CliRunner, deployer: Any, store: Any
) -> None:
    store.create_app(App(domain="shop.8-8-4-4.sslip.io", app_path="/b", port=3001))

    result = create(
        cli_runner, "--temporary", "-s", "https://github.com/acme/shop.git", "-t", "nextjs"
    )

    assert result.exit_code == 0, result.output
    assert deployer.configured["domain"] == "shop-2.8-8-4-4.sslip.io"


def test_the_command_says_which_name_it_made_and_what_it_is_for(
    cli_runner: CliRunner, deployer: Any, store: Any
) -> None:
    result = create(cli_runner, "--temporary", "-s", "https://github.com/acme/shop.git")

    assert "shop.8-8-4-4.sslip.io" in result.output


def test_a_recipe_gives_its_name_to_the_address(
    cli_runner: CliRunner, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, Any] = {}

    def recipe(**kwargs: Any) -> int:
        seen.update(kwargs)
        return 0

    monkeypatch.setattr(webapp, "_create_from_recipe", recipe)

    result = create(cli_runner, "--temporary", "--recipe", "uptime-kuma")

    assert result.exit_code == 0, result.output
    assert seen["domain"] == "uptime-kuma.8-8-4-4.sslip.io"


def test_an_image_gives_its_name_to_the_address(
    cli_runner: CliRunner, deployer: Any, store: Any
) -> None:
    result = create(cli_runner, "--temporary", "--image", "ghcr.io/acme/shop:1.2")

    assert result.exit_code == 0, result.output
    assert deployer.configured["domain"] == "shop.8-8-4-4.sslip.io"


def test_with_nothing_to_call_it_the_application_is_called_app(
    cli_runner: CliRunner, deployer: Any, store: Any
) -> None:
    result = create(cli_runner, "--temporary", "-s", ".", "-t", "static")

    assert result.exit_code == 0, result.output
    assert deployer.configured["domain"] == "app.8-8-4-4.sslip.io"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--temporary", "-d", "shop.example.com", "-s", "."],
        ["--temporary", "--www", "-s", "."],
        ["-s", "https://github.com/acme/shop.git"],
    ],
)
def test_a_domain_and_a_temporary_name_are_two_ways_to_say_one_thing(
    cli_runner: CliRunner, deployer: Any, store: Any, arguments: list[str]
) -> None:
    result = create(cli_runner, *arguments)

    assert result.exit_code == 2, result.output
    assert deployer.deployed is False


def test_a_server_with_no_public_address_is_told_how_to_declare_one(
    cli_runner: CliRunner, deployer: Any, store: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(temporary_domain, "Config", lambda: SimpleNamespace(get=lambda key: []))
    monkeypatch.setattr(temporary_domain, "machine_addresses", lambda: ("10.0.0.2",))

    result = create(cli_runner, "--temporary", "-s", ".", "-t", "static")

    assert result.exit_code != 0
    assert deployer.deployed is False
    assert "dns.public_addresses" in str(result.exception)
