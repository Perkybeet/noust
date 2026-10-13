# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust access`` and ``noust token create --project``: roles at a scope from the command line.

A front end over the same :class:`~noust.web.permissions.access.AccessManager`
the console's routes call, so what is defined and given here is what the console
shows and enforces. What these tests pin is what an operator types and reads.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.accounts import AccountManager, passwords
from noust.core.store import NoustStore
from noust.core.teams import TeamStore
from noust.web.auth import STATE_DIR_ENV

PASSWORD = "correct horse battery staple"


@pytest.fixture(autouse=True)
def cheap_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep scrypt cheap; the rules under test do not depend on its cost."""
    monkeypatch.setattr(passwords, "COST", (2**4, 8, 1))
    monkeypatch.setattr(passwords, "_dummy_hash", None)


@pytest.fixture(autouse=True)
def state_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The console's state (and the audit trail) inside the test's directory."""
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    monkeypatch.setenv(STATE_DIR_ENV, str(directory))
    return directory


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own, with a team, a project and an environment."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    teams = TeamStore(instance)
    teams.create_team("Acme", slug="acme")
    teams.create_project("acme", "Web", slug="web")
    teams.create_environment("acme", "web", "Staging", slug="staging")
    accounts = AccountManager(instance)
    accounts.create("maria", "viewer", password=PASSWORD, join_default_team=True)
    accounts.create("sofia", "security", password=PASSWORD, join_default_team=False)
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def cli() -> CliRunner:
    """A Click runner."""
    return CliRunner()


def run(cli: CliRunner, *args: str, code: int = 0) -> str:
    """
    Run a command and check how it ended.

    Args:
        cli: The runner.
        *args: The command line after ``noust``.
        code: The exit code expected.

    Returns:
        Its output, with the error a refusal raised.
    """
    result = cli.invoke(root_cli, list(args))
    assert result.exit_code == code, f"{result.output} {result.exception}"
    return f"{result.output}{result.exception or ''}"


def as_json(cli: CliRunner, *args: str) -> dict:
    """
    Args:
        cli: The runner.
        *args: The command line after ``noust``.

    Returns:
        The JSON the command printed.
    """
    return json.loads(run(cli, "--json", *args))


class TestRoles:
    def test_a_role_is_defined_listed_and_shown(self, cli: CliRunner) -> None:
        run(
            cli,
            "access",
            "role",
            "create",
            "release-manager",
            "--permission",
            "apps.read",
            "--permission",
            "apps.deploy",
            "--label",
            "Release manager",
        )

        listed = as_json(cli, "access", "role", "list")
        shown = run(cli, "access", "role", "show", "release-manager")

        assert [r["name"] for r in listed["custom"]] == ["release-manager"]
        assert {r["name"] for r in listed["built_in"]} == {"viewer", "operator", "admin", "owner"}
        assert "apps.deploy" in shown

    def test_a_built_in_name_is_refused(self, cli: CliRunner) -> None:
        output = run(cli, "access", "role", "create", "admin", "--permission", "apps.read", code=1)

        assert "built-in" in output

    def test_a_role_that_governs_and_runs_is_refused_with_the_way_out(self, cli: CliRunner) -> None:
        output = run(
            cli,
            "access",
            "role",
            "create",
            "god",
            "--permission",
            "security.manage",
            "--permission",
            "apps.deploy",
            code=1,
        )

        assert "govern access and run" in output
        assert "split it into two roles" in output

    def test_it_is_changed_and_removed(self, cli: CliRunner) -> None:
        run(cli, "access", "role", "create", "ci", "--permission", "apps.read")

        run(
            cli,
            "access",
            "role",
            "set",
            "ci",
            "--permission",
            "apps.read",
            "--permission",
            "apps.deploy",
        )
        assert "apps.deploy" in run(cli, "access", "role", "show", "ci")
        run(cli, "access", "role", "remove", "ci", "--yes")

        assert as_json(cli, "access", "role", "list")["custom"] == []

    def test_the_permissions_can_be_listed(self, cli: CliRunner) -> None:
        body = as_json(cli, "access", "permissions")

        assert "apps.deploy" in body["permissions"]


