# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db pitr``: recover PostgreSQL to an instant (checklist H-32).

The command parses and prints; the manager decides. What is pinned: what the operator types
reaches the manager as the values it knows (``none`` is no limit, a destination carries its own
retention, an instant is UTC unless it says otherwise), nothing is replaced without being asked
(``--yes``) or after the operator said no, ``--plan`` changes nothing, a refusal prints its fix
and exits non-zero, ``--json`` is JSON alone, and reading is a read the audit policy does not
record while the upkeep timer's command needs no reason.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli import app as cli_app
from noust.cli.audit_policy import SYSTEM_ENTRY_POINTS, is_read_only
from noust.cli.commands import db_pitr
from noust.core.exceptions import DatabaseBackupError, ValidationError
from noust.managers.database.pitr import RestorePlan
from noust.managers.database.pitr_archive import BaseBackup

BASE = BaseBackup(
    id="base-20261017T120000Z",
    started_at=datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc),
    finished_at=datetime(2026, 10, 17, 12, 0, 9, tzinfo=timezone.utc),
    start_wal_file="000000010000000000000002",
    timeline=1,
    size=100,
    sha256="0" * 64,
    server_version="160004",
    system_id="7430000000000000001",
)


class FakePitr:
    """Stands in for the manager: records what it was asked."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []
        self.restart = True
        self.refuse: Exception | None = None
        self.enabled = True

    def status(self) -> dict[str, Any]:
        if not self.enabled:
            return {"engine": "postgresql", "enabled": False, "policy": None, "warnings": []}
        return {
            "engine": "postgresql",
            "enabled": True,
            "policy": {
                "base_every_hours": 168,
                "retention_count": 2,
                "retention_days": None,
                "destinations": [{"name": "nas"}],
            },
            "bases": [BASE.to_dict()],
            "archive": {"path": "/var/lib/postgresql/noust-wal/7430", "files": 3, "bytes": 3000},
            "window": {
                "earliest": "2026-10-17T12:00:09+00:00",
                "latest": "2026-10-17T12:30:00+00:00",
            },
            "archiver": {"archived": 3, "failed": 0, "last_archived_at": "2026-10-17T12:30:00Z"},
            "timer": {"installed": True, "next_run": "Sat 12:35"},
            "warnings": ["The newest base backup is old."],
        }

    def plan_enable(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("plan_enable", kwargs))
        if self.refuse:
            raise self.refuse
        return {
            "settings": {"archive_mode": "on", "archive_timeout": "60s"},
            "restart": self.restart,
            "archive_path": "/var/lib/postgresql/noust-wal/7430",
            "base_every_hours": kwargs["base_every_hours"],
        }

    def enable(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("enable", kwargs))
        return self.status()

    def disable(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("disable", kwargs))
        return {"disabled": True, "deleted": 3 if kwargs["delete_archive"] else 0}

    def take_base(self) -> BaseBackup:
        self.calls.append(("take_base", None))
        return BASE

    def sync(self) -> dict[str, Any]:
        self.calls.append(("sync", None))
        return {"nas": {"uploaded": ["base-x"], "segments": ["a", "b"], "deleted": []}}

    def fetch(self, destination: str) -> dict[str, Any]:
        self.calls.append(("fetch", destination))
        return {"bases": ["base-x"], "segments": ["a"]}

    def run(self) -> dict[str, Any]:
        self.calls.append(("run", None))
        return {"base": None, "pruned": {}, "sync": {}}

    def plan_restore(self, target: datetime | None, *, base_id: str | None = None) -> RestorePlan:
        self.calls.append(("plan_restore", (target, base_id)))
        if self.refuse:
            raise self.refuse
        return RestorePlan(
            base=BASE,
            target=target,
            data_directory="/var/lib/postgresql/16/main",
            saved_as="/var/lib/postgresql/16/main.noust-before-recovery-20261017T130000Z",
            segments=4,
            last_archived_at=datetime(2026, 10, 17, 12, 30, tzinfo=timezone.utc),
            warnings=[] if target else ["Recovery to the end of the log."],
        )

    def restore(self, target: datetime | None, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("restore", (target, kwargs)))
        return {
            "recovered_to": None,
            "base": BASE.id,
            "saved_as": "/var/lib/postgresql/16/main.noust-before-recovery-20261017T130000Z",
            "seconds": 12,
        }


@pytest.fixture
def cli_runner() -> CliRunner:
    """A Click test runner."""
    return CliRunner()


@pytest.fixture
def pitr(runner, monkeypatch: pytest.MonkeyPatch) -> FakePitr:
    """The fake manager, in the place every command here builds its own."""
    fake = FakePitr()
    monkeypatch.setattr(db_pitr, "_pitr", lambda logger: fake)
    return fake


def invoke(cli_runner: CliRunner, *args: str, input: str | None = None) -> Any:
    """Run ``noust db pitr ...``."""
    return cli_runner.invoke(cli_app.cli, ["db", "pitr", *args], input=input)


def test_the_status_shows_the_window_the_base_backups_and_what_to_look_at(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(cli_runner, "status")

    assert result.exit_code == 0, result.output
    assert (
        "Recoverable:   from 2026-10-17T12:00:09+00:00 to 2026-10-17T12:30:00+00:00"
        in result.output
    )
    assert "base-20261017T120000Z" in result.output
    assert "Destinations:  nas" in result.output
    assert "last 2 base backups" in result.output
    assert "The newest base backup is old." in result.output


def test_the_status_of_an_engine_that_is_off_says_how_to_turn_it_on(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    pitr.enabled = False

    result = invoke(cli_runner, "status")

    assert "off for PostgreSQL" in result.output and "noust db pitr enable" in result.output


def test_the_status_as_json_is_json_alone(cli_runner: CliRunner, pitr: FakePitr) -> None:
    result = invoke(cli_runner, "status", "--json")

    assert json.loads(result.output)["bases"][0]["id"] == BASE.id


def test_what_the_operator_types_reaches_the_manager_by_the_values_it_knows(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(
        cli_runner,
        "enable", "--every", "24", "--keep", "3", "--keep-days", "none",
        "--to", "nas:5:30", "--to", "offsite", "--yes",
    )  # fmt: skip

    assert result.exit_code == 0, result.output
    expected = {
        "base_every_hours": 24,
        "retention_count": 3,
        "retention_days": None,
        "destinations": [
            {"name": "nas", "retention_count": 5, "retention_days": 30},
            {"name": "offsite", "retention_count": None, "retention_days": None},
        ],
    }
    assert ("plan_enable", expected) in pitr.calls
    assert ("enable", {"confirmed": True, **expected}) in pitr.calls


def test_the_defaults_are_a_week_apart_and_seven_days_back(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    invoke(cli_runner, "enable", "--yes")

    kwargs = pitr.calls[0][1]
    assert kwargs["base_every_hours"] == 168 and kwargs["retention_days"] == 7
    assert kwargs["retention_count"] is None


def test_the_restart_is_asked_about_and_a_no_changes_nothing(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(cli_runner, "enable", input="n\n")

    assert result.exit_code == 0, result.output
    assert "Restart PostgreSQL now?" in result.output and "Nothing was changed." in result.output
    assert [name for name, _ in pitr.calls] == ["plan_enable"]


def test_nothing_is_asked_when_nothing_restarts(cli_runner: CliRunner, pitr: FakePitr) -> None:
    pitr.restart = False

    result = invoke(cli_runner, "enable")

    assert result.exit_code == 0 and "Restart PostgreSQL now?" not in result.output
    assert [name for name, _ in pitr.calls] == ["plan_enable", "enable"]


def test_a_plan_says_what_would_be_done_and_does_it_not(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(cli_runner, "enable", "--plan")

    assert result.exit_code == 0, result.output
    assert "archive_mode = on" in result.output and "Restart:   yes, once" in result.output
    assert [name for name, _ in pitr.calls] == ["plan_enable"]


def test_a_plan_as_json_is_json_alone(cli_runner: CliRunner, pitr: FakePitr) -> None:
    result = invoke(cli_runner, "enable", "--plan", "--json")

    assert json.loads(result.output)["restart"] is True


def test_a_refusal_prints_its_fix_and_exits_non_zero(cli_runner: CliRunner, pitr: FakePitr) -> None:
    pitr.refuse = ValidationError(
        "PostgreSQL already archives its log", details="Turn that archiving off first."
    )

    result = invoke(cli_runner, "enable", "--yes")

    assert result.exit_code == 1
    assert (
        "already archives its log" in result.output and "Turn that archiving off" in result.output
    )
    assert [name for name, _ in pitr.calls] == ["plan_enable"]


@pytest.mark.parametrize("option", ["--keep", "--keep-days"])
def test_a_retention_that_is_not_a_number_is_a_usage_error(
    cli_runner: CliRunner, pitr: FakePitr, option: str
) -> None:
    result = invoke(cli_runner, "enable", option, "lots", "--yes")

    assert result.exit_code != 0 and pitr.calls == []


def test_turning_it_off_asks_first(cli_runner: CliRunner, pitr: FakePitr) -> None:
    refused = invoke(cli_runner, "disable", input="n\n")
    accepted = invoke(cli_runner, "disable", "--delete-archive", "--yes")

    assert "Nothing was changed." in refused.output
    assert pitr.calls == [("disable", {"confirmed": True, "delete_archive": True})]
    assert accepted.exit_code == 0 and "Deleted 3 files" in accepted.output


def test_a_base_backup_is_taken_and_reported(cli_runner: CliRunner, pitr: FakePitr) -> None:
    result = invoke(cli_runner, "base")

    assert result.exit_code == 0 and "base-20261017T120000Z" in result.output
    assert pitr.calls == [("take_base", None)]


def test_the_archive_is_sent_and_brought_back(cli_runner: CliRunner, pitr: FakePitr) -> None:
    sent = invoke(cli_runner, "sync")
    fetched = invoke(cli_runner, "fetch", "nas", "--json")

    assert "nas: 1 base backups and 2 segments sent" in sent.output
    assert json.loads(fetched.output) == {"bases": ["base-x"], "segments": ["a"]}
    assert ("fetch", "nas") in pitr.calls


def test_a_recovery_needs_an_instant_or_the_end_of_the_log_not_both_and_not_neither(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    for args in ([], ["--time", "2026-10-17 12:15:00", "--latest"]):
        result = invoke(cli_runner, "restore", *args)
        assert result.exit_code == 1 and "--time INSTANT or --latest" in result.output

    assert pitr.calls == []


def test_an_instant_is_utc_unless_it_carries_an_offset(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    invoke(cli_runner, "restore", "--time", "2026-10-17 14:15:00+02:00", "--plan")

    assert pitr.calls[0] == (
        "plan_restore",
        (datetime(2026, 10, 17, 12, 15, tzinfo=timezone.utc), None),
    )


def test_a_recovery_shows_the_plan_and_asks_before_it_replaces_anything(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    refused = invoke(cli_runner, "restore", "--time", "2026-10-17 12:15:00", input="n\n")

    assert refused.exit_code == 0, refused.output
    assert "Base backup:   base-20261017T120000Z" in refused.output
    assert "is renamed to /var/lib/postgresql/16/main.noust-before-recovery-" in refused.output
    assert "Replace the cluster in /var/lib/postgresql/16/main" in refused.output
    assert "Nothing was changed." in refused.output
    assert [name for name, _ in pitr.calls] == ["plan_restore"]


def test_a_confirmed_recovery_names_where_the_old_cluster_was_kept(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(
        cli_runner, "restore", "--time", "2026-10-17 12:15:00", "--base", BASE.id, "--yes"
    )

    assert result.exit_code == 0, result.output
    assert "Recovered in 12 seconds" in result.output
    assert "kept at /var/lib/postgresql/16/main.noust-before-recovery-" in result.output
    target = datetime(2026, 10, 17, 12, 15, tzinfo=timezone.utc)
    assert pitr.calls[-1] == ("restore", (target, {"confirmed": True, "base_id": BASE.id}))


def test_a_recovery_to_the_end_of_the_log_says_what_that_costs(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(cli_runner, "restore", "--latest", "--plan")

    assert result.exit_code == 0
    assert "the end of the archived log" in result.output
    assert "Recovery to the end of the log." in result.output
    assert pitr.calls[0] == ("plan_restore", (None, None))


def test_a_recovery_that_failed_prints_the_engines_own_words(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    pitr.refuse = DatabaseBackupError(
        "The log has a gap", details="Missing: 000000010000000000000005"
    )

    result = invoke(cli_runner, "restore", "--latest", "--yes")

    assert result.exit_code == 1
    assert "The log has a gap" in result.output and "000000010000000000000005" in result.output


def test_an_instant_that_is_not_one_is_refused_with_how_to_write_one(
    cli_runner: CliRunner, pitr: FakePitr
) -> None:
    result = invoke(cli_runner, "restore", "--time", "yesterday", "--yes")

    assert result.exit_code == 1 and "2026-10-17 12:34:56" in result.output
    assert pitr.calls == []


def test_the_upkeep_runs_what_the_timer_runs(cli_runner: CliRunner, pitr: FakePitr) -> None:
    result = invoke(cli_runner, "run")

    assert result.exit_code == 0 and pitr.calls == [("run", None)]


def test_reading_is_not_recorded_and_the_timers_command_needs_no_reason() -> None:
    group = db_pitr.pitr

    assert is_read_only(group.commands["status"], "db pitr status", {})
    assert not is_read_only(group.commands["restore"], "db pitr restore", {})
    assert not is_read_only(group.commands["base"], "db pitr base", {})
    assert "db pitr run" in SYSTEM_ENTRY_POINTS
    assert "db pitr restore" not in SYSTEM_ENTRY_POINTS
