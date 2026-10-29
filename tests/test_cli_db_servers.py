# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db servers``: the database servers of this server, and the default of each engine (4.1).

The commands are clients of :class:`~noust.managers.database.servers.DatabaseServers`, which
``tests/test_database_servers.py`` pins. What matters at this level: the list says what the
console's says, ``noust db servers`` alone is the list (the errors that suggest it must work as
typed), choosing and taking back a default work, and a refusal carries its fix.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.db_server_defaults import DbServerDefaultStore
from tests.support.cli_db_instances import cli_world, log, world  # noqa: F401
from tests.support.database_instance_support import World

# The fixtures above are the instance commands' own, imported so there is one definition of "a
# server the CLI is pointed at".
# ruff: noqa: F811


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", *args], input=input)


@pytest.fixture
def made(cli_world: World, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setattr("noust.managers.database.managed.host_architecture", lambda: "amd64")
    # Docker and none of the engines' own clients: nothing is installed on the system.
    cli_world.host.only_knows("docker")
    cli_world.managed.create("pg16", "postgresql", version="16", memory="1g")
    cli_world.managed.create("pg17", "postgresql", version="17", memory="1g")
    yield cli_world


class TestTheList:
    def test_noust_db_servers_alone_is_the_list(self, made: World, log: list[str]) -> None:
        result = run("servers")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "postgresql@pg16" in text and "postgresql@pg17" in text

    def test_the_default_is_marked(self, made: World, log: list[str]) -> None:
        result = run("servers", "list")

        assert result.exit_code == 0, result.output
        assert any("postgresql@pg16" in line and "default" in line for line in log)

    def test_json_is_what_the_api_says(self, made: World) -> None:
        result = run("servers", "list", "--json")

        body = json.loads(result.stdout)
        assert [s["key"] for s in body["servers"]] == ["postgresql@pg16", "postgresql@pg17"]
        assert body["servers"][0]["default_source"] == "oldest"
        assert body["problems"] == []

    def test_one_engine_can_be_asked_for(self, made: World) -> None:
        result = run("servers", "list", "--engine", "mysql", "--json")

        assert json.loads(result.stdout)["servers"] == []

    def test_an_empty_list_says_how_to_have_a_server(self, made: World, log: list[str]) -> None:
        result = run("servers", "list", "--engine", "mysql")

        assert result.exit_code == 0, result.output
        assert "noust db instance create" in "\n".join(log) + result.output


class TestTheDefault:
    def test_choosing_one(self, made: World, log: list[str]) -> None:
        result = run("servers", "default", "postgresql@pg17")

        assert result.exit_code == 0, result.output
        chosen = DbServerDefaultStore(made.store).get("postgresql")
        assert chosen is not None and chosen.server_key == "postgresql@pg17"
        assert "postgresql@pg17" in "\n".join(log)

    def test_taking_the_choice_back(self, made: World) -> None:
        run("servers", "default", "postgresql@pg17")

        result = run("servers", "default", "postgresql@pg17", "--clear")

        assert result.exit_code == 0, result.output
        assert DbServerDefaultStore(made.store).all() == {}

    def test_a_server_that_does_not_exist_says_how_to_list_them(
        self, made: World, log: list[str]
    ) -> None:
        result = run("servers", "default", "postgresql@nothing")

        assert result.exit_code != 0
        assert "noust db servers" in str(result.exception)
        assert DbServerDefaultStore(made.store).all() == {}

    def test_the_command_the_error_suggests_works_as_typed(self, made: World) -> None:
        # database_servers.unknown ends with "List them with: noust db servers".
        assert run("servers").exit_code == 0


class TestCreatingADatabaseUsesTheDefault:
    def test_a_database_made_by_its_engine_goes_to_the_default_server(
        self, made: World, log: list[str]
    ) -> None:
        run("servers", "default", "postgresql@pg17")

        result = run("create", "shop", "--engine", "postgresql")

        assert result.exit_code == 0, result.output
        stored = made.store.get_database("shop", "postgresql@pg17")
        assert stored is not None
