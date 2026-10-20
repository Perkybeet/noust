# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db instance create --image/--args/--command`` and ``noust db instance launch`` (4.0).

The commands are clients of the managers the engine tests pin
(``tests/test_database_custom_image.py``, ``tests/test_database_launch.py``). What matters at this
level: the price is shown before anything is made and accepted in words (interactively, or
``--accept-untested``, which ``--yes`` alone is not), a line of arguments is split like a shell
would and never run by one, and taking the words back asks for nothing.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.db_instance_store import DbInstanceStore
from noust.core.runner import SubprocessRunner
from tests.database_instance_support import World
from tests.test_cli_db_instances import cli_world, log, world  # noqa: F401

# The fixtures above are the instance commands' own, imported so there is one definition of "a
# server the CLI is pointed at".
# ruff: noqa: F811

DIGEST = "sha256:" + "ab" * 32
IMAGE = f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
MAKE = ["create", "own", "--engine", "postgresql", "--version", "17", "--memory", "1g"]


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", "instance", *args], input=input)


class TestMakingOne:
    def test_the_plan_shows_the_image_and_the_price_and_asks(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run(*MAKE, "--image", IMAGE, input="n\n")

        assert result.exit_code != 0
        text = "\n".join(log) + result.output
        assert f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}" in text
        assert "not tested" in text
        assert DbInstanceStore(cli_world.store).all() == []

    def test_saying_yes_to_the_question_is_accepting_the_price(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run(*MAKE, "--image", IMAGE, input="y\n")

        assert result.exit_code == 0, result.output
        (row,) = DbInstanceStore(cli_world.store).all()
        assert row.custom_image and row.digest == DIGEST

    def test_yes_alone_is_not_accepting_it(self, cli_world: World, log: list[str]) -> None:
        result = run(*MAKE, "--image", IMAGE, "--yes")

        assert result.exit_code != 0
        assert "--accept-untested" in (result.output + "\n".join(log))
        assert DbInstanceStore(cli_world.store).all() == []

    def test_accept_untested_with_yes_makes_it(self, cli_world: World) -> None:
        result = run(*MAKE, "--image", IMAGE, "--yes", "--accept-untested")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).get("own").custom_image is True

    def test_an_image_without_a_digest_says_how_to_get_one(self, cli_world: World) -> None:
        result = run(
            *MAKE, "--image", "ghcr.io/acme/pg-timescale:17.2", "--yes", "--accept-untested"
        )

        assert result.exit_code != 0
        assert "digest" in result.output.lower() or isinstance(result.exception, Exception)
        assert DbInstanceStore(cli_world.store).all() == []

    def test_a_line_of_arguments_is_split_like_a_shell_would_and_run_by_none(
        self, cli_world: World
    ) -> None:
        result = run(
            *MAKE,
            "--args",
            "-c 'max_connections=77' -c shared_preload_libraries=pg_stat_statements",
            "--yes",
            "--accept-untested",
        )

        assert result.exit_code == 0, result.output
        row = DbInstanceStore(cli_world.store).get("own")
        assert row.args == (
            "-c",
            "max_connections=77",
            "-c",
            "shared_preload_libraries=pg_stat_statements",
        )

    def test_a_command_is_split_the_same_way(self, cli_world: World) -> None:
        result = run(
            *MAKE, "--command", "postgres -c max_connections=33", "--yes", "--accept-untested"
        )

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).get("own").command == (
            "postgres",
            "-c",
            "max_connections=33",
        )

    def test_a_quote_that_never_closes_is_a_usage_error(self, cli_world: World) -> None:
        result = run(*MAKE, "--args", "-c 'oops", "--yes", "--accept-untested")

        assert result.exit_code == 2
        assert "--args" in result.output

    def test_a_dry_run_makes_nothing_and_still_says_the_price(
        self, cli_world: World, log: list[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A rehearsal still looks (docker ps, ss) through the real runner; the server it looks
        # at is the fake one.
        monkeypatch.setattr(
            SubprocessRunner, "run", lambda self, argv, **kwargs: cli_world.host.run(argv, **kwargs)
        )
        monkeypatch.setattr(
            SubprocessRunner, "exists", lambda self, program: cli_world.host.exists(program)
        )

        result = run(*MAKE, "--image", IMAGE, "--dry-run")

        assert result.exit_code == 0, result.output
        assert "not tested" in "\n".join(log) + result.output
        assert DbInstanceStore(cli_world.store).all() == []
        assert not any(call[:2] == ("docker", "pull") for call in cli_world.host.calls)

    def test_json_carries_what_the_operator_chose(self, cli_world: World) -> None:
        result = run(*MAKE, "--args", "-c work_mem=8MB", "--yes", "--accept-untested", "--json")

        assert result.exit_code == 0, result.output
        described = json.loads(result.stdout)
        assert described["args"] == ["-c", "work_mem=8MB"] and described["customised"] is True


class TestSeeingOne:
    def test_show_says_what_is_the_operators(self, cli_world: World, log: list[str]) -> None:
        run(*MAKE, "--image", IMAGE, "--args", "-c work_mem=8MB", "--yes", "--accept-untested")
        log.clear()

        result = run("show", "own")

        text = "\n".join(log)
        assert result.exit_code == 0, result.output
        assert "your own" in text.lower() or "yours" in text.lower()
        assert "-c work_mem=8MB" in text


class TestLaunch:
    @pytest.fixture
    def made(self, cli_world: World) -> World:
        assert (
            run(
                "create",
                "pg",
                "--engine",
                "postgresql",
                "--version",
                "17",
                "--memory",
                "1g",
                "--yes",
            ).exit_code
            == 0
        )
        return cli_world

    def test_without_options_it_shows_what_the_container_is_started_with(
        self, made: World, log: list[str]
    ) -> None:
        result = run("launch", "pg")

        assert result.exit_code == 0, result.output
        assert "postgres" in "\n".join(log)

    def test_the_plan_says_what_would_change_and_restarts_nothing(
        self, made: World, log: list[str]
    ) -> None:
        started = list(made.host.started)

        result = run("launch", "pg", "--args", "-c work_mem=8MB", "--plan")

        assert result.exit_code == 0, result.output
        assert "not tested" in "\n".join(log) + result.output
        assert made.host.started == started
        assert DbInstanceStore(made.store).get("pg").args == ()

    def test_applying_asks_and_a_no_changes_nothing(self, made: World, log: list[str]) -> None:
        result = run("launch", "pg", "--args", "-c work_mem=8MB", input="n\n")

        assert result.exit_code != 0
        assert DbInstanceStore(made.store).get("pg").args == ()

    def test_yes_alone_is_not_accepting_the_price(self, made: World) -> None:
        result = run("launch", "pg", "--args", "-c work_mem=8MB", "--yes")

        assert result.exit_code != 0
        assert "--accept-untested" in result.output or "accept" in str(result.exception).lower()
        assert DbInstanceStore(made.store).get("pg").args == ()

    def test_accepted_it_restarts_on_the_words(self, made: World) -> None:
        result = run("launch", "pg", "--args", "-c work_mem=8MB", "--yes", "--accept-untested")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(made.store).get("pg").args == ("-c", "work_mem=8MB")

    def test_reset_takes_them_back_and_asks_for_no_acceptance(self, made: World) -> None:
        run("launch", "pg", "--args", "-c work_mem=8MB", "--yes", "--accept-untested")

        result = run("launch", "pg", "--reset", "--yes")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(made.store).get("pg").args == ()

    def test_two_things_at_once_are_a_usage_error(self, made: World) -> None:
        result = run("launch", "pg", "--args", "-c x=1", "--reset")

        assert result.exit_code != 0
        assert DbInstanceStore(made.store).get("pg").args == ()

    def test_showing_is_read_only_for_the_audit_policy(self) -> None:
        from noust.cli.commands.db_instances import launch_command

        assert callable(launch_command.read_only)
        assert launch_command.read_only({"args": None, "command": None, "reset": False}) is True
        assert (
            launch_command.read_only({"args": "-c x=1", "command": None, "reset": False}) is False
        )
        assert launch_command.read_only({"args": "-c x=1", "plan_only": True}) is True


class TestUpgradingOne:
    def test_an_instance_on_an_image_of_the_operators_names_the_next_one(
        self, cli_world: World
    ) -> None:
        run(*MAKE, "--image", IMAGE, "--yes", "--accept-untested")
        newer = f"ghcr.io/acme/pg-timescale:17.3@sha256:{'cd' * 32}"

        refused = run("upgrade", "own", "--no-backup")
        moved = run("upgrade", "own", "--no-backup", "--image", newer, "--accept-untested")

        assert refused.exit_code != 0
        assert moved.exit_code == 0, moved.output
        assert DbInstanceStore(cli_world.store).get("own").image_tag == "17.3"
