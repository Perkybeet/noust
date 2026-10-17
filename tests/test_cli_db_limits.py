# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db limits`` (checklist C-30): what an engine on the host may use.

The command parses and prints; the service decides. What is pinned: the limits the operator typed
reach the service as the names it knows, ``--remove`` reaches it as a clear list, a memory limit
below the engine's use is only confirmed with ``--yes``, a failure prints the engine's journal
and exits non-zero, and showing the limits is a read the audit policy does not record.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import is_read_only
from noust.cli.commands import db as db_cli
from noust.core.exceptions import ConfirmationRequired, DatabaseEngineError
from noust.managers.database.host_limits import LimitsOutcome, LimitsStatus, LimitValues
from noust.managers.database.service import DatabaseService


@pytest.fixture
def cli_runner() -> CliRunner:
    """A Click test runner."""
    return CliRunner()


def status(**fields: Any) -> LimitsStatus:
    """The limits of PostgreSQL with 2 GB set, the kernel agreeing about all but swap."""
    data: dict[str, Any] = {
        "engine": "postgresql",
        "display_name": "PostgreSQL",
        "unit": "postgresql@16-main",
        "file": "/etc/systemd/system/postgresql@16-main.service.d/90-noust-limits.conf",
        "configured": LimitValues(memory_mb=2048, cpus=1.5),
        "effective": LimitValues(memory_mb=2048, cpus=1.5, tasks=4096),
        "enforced": {"memory_mb": True, "cpus": True, "swap_mb": None, "tasks": False},
        "memory_in_use_mb": 310,
        "server_memory_mb": 8192,
        "cpus": 4,
        "minimum_memory_mb": 256,
        "elsewhere": ["TasksMax"],
    }
    data.update(fields)
    return LimitsStatus(**data)


def outcome(**fields: Any) -> LimitsOutcome:
    """A change of the memory limit."""
    data: dict[str, Any] = {
        "engine": "postgresql",
        "display_name": "PostgreSQL",
        "file": "/etc/systemd/system/postgresql@16-main.service.d/90-noust-limits.conf",
        "changed": ["memory_mb"],
        "configured": LimitValues(memory_mb=4096),
        "warnings": [],
    }
    data.update(fields)
    return LimitsOutcome(**data)


def test_the_limits_are_shown_with_what_systemd_holds_and_what_the_kernel_enforces(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(DatabaseService, "engine_limits", lambda self, engine: status())

    result = cli_runner.invoke(cli_app.cli, ["db", "limits", "postgresql"])

    assert result.exit_code == 0, result.output
    assert "postgresql@16-main" in result.output
    assert "2048 MB" in result.output and "1.5" in result.output
    assert "NOT enforced" in result.output
    assert "It uses 310 MB now" in result.output
    assert "Also set outside Noust's drop-in: TasksMax" in result.output


def test_the_limits_as_json(cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DatabaseService, "engine_limits", lambda self, engine: status())

    result = cli_runner.invoke(cli_app.cli, ["db", "limits", "postgresql", "--json"])

    body = json.loads(result.output)
    assert body["configured"] == {"memory_mb": 2048, "cpus": 1.5, "swap_mb": None, "tasks": None}
    assert body["enforced"]["tasks"] is False


def test_what_the_operator_types_reaches_the_service_by_the_names_it_knows(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, dict[str, Any], list[str], bool]] = []

    def change(self, engine, changes=None, *, clear=(), confirm=False):
        calls.append((engine, dict(changes or {}), list(clear), confirm))
        return outcome()

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)

    result = cli_runner.invoke(
        cli_app.cli,
        [
            "db", "limits", "postgresql", "--memory", "4g", "--cpus", "2", "--swap", "0",
            "--tasks", "4096", "--remove", "cpus", "--yes",
        ],
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    assert calls == [
        (
            "postgresql",
            {"memory_mb": "4g", "cpus": "2", "swap_mb": "0", "tasks": "4096"},
            ["cpus"],
            True,
        )
    ]
    assert "memory_mb changed" in result.output and "without a restart" in result.output


def test_a_memory_limit_below_the_use_shows_the_warning_and_asks_for_yes(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(self, engine, changes=None, *, clear=(), confirm=False):
        raise ConfirmationRequired(["PostgreSQL uses 900 MB now, more than the 512 MB limit."])

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)

    result = cli_runner.invoke(cli_app.cli, ["db", "limits", "postgresql", "--memory", "512"])

    assert result.exit_code == 1
    assert "uses 900 MB now" in result.output
    assert "Nothing was changed" in result.output and "--yes" in result.output


def test_a_failure_prints_the_engines_journal_and_exits_non_zero(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    def change(self, engine, changes=None, *, clear=(), confirm=False):
        raise DatabaseEngineError(
            "PostgreSQL stopped answering under the new limits",
            output="FATAL:  the database system is shutting down",
        )

    monkeypatch.setattr(DatabaseService, "change_engine_limits", change)

    result = cli_runner.invoke(cli_app.cli, ["db", "limits", "postgresql", "--memory", "1g"])

    assert result.exit_code == 1
    assert "stopped answering" in result.output
    assert "the database system is shutting down" in result.output


def test_a_change_that_changes_nothing_says_so(
    cli_runner: CliRunner, runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        DatabaseService,
        "change_engine_limits",
        lambda self, engine, changes=None, *, clear=(), confirm=False: outcome(changed=[]),
    )

    result = cli_runner.invoke(cli_app.cli, ["db", "limits", "postgresql", "--memory", "4g"])

    assert result.exit_code == 0
    assert "already has those limits" in result.output


def test_a_limit_that_cannot_be_removed_is_a_usage_error(cli_runner: CliRunner, runner) -> None:
    result = cli_runner.invoke(cli_app.cli, ["db", "limits", "postgresql", "--remove", "shares"])

    assert result.exit_code == 2


def test_showing_the_limits_is_a_read_and_setting_them_is_not() -> None:
    command = db_cli.cli.commands["limits"]

    assert is_read_only(command, "db limits", {"engine": "postgresql"})
    assert not is_read_only(command, "db limits", {"engine": "postgresql", "memory": "4g"})
    assert not is_read_only(command, "db limits", {"engine": "postgresql", "clear": ("cpus",)})
