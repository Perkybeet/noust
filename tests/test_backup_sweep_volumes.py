# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data of an application is pruned without waiting for a run that went well (checklist H-04).

The same three rules as the dumps (``tests/test_backup_sweep.py``) over the archives of a
target: a run that could not take its backup prunes by the policy as a good one does, a separate
sweep applies every policy, and the newest archive that is not known to be bad is always spared.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

import json

import pytest

from noust.core.exceptions import BackupError
from noust.managers.volume_backups import VolumeBackupManager
from tests.test_volume_backups import (  # noqa: F401
    DOMAIN,
    TestRetention,
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)


def mark_bad(manager: VolumeBackupManager, backup_id: str) -> None:
    """Record that an archive failed its verification."""
    entry = manager.get_backup(backup_id)
    assert entry is not None
    sidecar = manager.archive_path(entry).with_name(f"{backup_id}.json")
    data = json.loads(sidecar.read_text())
    data["verified_ok"] = False
    sidecar.write_text(json.dumps(data))


class TestTheNewestGoodOne:
    make = TestRetention.make

    def test_a_newest_archive_that_failed_its_check_does_not_push_the_good_ones_out(
        self, manager: VolumeBackupManager
    ) -> None:
        ids = [self.make(manager, "scheduled", days) for days in (3, 2, 1)]
        mark_bad(manager, ids[2])

        deleted = manager.apply_retention(DOMAIN, retention_count=1, retention_days=None)

        left = {e.id for e in manager.list_backups(DOMAIN)}
        assert ids[2] in left and ids[1] in left
        assert deleted == [ids[0]]


class TestTheSweep:
    make = TestRetention.make

    def test_it_applies_every_policy_without_a_run(self, manager: VolumeBackupManager) -> None:
        ids = [self.make(manager, "scheduled", days) for days in (5, 4, 3, 2, 1)]
        manager.set_policy(DOMAIN, targets=["path:uploads"], retention_count=2, retention_days=None)

        deleted = manager.sweep_retention()

        assert sorted(deleted[DOMAIN]) == sorted(ids[:3])
        assert {e.id for e in manager.list_backups(DOMAIN)} == set(ids[3:])
        assert manager.sweep_retention() == {}

    def test_a_manual_backup_is_never_the_sweeps(self, manager: VolumeBackupManager) -> None:
        manual = self.make(manager, None, 900)
        manager.set_policy(DOMAIN, targets=["path:uploads"], retention_count=1, retention_days=1)

        manager.sweep_retention()

        assert manager.get_backup(manual) is not None


class TestAFailedRun:
    make = TestRetention.make

    def test_a_backup_that_cannot_be_taken_prunes_by_the_policy_all_the_same(
        self, manager: VolumeBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ids = [self.make(manager, "scheduled", days) for days in (5, 4, 3, 2, 1)]
        manager.set_policy(DOMAIN, targets=["path:uploads"], retention_count=2, retention_days=None)

        def refuse(*args: object, **kwargs: object) -> None:
            raise BackupError("tar: no space left on device")

        monkeypatch.setattr(manager, "_create_when_free", refuse)

        with pytest.raises(BackupError, match="no space left"):
            manager.run_policy(DOMAIN)

        assert {e.id for e in manager.list_backups(DOMAIN)} == set(ids[3:])
        policy = manager.get_policy(DOMAIN).to_dict()
        assert policy["last_status"] == "failed"

    def test_an_archive_that_was_taken_but_does_not_verify_prunes_nothing(
        self, manager: VolumeBackupManager, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ids = [self.make(manager, "scheduled", days) for days in (5, 4, 3)]
        manager.set_policy(DOMAIN, targets=["path:uploads"], retention_count=1, retention_days=None)
        monkeypatch.setattr(
            manager,
            "verify",
            lambda backup_id, deep=False: {"valid": False, "errors": ["checksum differs"]},
        )

        with pytest.raises(BackupError, match="failed its verification"):
            manager.run_policy(DOMAIN)

        assert set(ids) <= {e.id for e in manager.list_backups(DOMAIN)}
