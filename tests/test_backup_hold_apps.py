# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""An application's backup a central has not taken is not rotated away (item 195)."""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from noust.managers.backup_manager import SCHEDULED_TAG, BackupManager
from tests.support.backup_hold_support import Hold, hold  # noqa: F401
from tests.support.backup_safety_22 import (  # noqa: F401
    APP,
    DOMAIN,
    _remaining,
    _reset_store,
    _write_backup,
    manager,
)


def scheduled(manager: BackupManager) -> list[str]:
    return [_write_backup(manager, day, [SCHEDULED_TAG, "auto"]).id for day in (1, 2, 3, 4, 5)]


def test_the_rotation_asks_about_every_backup_it_is_judging(
    manager: BackupManager, hold: Hold
) -> None:
    ids = scheduled(manager)

    manager.rotate_by_policy(APP, max_count=2, tag=SCHEDULED_TAG)

    kinds = {kind for kind, _ in hold.asked}
    assert kinds == {"app"}
    assert set(ids) <= {item for _, asked in hold.asked for item in asked}


def test_a_backup_the_central_lacks_stays_and_does_not_count_toward_the_limit(
    manager: BackupManager, hold: Hold
) -> None:
    ids = scheduled(manager)
    hold.spare = {ids[0], ids[1]}

    deleted = manager.rotate_by_policy(APP, max_count=2, tag=SCHEDULED_TAG)

    # The two oldest wait; of the three the rule judges, the newest two stay by the count.
    assert deleted == 1
    assert _remaining(manager) == {ids[0], ids[1], ids[3], ids[4]}


def test_by_age_too(manager: BackupManager, hold: Hold) -> None:
    ids = scheduled(manager)
    hold.spare = {ids[0]}

    manager.rotate_by_policy(APP, max_age_days=1, tag=SCHEDULED_TAG)

    assert ids[0] in _remaining(manager)
    assert ids[1] not in _remaining(manager)


def test_a_plain_rotation_leaves_it_too(manager: BackupManager, hold: Hold) -> None:
    ids = scheduled(manager)
    hold.spare = set(ids[:3])

    manager._rotate_backups(APP)

    # max_backups is 2: the judged ones are the newest two, and the waiting three stay as well.
    assert set(ids[:3]) <= _remaining(manager)


def test_what_is_rotated_away_is_said_with_what_it_was_a_copy_of(
    manager: BackupManager, hold: Hold
) -> None:
    ids = scheduled(manager)

    manager.rotate_by_policy(APP, max_count=2, tag=SCHEDULED_TAG)

    noted = {item for kind, item, owner in hold.noted if kind == "app" and owner == DOMAIN}
    assert noted == set(ids[:3])


def test_a_delete_by_a_person_says_nothing_of_it(manager: BackupManager, hold: Hold) -> None:
    ids = scheduled(manager)

    manager.delete(ids[0])

    assert hold.noted == []
