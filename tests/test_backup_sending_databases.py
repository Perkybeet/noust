# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scheduled run of a database's dump owes what it could not send (item 201).

The same decision as an application's backup and the data of an application
(``tests/test_backup_sending_kinds.py``), asked of the same
:class:`~noust.managers.backup_sending.Sender`: a run that was scheduled (it announces) keeps the dump as
owed instead of failing, says nothing yet and never deletes its only copy; a run somebody asked for by
hand still raises, as it always did, and owes nothing.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from noust.core import timeutil
from noust.core.exceptions import BackupError
from noust.core.notifications.model import OFF_BY_DEFAULT, Notification
from noust.core.pending_sends import PendingSends
from noust.managers.database.backups import DatabaseBackups
from tests.support.database_backup_support import RcloneRunner
from tests.support.database_backups import (  # noqa: F401
    FakeScheduler,
    _reset_store,
    add_destination,
    backups,
    dumps_dir,
    engine,
    runner,
    service,
    set_up_history,
)
from tests.support.notifier import config  # noqa: F401

NOW = datetime(2026, 11, 17, 2, 0, tzinfo=timezone.utc)


@pytest.fixture
def heard(monkeypatch: pytest.MonkeyPatch) -> list[Notification]:
    """What the notifier was handed, as the operator would hear it (the heartbeat ships off)."""
    notices: list[Notification] = []
    monkeypatch.setattr(
        "noust.core.notifier.Notifier.notify",
        lambda self, event: notices.append(event) if event.kind not in OFF_BY_DEFAULT else None,
    )
    return notices


@pytest.fixture(autouse=True)
def the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timeutil, "utc_now", lambda: NOW)


@pytest.fixture(autouse=True)
def _nas(_reset_store: None, backups: DatabaseBackups, runner: RcloneRunner) -> None:
    """The destination every test sends to, made once the store is this test's own."""
    add_destination(runner)


class TestADatabaseDump:
    def test_a_scheduled_run_keeps_the_dump_as_owed_and_does_not_fail(
        self, backups: DatabaseBackups, runner: RcloneRunner, heard: list[Notification]
    ) -> None:
        runner.refusing["nas"] = "Failed to copyto: dial tcp: i/o timeout"
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}], remote_only=True)

        result = backups.run_policy("postgresql", "shop", notify=True)

        assert result["destinations"]["nas"]["pending"] is True
        (row,) = PendingSends().for_owner("database", "postgresql/shop")
        assert row.item_id == f"postgresql:{result['dump']['name']}"
        assert heard == []
        assert len(backups.list_dumps("postgresql", "shop")) == 1

    def test_the_next_run_sends_what_is_owed_first(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        runner.refusing["nas"] = "Failed to copyto: dial tcp: i/o timeout"
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}])
        backups.run_policy("postgresql", "shop", notify=True)
        runner.refusing.clear()

        backups.run_policy("postgresql", "shop", notify=True)

        assert PendingSends().all() == []
        assert len(backups.remote_dumps("postgresql", "shop", "nas")) == 2

    def test_a_run_somebody_asked_for_still_raises(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        runner.refusing["nas"] = "Failed to copyto: dial tcp: i/o timeout"
        backups.set_policy("postgresql", "shop", destinations=[{"name": "nas"}])

        with pytest.raises(BackupError, match="could not be sent"):
            backups.run_policy("postgresql", "shop")

        assert PendingSends().all() == []
