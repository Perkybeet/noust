# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Retention does not wait for a run that went well (checklist H-04).

A policy pruned its dumps after a successful run and only then: a dump that failed pruned nothing,
and a timer that stopped pruned nothing either, which is how a disk fills while the backups fail.
What is pinned here: a failed run prunes by the policy as a good one does; a separate sweep applies
every policy's retention without a run; both keep the newest copy that is not known to be bad,
never delete a manual dump or a safety copy, and leave alone a dump that failed its check so that
its evidence is kept and no good copy is pushed out to make room for it.
"""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseBackupError
from noust.managers.database.backups import DatabaseBackups
from noust.managers.retention import without_newest_good
from tests.test_database_backups import (  # noqa: F401
    RcloneRunner,
    _reset_store,
    audited,
    backups,
    dumps_dir,
    engine,
    runner,
    service,
    set_up_history,
)

NOW = datetime(2026, 10, 17, tzinfo=timezone.utc)


class TestTheRuleByItself:
    def test_the_newest_good_one_is_spared_even_when_it_is_beyond_the_count(self) -> None:
        entries = [
            ("bad-new", NOW, False),
            ("bad-newer", NOW.replace(day=18), False),
            ("good", NOW.replace(day=10), True),
            ("old", NOW.replace(day=1), True),
        ]
        expired = ["bad-new", "good", "old"]

        assert without_newest_good(expired, entries) == ["bad-new", "old"]

    def test_nothing_changes_when_every_copy_is_good_or_none_is(self) -> None:
        entries = [("a", NOW, True), ("b", NOW.replace(day=1), True)]
        assert without_newest_good(["b"], entries) == ["b"]
        assert without_newest_good(["b"], [("a", NOW, False), ("b", NOW, False)]) == ["b"]


class TestAFailedRunStillPrunes:
    def test_a_dump_that_cannot_be_taken_prunes_by_the_policy_all_the_same(
        self, backups: DatabaseBackups, dumps_dir: Path, engine: Any
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)
        original = engine.backup

        def failing(self: Any, *args: Any, **kwargs: Any) -> Any:
            raise DatabaseBackupError("pg_dump: no space left on device")

        engine.backup = failing

        with pytest.raises(DatabaseBackupError) as failure:
            backups.run_policy("postgresql", "shop")

        engine.backup = original
        assert "no space left on device" in str(failure.value)
        survivors = {view.name for view in backups.list_dumps("postgresql")}
        # The two newest scheduled dumps stay; the manual dump and the safety copy always do.
        assert files["scheduled-4"].name in survivors and files["scheduled-3"].name in survivors
        for role in ("scheduled-0", "scheduled-1", "scheduled-2"):
            assert not files[role].exists(), role
        assert files["manual"].exists() and files["safety"].exists()
        assert backups.get_policy("postgresql", "shop").to_dict()["last_status"] == "failed"

    def test_a_dump_that_was_taken_but_failed_its_check_prunes_nothing(
        self,
        backups: DatabaseBackups,
        dumps_dir: Path,
        runner: RcloneRunner,
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=1, retention_days=None)
        runner.script(
            ("pg_restore", "--list"), stderr="pg_restore: error: end of file", exit_code=1
        )

        with pytest.raises(DatabaseBackupError):
            backups.run_policy("postgresql", "shop")

        assert all(path.exists() for path in files.values())


class TestTheSweep:
    def test_it_applies_every_policy_without_a_run(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)

        deleted = backups.sweep_retention()

        assert sorted(deleted["postgresql/shop"]) == sorted(
            files[role].name for role in ("scheduled-0", "scheduled-1", "scheduled-2")
        )
        assert files["scheduled-4"].exists() and files["scheduled-3"].exists()
        assert files["manual"].exists() and files["safety"].exists()

    def test_a_second_sweep_has_nothing_left_to_do(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)
        backups.sweep_retention()

        assert backups.sweep_retention() == {}

    def test_the_newest_copy_that_is_not_bad_survives_an_age_limit_nobody_met(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        # Everything is older than the limit: the timer has been dead for weeks.
        backups.set_policy("postgresql", "shop", retention_count=None, retention_days=2)

        backups.sweep_retention()

        assert files["scheduled-4"].exists()
        for role in ("scheduled-0", "scheduled-1", "scheduled-2", "scheduled-3"):
            assert not files[role].exists(), role

    def test_a_dump_that_failed_its_check_does_not_count_as_the_newest_good_one(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        bad = backups.dump("postgresql", "shop", origin="scheduled", verify=False)
        record = bad.record
        assert record is not None
        record.verify_status = "failed"
        backups.records.save_dump(record)
        backups.set_policy("postgresql", "shop", retention_count=1, retention_days=None)

        backups.sweep_retention()

        # The bad one is the newest and is kept as evidence; the newest good one is spared too.
        assert bad.info.path.exists() and files["scheduled-4"].exists()
        assert not files["scheduled-3"].exists()

    def test_a_policy_for_all_databases_prunes_each_one_it_has_dumps_of(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "*", retention_count=2, retention_days=None)

        deleted = backups.sweep_retention()

        assert "postgresql/shop" in deleted
        assert files["scheduled-0"].exists() is False

    def test_a_policy_that_is_off_is_still_pruned_because_its_dumps_are_still_there(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy(
            "postgresql", "shop", retention_count=2, retention_days=None, enabled=False
        )

        backups.sweep_retention()

        assert not files["scheduled-0"].exists()

    def test_what_it_deletes_is_audited_as_retention(
        self,
        backups: DatabaseBackups,
        dumps_dir: Path,
        audited: list[dict[str, Any]],
    ) -> None:
        set_up_history(backups, dumps_dir)
        backups.set_policy("postgresql", "shop", retention_count=2, retention_days=None)

        backups.sweep_retention()

        deleted = [e for e in audited if e["event"] == "db.backup.delete"]
        assert deleted and {e["details"]["reason"] for e in deleted} == {"retention"}
