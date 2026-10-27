# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Saying which control asks, and how to stop it asking.

The owner, holding every permission, was asked for an approval and for a reason and read it as a
permissions problem: both come from controls (``approvals``, ``change_reasons``) that apply to
everybody. A refusal that asks something of a person says which control asks, who turned it on, and
the command that turns it off, and a test runs each command it names.
"""

from __future__ import annotations

import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import audit_policy
from noust.cli.app import cli
from noust.core.audit import Actor
from noust.core.compliance import resolve
from noust.core.compliance.explain import why_on
from noust.core.config import Config
from noust.core.store import NoustStore, get_store


@pytest.fixture(autouse=True)
def operator(monkeypatch: pytest.MonkeyPatch) -> None:
    actor = Actor(kind="cli", id="0", name="root", via="sudo", source=None)
    monkeypatch.setattr(audit_policy.audit, "cli_actor", lambda: actor)


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = get_store()
    yield instance
    NoustStore.reset_instance()


def run(argv: list[str], input: str = "y\n") -> Any:
    return CliRunner().invoke(cli, argv, input=input)


class TestAControlTheOperatorSwitchedOn:
    def test_it_says_which_control_and_the_command_that_turns_it_off(
        self, real_config: Config
    ) -> None:
        real_config.set("approval.enabled", True)

        why = why_on("approvals", real_config)

        assert why.control == "approvals" and why.forced_by == ()
        assert why.command == "noust compliance set approvals off"
        assert "Settings > Controls" in why.sentence()
        assert "approvals" in why.sentence() and why.command in why.sentence()

    def test_the_command_it_names_works(self, real_config: Config) -> None:
        real_config.set("approval.enabled", True)
        why = why_on("approvals", real_config)

        result = run(shlex.split(why.command)[1:])

        assert result.exit_code == 0, result.output
        assert not resolve.is_on("approvals", real_config)


class TestAControlAFrameworkAsksFor:
    def test_it_names_the_framework_and_the_command_that_takes_it_off(
        self, real_config: Config
    ) -> None:
        assert run(["compliance", "enable", "ens-media", "--yes"]).exit_code == 0

        why = why_on("approvals", real_config)

        assert why.forced_by == ("ens-media",)
        assert why.command.startswith("noust compliance disable ens-media")
        assert "ens-media" in why.sentence()
        assert "cannot" in why.sentence() or "stays" in why.sentence()

    def test_the_command_it_names_works_and_carries_the_reason_that_server_asks_for(
        self, real_config: Config
    ) -> None:
        run(["compliance", "enable", "ens-media", "--yes"])
        why = why_on("approvals", real_config)

        result = run(shlex.split(why.command)[1:])

        assert result.exit_code == 0, result.output
        assert not resolve.is_on("approvals", real_config)

    def test_several_frameworks_are_all_named(self, real_config: Config) -> None:
        run(["compliance", "enable", "ens-media", "iso-27001", "--yes"])

        why = why_on("separation_of_duties", real_config)

        assert set(why.forced_by) == {"ens-media", "iso-27001"}
        assert "ens-media" in why.command and "iso-27001" in why.command


class TestAControlThatIsOff:
    def test_it_has_nothing_to_explain(self, real_config: Config) -> None:
        why = why_on("approvals", real_config)

        assert why.on is False
        assert why.sentence() == ""


class TestTheReasonTheCommandLineAsksFor:
    def test_it_says_which_control_asks_and_how_to_stop_it(self, real_config: Config) -> None:
        real_config.set("security.change_reasons", True)

        result = run(["compliance", "set", "session_idle_minutes", "10", "--yes"])

        assert result.exit_code == 2
        assert "change_reasons" in result.output
        assert "noust compliance set change_reasons off" in result.output
        assert "Settings > Controls" in result.output

    def test_the_command_it_names_works(self, real_config: Config) -> None:
        real_config.set("security.change_reasons", True)
        refused = run(["compliance", "set", "session_idle_minutes", "10", "--yes"])
        command = next(
            line.strip()
            for line in refused.output.splitlines()
            if line.strip().startswith("noust compliance set change_reasons")
        )

        result = run(shlex.split(command)[1:])

        assert result.exit_code == 0, result.output
        assert not resolve.is_on("change_reasons", real_config)
