# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust node assign`` and ``unassign``: giving a server of the fleet to a team."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.exceptions import NodeError
from noust.core.store import NoustStore
from noust.core.teams import TeamNotFoundError, TeamStore
from noust.core.teams.nodes import NodeAssignments
from noust.web.auth import STATE_DIR_ENV
from tests.fleet_support import build_fleet, join_code


@pytest.fixture
def fleet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    built = build_fleet(tmp_path)
    monkeypatch.setattr("noust.cli.commands.node._manager", lambda: built.manager)
    TeamStore(built.manager.store).create_team("Acme", slug="acme")
    key = built.manager.central_public_key("web-2")
    added = CliRunner().invoke(
        root_cli,
        ["node", "add", "web-2", "--ssh", "root@web2.example.com", "--join-code", "-", "--json"],
        input=join_code(key) + "\n",
    )
    assert added.exit_code == 0, added.output
    yield built
    NoustStore.reset_instance()


def _run(*args: str):
    return CliRunner().invoke(root_cli, ["node", *args])


def test_a_server_is_given_to_a_team_and_listed_with_it(fleet) -> None:
    result = _run("assign", "web-2", "--team", "acme", "--json")

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["team"] == "acme"
    listed = json.loads(_run("list", "--json").output)["nodes"]
    assert [(node["name"], node["team"]) for node in listed] == [("web-2", "acme")]
    assert "acme" in _run("list").output
    assert json.loads(_run("show", "web-2", "--json").output)["team"] == "acme"


def test_a_server_with_no_team_is_listed_without_one(fleet) -> None:
    listed = json.loads(_run("list", "--json").output)["nodes"]

    assert listed[0]["team"] is None


def test_a_team_that_does_not_exist_is_refused(fleet) -> None:
    result = _run("assign", "web-2", "--team", "nope")

    assert isinstance(result.exception, TeamNotFoundError)
    assert "nope" in str(result.exception)
    assert NodeAssignments(fleet.manager.store).team_of("web-2") is None


def test_a_server_that_is_not_registered_is_refused(fleet) -> None:
    result = _run("assign", "ghost", "--team", "acme")

    assert isinstance(result.exception, NodeError)
    assert "ghost" in str(result.exception)


def test_unassigning_gives_it_back_and_says_when_there_was_nothing_to_give(fleet) -> None:
    _run("assign", "web-2", "--team", "acme")

    first = _run("unassign", "web-2", "--json")
    second = _run("unassign", "web-2", "--json")

    assert json.loads(first.output)["changed"] is True
    assert json.loads(second.output)["changed"] is False
    assert NodeAssignments(fleet.manager.store).team_of("web-2") is None


def test_dry_run_assigns_nothing(fleet) -> None:
    result = CliRunner().invoke(
        root_cli, ["--dry-run", "node", "assign", "web-2", "--team", "acme"]
    )

    assert result.exit_code == 0, result.output
    assert NodeAssignments(fleet.manager.store).team_of("web-2") is None
