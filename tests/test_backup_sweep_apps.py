# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's backups are pruned without waiting for a run that went well (checklist H-04).

Retention by age used to delete every scheduled backup older than the limit, the newest included:
harmless while it ran right after a new backup, and the way an application ends with no backup at
all once it runs without one. The newest copy that is not known to be bad is always spared, a
schedule's retention is applied by a failed run and by a separate sweep, and a manual backup is
never the schedule's.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import json
from typing import Any

import pytest

from noust.core.exceptions import BackupError
from noust.core.store import BackupScheduleRecord, get_store
from noust.managers.backup_manager import SCHEDULED_TAG, BackupManager
from noust.managers.backup_scheduler import run_schedule
from tests.test_backup_safety_22 import (  # noqa: F401
    APP,
    DOMAIN,
    _remaining,
    _reset_store,
    _write_backup,
    manager,
)


def mark_bad(manager: BackupManager, backup_id: str) -> None:
    """Record that a backup failed its verification."""
    sidecar = manager.backup_dir / APP / f"{backup_id}.json"
    data = json.loads(sidecar.read_text())
    data["verified_ok"] = False
    sidecar.write_text(json.dumps(data))


class TestTheNewestIsSpared:
    def test_an_age_limit_never_takes_the_newest_scheduled_backup(
        self, manager: BackupManager
    ) -> None:
        # Every one is older than the limit: the schedule has not run for weeks.
        scheduled = [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]) for day in (3, 4, 5)]

        deleted = manager.rotate_by_policy(APP, max_age_days=1, tag=SCHEDULED_TAG)

        assert deleted == 2
        assert _remaining(manager) == {scheduled[2].id}

    def test_a_newest_backup_that_failed_its_check_does_not_push_the_good_ones_out(
        self, manager: BackupManager
    ) -> None:
        scheduled = [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]) for day in (3, 4, 5)]
        mark_bad(manager, scheduled[2].id)

        manager.rotate_by_policy(APP, max_count=1, tag=SCHEDULED_TAG)

        assert _remaining(manager) == {scheduled[2].id, scheduled[1].id}

    def test_a_manual_backup_is_still_never_touched(self, manager: BackupManager) -> None:
        manual = _write_backup(manager, 1, [])
        _write_backup(manager, 2, [SCHEDULED_TAG, "auto"])

        manager.rotate_by_policy(APP, max_age_days=1, tag=SCHEDULED_TAG)

        assert manual.id in _remaining(manager)


class TestAFailedScheduledRun:
    def save(self, **kwargs: Any) -> None:
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain=DOMAIN,
                schedule="daily",
                include_databases=True,
                retention_count=kwargs.get("count"),
                retention_days=kwargs.get("days"),
                destinations=[],
            )
        )

    def test_a_backup_that_cannot_be_taken_prunes_by_the_schedule_all_the_same(
        self, manager: BackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        scheduled = [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]) for day in (3, 4, 5)]
        self.save(count=2)

        def refuse(self: BackupManager, **kwargs: object) -> None:
            raise BackupError("tar: no space left on device")

        monkeypatch.setattr(BackupManager, "create", refuse)
        monkeypatch.setattr("noust.managers.backup_scheduler.BackupManager", lambda **kw: manager)

        with pytest.raises(BackupError, match="no space left"):
            run_schedule(DOMAIN)

        assert _remaining(manager) == {scheduled[1].id, scheduled[2].id}


class TestTheSweep:
    def test_it_applies_every_schedule_without_a_run(self, manager: BackupManager) -> None:
        from noust.managers.backup_scheduler import sweep_application_retention

        scheduled = [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]) for day in (3, 4, 5)]
        manual = _write_backup(manager, 1, [])
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain=DOMAIN,
                schedule="daily",
                include_databases=True,
                retention_count=1,
                retention_days=None,
                destinations=[],
            )
        )

        deleted = sweep_application_retention(manager)

        assert deleted == {DOMAIN: 2}
        assert _remaining(manager) == {scheduled[2].id, manual.id}
        assert sweep_application_retention(manager) == {}

    def test_a_schedule_with_no_limits_deletes_nothing(self, manager: BackupManager) -> None:
        from noust.managers.backup_scheduler import sweep_application_retention

        _write_backup(manager, 3, [SCHEDULED_TAG, "auto"])
        get_store().save_backup_schedule(
            BackupScheduleRecord(
                app_domain=DOMAIN,
                schedule="daily",
                include_databases=True,
                retention_count=None,
                retention_days=None,
                destinations=[],
            )
        )

        assert sweep_application_retention(manager) == {}
