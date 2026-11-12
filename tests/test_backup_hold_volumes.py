# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A volume backup a central has not taken is not rotated away (item 195)."""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from noust.managers.volume_backups import VolumeBackupManager
from tests.support.backup_hold_support import Hold, hold  # noqa: F401
from tests.support.volume_backups import (  # noqa: F401
    DOMAIN,
    RetentionTests,
    _reset_store,
    app,
    app_dir,
    audited,
    manager,
    runner,
    services,
)


class TestTheRetentionOfAnApplicationsData:
    make = RetentionTests.make

    def ids(self, manager: VolumeBackupManager) -> list[str]:
        return [self.make(manager, "scheduled", days) for days in (5, 4, 3, 2, 1)]

    def test_the_retention_asks_about_the_scheduled_backups_as_volumes(
        self, manager: VolumeBackupManager, hold: Hold
    ) -> None:
        ids = self.ids(manager)

        manager.apply_retention(DOMAIN, retention_count=2, retention_days=None)

        assert [kind for kind, _ in hold.asked] == ["volume"]
        assert set(hold.asked[0][1]) == set(ids)

    def test_a_backup_the_central_lacks_stays_and_does_not_count_toward_the_limit(
        self, manager: VolumeBackupManager, hold: Hold
    ) -> None:
        ids = self.ids(manager)
        hold.spare = {ids[0], ids[1]}

        deleted = manager.apply_retention(DOMAIN, retention_count=2, retention_days=None)

        assert deleted == [ids[2]]
        assert {e.id for e in manager.list_backups(DOMAIN)} == {ids[0], ids[1], ids[3], ids[4]}

    def test_what_is_rotated_away_is_said(self, manager: VolumeBackupManager, hold: Hold) -> None:
        ids = self.ids(manager)

        manager.apply_retention(DOMAIN, retention_count=2, retention_days=None)

        assert {
            item for kind, item, owner in hold.noted if kind == "volume" and owner == DOMAIN
        } == set(ids[:3])

    def test_deleting_one_on_purpose_says_nothing(
        self, manager: VolumeBackupManager, hold: Hold
    ) -> None:
        ids = self.ids(manager)

        manager.delete(ids[0])

        assert hold.noted == []
