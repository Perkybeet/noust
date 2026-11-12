# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A database dump a central has not taken is not rotated away (item 195)."""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from pathlib import Path

from noust.managers.database.backups import DatabaseBackups
from tests.support.backup_hold_support import Hold, hold  # noqa: F401
from tests.support.database_backups import (  # noqa: F401
    _reset_store,
    audited,
    backups,
    dumps_dir,
    engine,
    runner,
    service,
    set_up_history,
)


def names(backups: DatabaseBackups) -> set[str]:
    return {view.name for view in backups.list_dumps("postgresql")}


def test_the_retention_asks_about_the_scheduled_dumps_by_engine_and_file(
    backups: DatabaseBackups, dumps_dir: Path, hold: Hold
) -> None:
    files = set_up_history(backups, dumps_dir)
    backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)

    backups.sweep_retention()

    (kind, asked) = hold.asked[0]
    assert kind == "database"
    assert f"postgresql:{files['scheduled-0'].name}" in asked
    # A dump by hand and a restore's safety copy are not the schedule's to rotate, so never asked.
    assert f"postgresql:{files['manual'].name}" not in asked
    assert f"postgresql:{files['safety'].name}" not in asked


def test_a_dump_the_central_lacks_stays_and_does_not_count_toward_the_limit(
    backups: DatabaseBackups, dumps_dir: Path, hold: Hold
) -> None:
    files = set_up_history(backups, dumps_dir)
    hold.spare = {f"postgresql:{files[f'scheduled-{i}'].name}" for i in (0, 1)}
    backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)

    backups.sweep_retention()

    survivors = names(backups)
    for index in (0, 1, 3, 4):
        assert files[f"scheduled-{index}"].name in survivors, index
    assert files["scheduled-2"].name not in survivors


def test_what_is_rotated_away_is_said_with_the_engine_and_the_database(
    backups: DatabaseBackups, dumps_dir: Path, hold: Hold
) -> None:
    files = set_up_history(backups, dumps_dir)
    backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)

    backups.sweep_retention()

    said = {(item, owner) for kind, item, owner in hold.noted if kind == "database"}
    assert said == {
        (f"postgresql:{files[f'scheduled-{i}'].name}", "postgresql/shop") for i in (0, 1, 2)
    }


def test_a_dump_deleted_by_a_person_says_nothing(
    backups: DatabaseBackups, dumps_dir: Path, hold: Hold
) -> None:
    files = set_up_history(backups, dumps_dir)

    backups.delete_dump("postgresql", files["scheduled-0"].name)

    assert hold.noted == []
