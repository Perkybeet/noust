# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup resend`` and what ``noust backup list`` says of a copy that is owed (item 201).

The command is the pass a timer runs every few minutes (and the central's own scheduler where there are
no timers): it sends what is due, or with ``--now`` everything that is owed. That a copy stays owed is
the ordinary state of a destination that is down, so the command ends well with it; it fails only when
the pass itself could not be made.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import Context
from noust.cli.commands import backup_resend
from noust.cli.commands.backup import _print_backup_table
from noust.core.logger import Logger
from noust.core.pending_sends import PendingSends
from noust.core.store import NoustStore
from noust.managers.backup_manager import BackupMetadata
from noust.managers.backup_resend import ResendReport

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    yield instance
    NoustStore.reset_instance()


class Passes:
    """A resender that records how it was asked and answers with a report."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []
        self.report = ResendReport(sent=2, failed=1, skipped=3, dropped=0, owed=4)
        self.error: Exception | None = None

    def __call__(self, *args: Any, **kwargs: Any) -> Passes:
        return self

    def run(self, **kwargs: Any) -> ResendReport:
        self.asked.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.report


@pytest.fixture
def passes(monkeypatch: pytest.MonkeyPatch) -> Passes:
    fake = Passes()
    monkeypatch.setattr(backup_resend, "Resender", fake)
    return fake


def invoke(*args: str) -> Any:
    return CliRunner().invoke(backup_resend.resend, list(args), obj=Context())


class TestTheCommand:
    def test_it_sends_what_is_due(self, passes: Passes) -> None:
        result = invoke()

        assert result.exit_code == 0, result.output
        assert passes.asked == [{"force": False}]

    def test_with_now_it_sends_everything_that_is_owed(self, passes: Passes) -> None:
        result = invoke("--now")

        assert result.exit_code == 0, result.output
        assert passes.asked == [{"force": True}]

    def test_a_copy_that_is_still_owed_is_not_a_failure(self, passes: Passes) -> None:
        result = invoke()

        assert result.exit_code == 0
        assert "4" in result.output  # what is still owed

    def test_the_json_is_the_report(self, passes: Passes) -> None:
        result = invoke("--json")

        assert json.loads(result.output) == passes.report.to_dict()

    def test_a_pass_that_could_not_be_made_fails(self, passes: Passes) -> None:
        import sqlite3

        passes.error = sqlite3.OperationalError("database is locked")

        result = invoke()

        assert result.exit_code == 1
        assert "database is locked" in result.output

    def test_it_is_a_command_of_the_backup_group(self) -> None:
        from noust.cli.commands.backup import backup

        assert "resend" in backup.commands


def metadata(backup_id: str) -> BackupMetadata:
    return BackupMetadata(
        id=backup_id,
        domain="shop.example.com",
        app_name="shop-example-com",
        created_at="2026-11-17T01:00:00",
        size_bytes=1024,
        app_type="static",
        version="4.3.0",
        description="",
        includes_env=True,
        includes_node_modules=False,
    )


class TestTheList:
    def owe(self, item: str, destination: str = "nas") -> None:
        PendingSends().defer(
            "app",
            item,
            destination,
            owner="shop.example.com",
            copy_created_at=NOW,
            reason="i/o timeout",
            permanent=False,
            failure_id=None,
            now=NOW,
            next_attempt_at=NOW + timedelta(minutes=15),
        )

    def test_a_copy_that_was_not_sent_says_where_and_when_it_is_tried_again(
        self, store: NoustStore, capsys: pytest.CaptureFixture[str]
    ) -> None:
        self.owe("shop-example-com_a")

        _print_backup_table(
            [metadata("shop-example-com_a"), metadata("shop-example-com_b")],
            Logger(verbose=False),
            owed=PendingSends().for_owner("app", "shop.example.com"),
        )

        lines = capsys.readouterr().out.splitlines()
        owed = next(line for line in lines if "shop-example-com_a" in line)
        sent = next(line for line in lines if "shop-example-com_b" in line)
        assert "not sent to nas" in owed and "02:15 UTC" in owed
        assert "not sent" not in sent

    def test_the_json_carries_what_is_owed(self, store: NoustStore) -> None:
        from noust.core.pending_sends import owed_by_copy

        self.owe("shop-example-com_a")

        by_copy = owed_by_copy("app")

        (entry,) = by_copy["shop-example-com_a"]
        assert entry.destination == "nas"
        assert owed_by_copy("volume") == {}

    def test_a_store_that_cannot_be_read_costs_the_list_its_note_and_nothing_else(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sqlite3

        from noust.core.pending_sends import owed_by_copy

        def broken(self: PendingSends, kind: str) -> list[Any]:
            raise sqlite3.OperationalError("database is locked")

        monkeypatch.setattr(PendingSends, "for_owner", broken)
        monkeypatch.setattr(PendingSends, "all", lambda self: broken(self, "app"))

        assert owed_by_copy("app") == {}
