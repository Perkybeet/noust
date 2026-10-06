# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db public open|close|list|show`` (checklist C-22).

The commands are clients of :class:`~noust.managers.database.public_access.PublicAccess`,
like the console's API. What matters at this level: nothing is changed before
the operator has seen every consequence and said yes, a rehearsal changes
nothing, the password is printed once and where it was asked for, and closing
undoes it all.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.cli.commands import db as db_cli
from noust.core.config import Config
from noust.core.logger import Logger
from noust.core.runner import SubprocessRunner, set_runner
from noust.core.store import NoustStore
from tests.test_database_public import PASSWORD, World, build_world

ARGS = ["db", "public"]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    """A server a database can be made public on, and the CLI pointed at it."""
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(db_cli, "_service", lambda logger: built.service)
    try:
        yield built
    finally:
        built.store.close()
        NoustStore.reset_instance()
        Config.reset_instance()
        set_runner(None)


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """What the commands report to the operator."""
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def run(*args: str, input: str | None = None):
    """Run ``noust db public ...``."""
    return CliRunner().invoke(root_cli, [*ARGS, *args], input=input)


class TestOpen:
    """Seen, asked, then done."""

    def test_it_shows_everything_and_asks_before_it_changes_anything(
        self, world: World, log: list[str]
    ) -> None:
        result = run("open", "appdb", "-e", "postgresql", input="n\n")

        assert result.exit_code != 0
        text = "\n".join(log) + result.output
        assert "5433" in text and "appdb_remote" in text
        assert "try the account's password" in text
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []

    def test_yes_does_it_and_prints_the_connection_with_the_password_once(
        self, world: World, log: list[str]
    ) -> None:
        result = run("open", "appdb", "-e", "postgresql", "--yes", "--from", "203.0.113.7")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "appdb_remote" in text
        assert PASSWORD in text
        assert "sslmode=require" in text
        assert [rule for rule in world.runner.rules if "5433" in rule and "203.0.113.7" in rule]
        (record,) = world.access.listing()
        assert record.sources == ("203.0.113.7/32",)

    def test_the_options_reach_the_plan(self, world: World, log: list[str]) -> None:
        result = run(
            "open",
            "appdb",
            "-e",
            "postgresql",
            "--yes",
            "--port",
            "6543",
            "--bind",
            "192.0.2.10",
            "--access",
            "read-only",
            "--allow-plaintext",
        )
        assert result.exit_code == 0, result.output
        (record,) = world.access.listing()
        assert (record.port, record.bind_address) == (6543, "192.0.2.10")
        assert record.profile == "read_only" and record.tls == "optional"

    def test_json_carries_the_password_for_a_script(self, world: World) -> None:
        result = run("open", "appdb", "-e", "postgresql", "--yes", "--json")
        assert result.exit_code == 0, result.output
        body = json.loads(result.output)
        assert body["connection"]["password"] == PASSWORD
        assert body["exposure"]["port"] == 5433

    def test_a_dry_run_changes_nothing(
        self, world: World, log: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A rehearsal still looks (ss, ufw status) through the real runner; the
        # server it looks at is the fake one.
        monkeypatch.setattr(
            SubprocessRunner, "run", lambda self, argv, **kwargs: world.runner.run(argv, **kwargs)
        )
        result = run("open", "appdb", "-e", "postgresql", "--dry-run")
        assert result.exit_code == 0, result.output
        assert "Would make postgresql/appdb reachable on port 5433" in "\n".join(log)
        assert world.access.listing() == []
        assert list(world.units.iterdir()) == []
        assert world.state["accounts"] == set()

    def test_a_refusal_says_why(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "noust.managers.database.public_access.security_profile.database_remote_listen_allowed",
            lambda: False,
        )
        result = run("open", "appdb", "-e", "postgresql", "--yes")
        assert result.exit_code != 0
        assert "ENS profile" in str(result.exception)

    def test_a_database_that_does_not_exist_is_named(self, world: World) -> None:
        result = run("open", "ghost", "-e", "postgresql", "--yes")
        assert "does not exist" in str(result.exception)

    def test_an_unknown_engine_is_a_usage_error(self, world: World) -> None:
        result = run("open", "appdb", "-e", "oracle", "--yes")
        assert result.exit_code == 2


class TestCloseListShow:
    """The way back, and where things stand."""

    def test_close_asks_first_and_then_undoes_it_all(self, world: World, log: list[str]) -> None:
        run("open", "appdb", "-e", "postgresql", "--yes")
        refused = run("close", "appdb", "-e", "postgresql", input="n\n")
        assert refused.exit_code != 0
        assert world.access.listing()

        result = run("close", "appdb", "-e", "postgresql", "--yes")
        assert result.exit_code == 0, result.output
        assert "private again" in "\n".join(log)
        assert world.access.listing() == []
        assert world.runner.rules == ["ufw allow 22/tcp"]

    def test_closing_what_is_private_is_not_an_error(self, world: World, log: list[str]) -> None:
        assert run("close", "appdb", "-e", "postgresql", "--yes").exit_code == 0
        assert "is not reachable from the network" in "\n".join(log)

    def test_a_dry_run_close_removes_nothing(
        self, world: World, log: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run("open", "appdb", "-e", "postgresql", "--yes")
        monkeypatch.setattr(
            SubprocessRunner, "run", lambda self, argv, **kwargs: world.runner.run(argv, **kwargs)
        )
        result = run("close", "appdb", "-e", "postgresql", "--dry-run")
        assert result.exit_code == 0, result.output
        assert world.access.listing()

    def test_list(self, world: World, log: list[str]) -> None:
        assert run("list").exit_code == 0
        assert "Every database is private" in "\n".join(log)
        run("open", "appdb", "-e", "postgresql", "--yes")
        body = json.loads(run("list", "--json").output)
        assert body["exposures"][0]["database"] == "appdb"
        assert body["exposures"][0]["listening"] is True

    def test_show_never_prints_the_password_unless_asked(
        self, world: World, log: list[str]
    ) -> None:
        run("open", "appdb", "-e", "postgresql", "--yes")
        log.clear()
        assert run("show", "appdb", "-e", "postgresql").exit_code == 0
        assert PASSWORD not in "\n".join(log)
        log.clear()
        assert run("show", "appdb", "-e", "postgresql", "--reveal").exit_code == 0
        assert PASSWORD in "\n".join(log)

    def test_show_of_a_private_database(self, world: World, log: list[str]) -> None:
        assert run("show", "appdb", "-e", "postgresql").exit_code == 0
        assert "is private" in "\n".join(log)
        body = json.loads(run("show", "appdb", "-e", "postgresql", "--json").output)
        assert body == {"engine": "postgresql", "database": "appdb", "public": False}
