# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db move`` and ``noust db instance migrate|migrations|rollback`` (checklist C-13, C-21).

The commands are clients of the one mover and the one migration, like the console's
API. What matters at this level: nothing changes before the operator has seen
every consequence and said yes, ``--plan`` and ``--dry-run`` change nothing, a plan
with blockers is refused with them listed, and the way back is printed.
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
from noust.core.runner import SubprocessRunner
from tests.database_migration_support import Lab, build_lab
from tests.database_move_support import DOMAIN, HOST


@pytest.fixture
def lab(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Lab]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    try:
        for built in build_lab(tmp_path, monkeypatch):
            monkeypatch.setattr(db_cli, "_service", lambda logger, built=built: built.service)
            monkeypatch.setattr(
                "noust.managers.database.managed.server_resources", built.managed._resources
            )
            yield built
    finally:
        Config.reset_instance()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", *args], input=input)


class TestMovingAnEngine:
    def test_plan_says_everything_and_makes_nothing(self, lab: Lab, log: list[str]) -> None:
        result = run("instance", "migrate", HOST, "--to-version", "17", "--memory", "1g", "--plan")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "shop_db" in text and DOMAIN in text and "1024 MB" in text
        assert lab.managed.records.all() == []

    def test_it_asks_before_moving(self, lab: Lab, log: list[str]) -> None:
        result = run(
            "instance", "migrate", HOST, "--to-version", "17", "--memory", "1g", input="n\n"
        )

        assert result.exit_code != 0
        assert lab.managed.records.all() == []

    def test_yes_moves_it_and_prints_the_way_back(self, lab: Lab, log: list[str]) -> None:
        result = run("instance", "migrate", HOST, "--to-version", "17", "--memory", "1g", "--yes")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "1 database(s) are on postgresql17" in text
        assert "noust db instance rollback 1" in text
        assert lab.managed.get("postgresql17").state == "ready"

    def test_a_dry_run_changes_nothing(
        self, lab: Lab, log: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A rehearsal still looks (docker ps, ss) through the real runner; the server it
        # looks at is the fake one.
        monkeypatch.setattr(
            SubprocessRunner, "run", lambda self, argv, **kwargs: lab.world.host.run(argv, **kwargs)
        )
        result = run(
            "instance",
            "migrate",
            HOST,
            "--to-version",
            "17",
            "--memory",
            "1g",
            "--yes",
            "--dry-run",
        )

        assert result.exit_code == 0, result.output
        assert lab.managed.records.all() == []

    def test_a_plan_with_a_blocker_is_refused_with_it_listed(
        self, lab: Lab, log: list[str]
    ) -> None:
        result = run("instance", "migrate", HOST, "--to-version", "16", "--memory", "1g", "--yes")

        assert result.exit_code != 0
        assert "not newer" in "\n".join(log)
        assert lab.managed.records.all() == []

    def test_json_is_the_plan_alone(self, lab: Lab) -> None:
        result = run(
            "instance", "migrate", HOST, "--to-version", "17", "--memory", "1g", "--plan", "--json"
        )

        assert result.exit_code == 0, result.output
        body = json.loads(result.stdout)
        assert body["target"] == "postgresql17" and body["apps"] == [DOMAIN]

    def test_the_moves_are_listed_and_one_is_rolled_back(self, lab: Lab, log: list[str]) -> None:
        run("instance", "migrate", HOST, "--to-version", "17", "--memory", "1g", "--yes")
        log.clear()

        listing = run("instance", "migrations", "--json")
        back = run("instance", "rollback", "1", "--yes")

        assert json.loads(listing.stdout)["migrations"][0]["can_roll_back"] is True
        assert back.exit_code == 0, back.output
        assert "are on postgresql again" in "\n".join(log)
        assert lab.env()["DATABASE_URL"].split("@")[1].startswith("localhost:5432/")


class TestMovingADatabase:
    def test_plan_names_the_applications(self, lab: Lab, log: list[str]) -> None:
        lab.managed.create("pg17", "postgresql", version="17", memory="1g")
        log.clear()

        result = run("move", "shop_db", "--from", HOST, "--to", "postgresql@pg17", "--plan")

        assert result.exit_code == 0, result.output
        assert DOMAIN in "\n".join(log)

    def test_yes_moves_it_and_says_it_is_still_on_the_source(
        self, lab: Lab, log: list[str]
    ) -> None:
        lab.managed.create("pg17", "postgresql", version="17", memory="1g")
        log.clear()

        result = run("move", "shop_db", "--from", HOST, "--to", "postgresql@pg17", "--yes")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "shop_db is on postgresql@pg17" in text
        assert "still on postgresql" in text

    def test_a_move_that_cannot_go_ahead_lists_why(self, lab: Lab, log: list[str]) -> None:
        result = run("move", "shop_db", "--from", HOST, "--to", HOST, "--yes")

        assert result.exit_code != 0
        assert "same engine" in "\n".join(log)
