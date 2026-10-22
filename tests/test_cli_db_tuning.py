# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db instance limits`` and ``noust db instance health`` (checklist C-30, C-31).

The commands are clients of the one tuning manager, like the console's API. What matters at
this level: without options they only show, ``--plan`` changes nothing, nothing restarts before
the operator has seen the consequences and said yes, a word asks for a default, and a value the
server could not honour is refused with why.
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
from noust.managers.database.service import DatabaseService
from tests.database_instance_support import World, build_world, close_world


@pytest.fixture
def cli_world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    world = build_world(tmp_path, monkeypatch)
    world.managed.create("pg16", "postgresql", version="16", memory="1g")
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "config.yaml")
    Config.reset_instance()
    monkeypatch.setattr(
        db_cli,
        "_service",
        lambda logger: DatabaseService(
            store=world.store, secrets=world.service.secrets, logger=logger
        ),
    )
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources", world.managed._resources
    )
    try:
        yield world
    finally:
        Config.reset_instance()
        close_world()


@pytest.fixture
def log(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []
    monkeypatch.setattr(Logger, "_write", lambda self, message, newline=True: lines.append(message))
    return lines


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", "instance", *args], input=input)


class TestShowing:
    def test_limits_without_options_shows_what_it_is_held_to_and_changes_nothing(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run("limits", "pg16")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "1024 MB" in text and "no limit" in text and "Docker's default" in text
        assert cli_world.host.started == ["noust-db-pg16"], "only the creation started it"

    def test_health_without_options_shows_the_probe_and_what_docker_says_now(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run("health", "pg16")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "every 10 s, 5 s to answer, unhealthy after 12 failures" in text
        assert "healthy" in text

    def test_the_status_is_json_for_scripts(self, cli_world: World) -> None:
        result = run("limits", "pg16", "--json")

        body = json.loads(result.output)
        assert body["limits"]["memory_mb"] == 1024 and body["probe"]["retries"] == 12

    def test_show_names_the_other_limits_too(self, cli_world: World, log: list[str]) -> None:
        run("show", "pg16")

        text = "\n".join(log)
        assert "CPUs" in text and "Swap" in text and "TLS" in text


class TestChanging:
    def test_a_plan_says_what_would_change_and_changes_nothing(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run("limits", "pg16", "--memory", "2g", "--cpus", "1.5", "--plan")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "1024 -> 2048" in text and "default -> 1.5" in text
        assert cli_world.managed.get("pg16").memory_limit_mb == 1024

    def test_it_asks_before_restarting_the_engine(self, cli_world: World, log: list[str]) -> None:
        result = run("limits", "pg16", "--cpus", "2", input="n\n")

        assert result.exit_code != 0
        assert cli_world.managed.get("pg16").cpu_limit is None

    def test_yes_applies_it(self, cli_world: World, log: list[str]) -> None:
        result = run("limits", "pg16", "--cpus", "2", "--swap", "0", "--yes")

        assert result.exit_code == 0, result.output
        record = cli_world.managed.get("pg16")
        assert (record.cpu_limit, record.swap_limit_mb) == (2.0, 0)

    def test_a_word_asks_for_the_default(self, cli_world: World, log: list[str]) -> None:
        run("limits", "pg16", "--cpus", "2", "--swap", "512m", "--yes")

        result = run("limits", "pg16", "--cpus", "none", "--swap", "default", "--yes")

        assert result.exit_code == 0, result.output
        record = cli_world.managed.get("pg16")
        assert (record.cpu_limit, record.swap_limit_mb) == (None, None)

    def test_the_probe_is_changed_and_a_default_put_back(
        self, cli_world: World, log: list[str]
    ) -> None:
        run("health", "pg16", "--interval", "20", "--timeout", "4", "--retries", "3", "--yes")
        record = cli_world.managed.get("pg16")
        assert (record.probe_interval_s, record.probe_timeout_s, record.probe_retries) == (20, 4, 3)

        run("health", "pg16", "--retries", "default", "--yes")

        assert cli_world.managed.get("pg16").probe_retries is None

    def test_what_is_already_so_is_said_and_nothing_restarts(
        self, cli_world: World, log: list[str]
    ) -> None:
        cli_world.host.started.clear()

        result = run("limits", "pg16", "--memory", "1g", "--yes")

        assert result.exit_code == 0, result.output
        assert "already has these settings" in "\n".join(log)
        assert cli_world.host.started == []

    def test_a_value_the_server_could_not_honour_is_refused_with_why(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run("limits", "pg16", "--cpus", "64", "--yes")

        assert result.exit_code != 0
        assert "more than this server has" in result.exception.message  # type: ignore[union-attr]
        assert cli_world.managed.get("pg16").cpu_limit is None

    def test_a_probe_slower_than_its_interval_is_refused(self, cli_world: World) -> None:
        result = run("health", "pg16", "--timeout", "30", "--yes")

        assert result.exit_code != 0
        assert "longer than the time between probes" in result.exception.message  # type: ignore[union-attr]
