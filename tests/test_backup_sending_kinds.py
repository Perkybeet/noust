# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scheduled run of every kind of copy owes what it could not send (item 201).

An application's backup is held by ``tests/test_backup_night.py`` and a database's dump by
``tests/test_backup_sending_databases.py``. The data of an application, a
database's dump and Noust's own state take the same decision from the same
:class:`~noust.managers.backup_sending.Sender`: a run that was scheduled (it announces) keeps the copy
as owed instead of failing, says nothing yet, and never deletes its only copy; a run somebody asked for
by hand (it does not announce) still raises, as it always did, and still owes nothing.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from noust.core import timeutil
from noust.core.exceptions import BackupError
from noust.core.notifications.model import OFF_BY_DEFAULT, Notification
from noust.core.pending_sends import PendingSends
from noust.managers.self_backup import SelfBackupManager
from noust.managers.volume_backups import VolumeBackupManager
from tests.support.database_backup_support import RcloneRunner
from tests.support.notifier import config  # noqa: F401
from tests.support.self_backup import PASSPHRASE, FakeTimers, old  # noqa: F401
from tests.support.self_backup import Runner as SelfRunner
from tests.support.self_backup import audited as self_audited  # noqa: F401
from tests.support.self_backup import runner as self_runner  # noqa: F401
from tests.support.volume_backups import (  # noqa: F401
    DOMAIN,
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)

pytestmark = pytest.mark.allow_subprocess

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


def add_nas(runner: RcloneRunner, name: str = "nas") -> None:
    from noust.managers.backup_destination_files import DestinationFileManager

    DestinationFileManager(runner=runner).add(
        name, "sftp", {"host": "nas.example.net", "user": "noust", "pass": "s3cr3t"}
    )


# ============================================================ data (volumes)


class TestTheDataOfAnApplication:
    def policy(self, manager: VolumeBackupManager, **fields: Any) -> None:
        manager.set_policy(
            DOMAIN, targets=["path:uploads"], destinations=[{"name": "nas"}], **fields
        )

    def test_a_scheduled_run_keeps_the_copy_as_owed_and_does_not_fail(
        self, manager: VolumeBackupManager, runner: RcloneRunner, heard: list[Notification]
    ) -> None:
        add_nas(runner)
        runner.refusing["nas"] = "dial tcp: i/o timeout"
        self.policy(manager)

        result = manager.run_policy(DOMAIN, notify=True)

        (taken,) = result["backups"]
        entry = result["destinations"]["nas"][taken]
        assert entry["ok"] is False and entry["pending"] is True
        assert [row.item_id for row in PendingSends().for_owner("volume", DOMAIN)] == [taken]
        assert heard == []
        assert len(manager.list_backups(DOMAIN)) == 1

    def test_it_is_never_the_only_copy_deleted_for_having_been_sent(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_nas(runner)
        runner.refusing["nas"] = "dial tcp: i/o timeout"
        self.policy(manager, remote_only=True)

        result = manager.run_policy(DOMAIN, notify=True)

        assert result["removed_after_sending"] == []
        assert len(manager.list_backups(DOMAIN)) == 1

    def test_the_next_run_sends_what_is_owed_first(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_nas(runner)
        runner.refusing["nas"] = "dial tcp: i/o timeout"
        self.policy(manager)
        (first,) = manager.run_policy(DOMAIN, notify=True)["backups"]
        runner.refusing.clear()

        second = manager.run_policy(DOMAIN, notify=True)["backups"]

        assert PendingSends().all() == []
        assert len(second) == 1
        remote = sorted(p.name for p in (runner.root / "nas").rglob("*.tar.gz"))
        assert len(remote) == 2 and any(first in name for name in remote)

    def test_the_policy_says_ok_while_a_copy_is_only_waiting_and_failed_when_it_was_refused(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_nas(runner)
        self.policy(manager)

        runner.refusing["nas"] = "dial tcp: i/o timeout"
        manager.run_policy(DOMAIN, notify=True)
        waiting = manager.records.policy(DOMAIN)
        runner.refusing["nas"] = "Failed to copyto: AccessDenied: Access Denied (403)"
        manager.run_policy(DOMAIN, notify=True)
        refused = manager.records.policy(DOMAIN)

        assert waiting is not None and waiting.last_status == "ok"
        assert refused is not None and refused.last_status == "failed"
        assert "refused by: nas" in (refused.last_error or "")

    def test_a_run_somebody_asked_for_still_raises_and_owes_nothing(
        self, manager: VolumeBackupManager, runner: RcloneRunner
    ) -> None:
        add_nas(runner)
        runner.refusing["nas"] = "dial tcp: i/o timeout"
        self.policy(manager)

        with pytest.raises(BackupError, match="could not be sent"):
            manager.run_policy(DOMAIN)

        assert PendingSends().all() == []


# ============================================================ Noust's own state


class TestNoustsOwnState:
    @pytest.fixture
    def own(self, old: Any, self_runner: SelfRunner) -> SelfBackupManager:
        from noust.managers.backup_destination_files import DestinationFileManager

        return old.manager(
            self_runner,
            scheduler=FakeTimers(),  # type: ignore[arg-type]
            destinations=DestinationFileManager(runner=self_runner),
        )

    def test_a_scheduled_run_keeps_the_bundle_as_owed_and_does_not_fail(
        self, own: SelfBackupManager, self_runner: SelfRunner, heard: list[Notification]
    ) -> None:
        add_nas(self_runner)
        self_runner.refusing["nas"] = "dial tcp: i/o timeout"
        own.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}], remote_only=True)

        result = own.run_schedule(notify=True)

        assert result["destinations"]["nas"]["pending"] is True
        (row,) = PendingSends().for_owner("noust", "Noust")
        assert row.item_id == result["file"]
        assert heard == []
        assert len(own.entries()) == 1

    def test_the_next_run_sends_what_is_owed_first(
        self, own: SelfBackupManager, self_runner: SelfRunner
    ) -> None:
        add_nas(self_runner)
        self_runner.refusing["nas"] = "dial tcp: i/o timeout"
        own.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}])
        own.run_schedule(notify=True)
        self_runner.refusing.clear()

        own.run_schedule(notify=True)

        assert PendingSends().all() == []

    def test_a_run_somebody_asked_for_still_raises(
        self, own: SelfBackupManager, self_runner: SelfRunner
    ) -> None:
        add_nas(self_runner)
        self_runner.refusing["nas"] = "dial tcp: i/o timeout"
        own.set_schedule(passphrase=PASSPHRASE, destinations=[{"name": "nas"}])

        with pytest.raises(BackupError, match="could not be sent"):
            own.run_schedule()

        assert PendingSends().all() == []
