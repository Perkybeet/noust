# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db instance create`` for a version without support, and for an image pinned at creation.

The manager's tests (``tests/test_database_unsupported_instances.py``) pin the rules. At this
level: the date is shown before anything is made, the confirmation is asked in words
(interactively, or ``--accept-unsupported``, which ``--yes`` alone is not), the refusal names the
flag, and the catalog lists the older versions as such.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.db_instance_store import DbInstanceStore
from tests.support.cli_db_instances import cli_world, log, world  # noqa: F401
from tests.support.database_instance_support import World

# The fixtures above are the instance commands' own, imported so there is one definition of "a
# server the CLI is pointed at".
# ruff: noqa: F811

DIGEST = "sha256:" + "ab" * 32
OLD = ["create", "old", "--engine", "mysql", "--version", "5.7", "--memory", "1g"]


def run(*args: str, input: str | None = None):
    return CliRunner().invoke(root_cli, ["db", "instance", *args], input=input)


@pytest.fixture(autouse=True)
def amd64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("noust.managers.database.managed.host_architecture", lambda: "amd64")


class TestMakingOneWithoutSupport:
    def test_the_plan_says_since_when_and_asks(self, cli_world: World, log: list[str]) -> None:
        result = run(*OLD, input="n\n")

        assert result.exit_code != 0
        text = "\n".join(log) + result.output
        assert "2023-10-31" in text and "no security fixes" in text
        assert DbInstanceStore(cli_world.store).all() == []

    def test_saying_yes_to_the_question_accepts_it(self, cli_world: World) -> None:
        result = run(*OLD, input="y\n")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).get("old").version == "5.7"

    def test_yes_alone_is_not_accepting_it_and_the_error_names_the_flag(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run(*OLD, "--yes")

        assert result.exit_code != 0
        assert "--accept-unsupported" in result.output + "\n".join(log)
        assert "2023-10-31" in result.output + "\n".join(log)
        assert DbInstanceStore(cli_world.store).all() == []

    def test_the_flag_with_yes_makes_it(self, cli_world: World) -> None:
        result = run(*OLD, "--yes", "--accept-unsupported")

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).get("old") is not None

    def test_json_without_the_flag_is_refused_too(self, cli_world: World) -> None:
        result = run(*OLD, "--yes", "--json")

        assert result.exit_code != 0
        assert "--accept-unsupported" in result.output
        assert DbInstanceStore(cli_world.store).all() == []

    def test_json_carries_the_support_the_instance_has(self, cli_world: World) -> None:
        result = run(*OLD, "--accept-unsupported", "--yes", "--json")

        assert result.exit_code == 0, result.output
        described = json.loads(result.stdout)
        assert (described["support"], described["end_of_life"]) == ("ended", "2023-10-31")

    def test_a_current_version_is_asked_nothing_more_than_before(self, cli_world: World) -> None:
        result = run("create", "new", "--engine", "mysql", "--memory", "1g", "--yes")

        assert result.exit_code == 0, result.output

    def test_both_confirmations_are_separate_flags(self, cli_world: World, log: list[str]) -> None:
        image = f"ghcr.io/acme/mysql:5.7@{DIGEST}"

        only_untested = run(*OLD, "--image", image, "--yes", "--accept-untested")
        both = run(*OLD, "--image", image, "--yes", "--accept-untested", "--accept-unsupported")

        assert only_untested.exit_code != 0
        assert both.exit_code == 0, both.output


class TestAnImageByItsTag:
    def test_the_plan_says_the_digest_is_read_when_it_is_made(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run(
            "create",
            "own",
            "--engine",
            "postgresql",
            "--version",
            "17",
            "--memory",
            "1g",
            "--image",
            "ghcr.io/acme/pg:17.2",
            input="n\n",
        )

        assert result.exit_code != 0
        assert "ghcr.io/acme/pg:17.2" in "\n".join(log)
        assert "digest" in "\n".join(log).lower()

    def test_it_is_pinned_when_made(self, cli_world: World) -> None:
        cli_world.host.served["ghcr.io/acme/pg:17.2"] = DIGEST

        result = run(
            "create",
            "own",
            "--engine",
            "postgresql",
            "--version",
            "17",
            "--memory",
            "1g",
            "--image",
            "ghcr.io/acme/pg:17.2",
            "--yes",
            "--accept-untested",
        )

        assert result.exit_code == 0, result.output
        assert DbInstanceStore(cli_world.store).get("own").digest == DIGEST


class TestTheCatalog:
    def test_the_older_versions_are_listed_and_marked(
        self, cli_world: World, log: list[str]
    ) -> None:
        result = run("catalog")

        assert result.exit_code == 0, result.output
        text = "\n".join(log)
        assert "5.7 (no support)" in text and "8.4 (default)" in text

    def test_json_says_which_are_unsupported(self, cli_world: World) -> None:
        result = run("catalog", "--json")

        engines = {e["flavour"]: e for e in json.loads(result.stdout)["engines"]}
        assert {v["version"]: v["unsupported"] for v in engines["mysql"]["versions"]}["5.7"]

    def test_one_engine_can_be_asked_for(self, cli_world: World) -> None:
        result = run("catalog", "--engine", "mariadb", "--json")

        assert [e["flavour"] for e in json.loads(result.stdout)["engines"]] == ["mariadb"]
