# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A scheduled run that owes a copy is a run that went well (item 201).

The command a timer runs ends with the run's exit status, and a unit that ends in an error is a failed
unit. A copy that could not be sent and is owed is not a failure of the run (it is kept, sent again by
itself and said once for the destination), so the four commands that run a schedule say so in words,
do not call it sent, and exit 0.
"""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands import backup as backup_cli
from noust.cli.commands import backup_volumes, db_backups, self_backup
from noust.core.logger import Logger

PENDING = {
    "ok": False,
    "error": "Failed to upload\n  Details: i/o timeout",
    "pending": True,
    "next_attempt_at": "2026-11-17T02:15:00+00:00",
}


def test_the_run_of_an_application_s_schedule_does_not_fail_for_a_copy_that_is_owed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "noust.managers.backup_scheduler.run_schedule",
        lambda domain, verbose=False: {
            "backup_id": "shop_1",
            "destinations": {"nas": PENDING, "cloud": {"ok": True}},
        },
    )

    code = backup_cli._run_schedule(logger=Logger(verbose=False), domain="shop.example.com")

    said = capsys.readouterr().out
    assert code == 0
    assert "Sent to cloud" in said
    assert "Not sent to nas yet" in said and "02:15 UTC" in said
    assert "Sent to nas" not in said


def test_a_destination_that_failed_for_good_still_fails_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "noust.managers.backup_scheduler.run_schedule",
        lambda domain, verbose=False: {
            "backup_id": "shop_1",
            "destinations": {"nas": {"ok": False, "error": "its owing could not be kept"}},
        },
    )

    assert backup_cli._run_schedule(logger=Logger(verbose=False), domain="shop.example.com") == 1


class Volumes:
    def run_policy(self, domain: str, **kwargs: Any) -> dict[str, Any]:
        return {
            "backups": ["a"],
            "destinations": {"nas": {"a": PENDING}, "cloud": {"a": {"ok": True}}},
            "local_deleted": [],
            "removed_after_sending": [],
        }


def test_the_run_of_the_data_of_an_application_says_a_copy_is_owed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backup_volumes, "_manager", lambda ctx: Volumes())

    result = CliRunner().invoke(
        backup_volumes.volumes.commands["run"], ["shop.example.com"], obj=Context()
    )

    assert result.exit_code == 0, result.output
    assert "Not sent to nas yet" in result.output
    assert "Sent to cloud" in result.output
    assert "Sent to nas" not in result.output


class Bundles:
    def run_schedule(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "file": "noust.tar.gz.enc",
            "destinations": {"nas": PENDING},
            "local_deleted": [],
            "removed_after_sending": [],
        }


def test_the_run_of_noust_s_own_state_says_a_copy_is_owed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(self_backup, "_manager", lambda ctx: Bundles())

    result = CliRunner().invoke(self_backup.run_command, [], obj=Context())

    assert result.exit_code == 0, result.output
    assert "Not sent to nas yet" in result.output
    assert "Sent to nas" not in result.output


def test_the_four_commands_say_it_with_the_same_words() -> None:
    from noust.managers.backup_sending import pending_line

    assert pending_line("nas", PENDING) == (
        "Not sent to nas yet: it is kept on this server and is sent again at 2026-11-17 02:15 UTC"
    )
    assert db_backups is not None
