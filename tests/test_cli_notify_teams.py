# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""``noust notify channel set --team``: a channel that belongs to a team, from the terminal."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.store import NoustStore
from noust.core.teams import TeamStore

SLACK = "https://hooks.slack.test/services/T0/B0/acme-secret"


@pytest.fixture
def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    monkeypatch.setattr(
        "noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "etc" / "noust" / "config.yaml"
    )
    Config.reset_instance()
    NoustStore.reset_instance()
    TeamStore(NoustStore(tmp_path / "noust.db")).create_team("Acme", slug="acme")
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None):  # type: ignore[no-untyped-def]
        Config.reset_instance()
        return runner.invoke(root_cli, list(args), input=input)

    try:
        yield invoke
    finally:
        Config.reset_instance()
        NoustStore.reset_instance()


def test_a_channel_made_for_a_team_is_listed_with_it(run) -> None:  # type: ignore[no-untyped-def]
    made = run(
        "notify",
        "channel",
        "set",
        "alerts",
        "--team",
        "acme",
        "--type",
        "slack",
        "--secret-stdin",
        "webhook_url",
        input=SLACK,
    )

    listed = json.loads(run("--json", "notify", "list").output)

    assert made.exit_code == 0, made.output
    assert SLACK not in made.output
    owned = [view for view in listed if view["team"] == "acme"]
    assert len(owned) == 1 and owned[0]["type"] == "slack"
    assert [view["team"] for view in listed if view["name"] == "slack"] == [None]


def test_the_table_has_a_team_column(run) -> None:  # type: ignore[no-untyped-def]
    run(
        "notify", "channel", "set", "alerts", "--team", "acme", "--type", "slack",
        "--secret-stdin", "webhook_url", input=SLACK,
    )  # fmt: skip

    result = run("notify", "list")

    assert "Team" in result.output and "acme" in result.output


def test_a_team_that_does_not_exist_is_refused(run) -> None:  # type: ignore[no-untyped-def]
    result = run(
        "notify", "channel", "set", "alerts", "--team", "nope", "--type", "slack",
        "--secret-stdin", "webhook_url", input=SLACK,
    )  # fmt: skip

    assert result.exit_code != 0


def test_email_is_not_a_teams(run) -> None:  # type: ignore[no-untyped-def]
    result = run("notify", "channel", "set", "mail", "--team", "acme", "--type", "email")

    assert result.exit_code != 0
    assert "mail account" in result.output


def test_a_team_channel_is_removed_with_the_team_option(run) -> None:  # type: ignore[no-untyped-def]
    run(
        "notify", "channel", "set", "alerts", "--team", "acme", "--type", "slack",
        "--secret-stdin", "webhook_url", input=SLACK,
    )  # fmt: skip

    result = run("notify", "channel", "remove", "alerts", "--team", "acme")
    listed = json.loads(run("--json", "notify", "list").output)

    assert result.exit_code == 0, result.output
    assert not [view for view in listed if view["team"]]