class TestGrants:
    def test_a_role_is_given_on_a_project_and_listed(self, cli: CliRunner) -> None:
        output = run(
            cli, "access", "grant", "maria", "--role", "admin", "--team", "acme", "--project", "web"
        )

        assert "maria is now admin on acme/web" in output
        found = as_json(cli, "access", "list", "--user", "maria")["bindings"]
        assert [(b["scope_kind"], b["scope"], b["role"]) for b in found] == [
            ("project", "acme/web", "admin")
        ]

    def test_an_environment_needs_nothing_but_its_names(self, cli: CliRunner) -> None:
        run(
            cli,
            "access",
            "grant",
            "maria",
            "--role",
            "operator",
            "--team",
            "acme",
            "--project",
            "web",
            "--env",
            "staging",
        )

        found = as_json(cli, "access", "list", "--team", "acme")["bindings"]

        assert [(b["scope"], b["role"]) for b in found] == [("acme/web/staging", "operator")]

    def test_the_server_scope_takes_a_custom_role(self, cli: CliRunner) -> None:
        run(cli, "access", "role", "create", "db-reader", "--permission", "databases.read")

        output = run(cli, "access", "grant", "maria", "--role", "db-reader", "--scope", "instance")

        assert "maria is now db-reader on server" in output

    def test_a_security_officer_is_refused_a_place_in_a_team(self, cli: CliRunner) -> None:
        output = run(
            cli,
            "access",
            "grant",
            "sofia",
            "--role",
            "viewer",
            "--team",
            "acme",
            "--project",
            "web",
            code=1,
        )

        assert "never join a team" in output

    def test_revoke_takes_it_back(self, cli: CliRunner) -> None:
        run(
            cli,
            "access",
            "grant",
            "maria",
            "--role",
            "viewer",
            "--team",
            "acme",
            "--project",
            "web",
        )
        binding_id = as_json(cli, "access", "list")["bindings"][0]["id"]

        run(cli, "access", "revoke", str(binding_id))

        assert as_json(cli, "access", "list")["bindings"] == []

    def test_a_role_that_is_given_cannot_be_removed(self, cli: CliRunner) -> None:
        run(cli, "access", "role", "create", "ci", "--permission", "apps.read")
        run(cli, "access", "grant", "maria", "--role", "ci", "--team", "acme", "--project", "web")

        output = run(cli, "access", "role", "remove", "ci", "--yes", code=1)

        assert "held by 1" in output


class TestBoundTokens:
    def test_a_token_is_bound_to_a_project_and_the_list_says_so(self, cli: CliRunner) -> None:
        run(
            cli, "access", "grant", "maria", "--role", "admin", "--team", "acme", "--project", "web"
        )

        output = run(
            cli,
            "token",
            "create",
            "client-ci",
            "--owner",
            "maria",
            "--scope",
            "deploy",
            "--team",
            "acme",
            "--project",
            "web",
        )
        listing = run(cli, "token", "list")

        assert "bound to acme/web" in output
        assert "acme/web" in listing

    def test_the_owner_must_have_a_role_there(self, cli: CliRunner) -> None:
        output = run(
            cli,
            "token",
            "create",
            "client-ci",
            "--owner",
            "maria",
            "--team",
            "acme",
            "--project",
            "web",
            code=1,
        )

        assert "does not reach" in output

    def test_a_token_without_an_owner_is_not_bound(self, cli: CliRunner) -> None:
        output = run(cli, "token", "create", "x", "--team", "acme", code=1)

        assert "belongs to an account" in output

    def test_the_list_shows_how_long_a_token_has_left(self, cli: CliRunner) -> None:
        run(cli, "token", "create", "short", "--expires-hours", "5")
        run(cli, "token", "create", "long", "--expires-hours", "720")

        listing = run(cli, "token", "list")

        assert "(in 4h)" in listing or "(in 5h)" in listing
        assert "(in 29d)" in listing or "(in 30d)" in listing
