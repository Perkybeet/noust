# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Retention by size, beside by count and by age (checklist H-04)."""

# The fixtures are imported from the tests of the thing they set up.
# ruff: noqa: F811

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.managers.database.backups import DatabaseBackups
from noust.managers.retention import select_expired
from tests.test_database_backups import (  # noqa: F401
    FakeScheduler,
    RcloneRunner,
    _reset_store,
    add_destination,
    backups,
    dumps_dir,
    engine,
    runner,
    service,
    set_up_history,
)

NOW = datetime(2026, 9, 29, 12, 0, tzinfo=timezone.utc)
GB = 1024**3


def days_ago(days: int) -> datetime:
    return NOW - timedelta(days=days)


class TestTheRule:
    def sized(self) -> tuple[list[tuple[str, datetime]], dict[str, int]]:
        entries = [(f"d{i}", days_ago(i)) for i in range(5)]
        return entries, {f"d{i}": 10 for i in range(5)}

    def test_the_newest_that_fit_are_kept_and_the_rest_go(self) -> None:
        entries, sizes = self.sized()

        assert select_expired(
            entries, count=None, days=None, now=NOW, max_bytes=25, sizes=sizes
        ) == [
            "d2",
            "d3",
            "d4",
        ]

    def test_the_newest_stays_even_when_it_alone_is_over_the_budget(self) -> None:
        entries = [("big", days_ago(0)), ("old", days_ago(1))]

        assert select_expired(
            entries, count=None, days=None, now=NOW, max_bytes=5, sizes={"big": 50, "old": 1}
        ) == ["old"]

    def test_an_older_small_entry_does_not_take_the_place_of_a_newer_big_one(self) -> None:
        # A window of time, not a pile of whatever is small: once one does not fit, the rest go.
        entries = [("a", days_ago(0)), ("b", days_ago(1)), ("c", days_ago(2))]

        assert select_expired(
            entries, count=None, days=None, now=NOW, max_bytes=12, sizes={"a": 10, "b": 8, "c": 1}
        ) == ["b", "c"]

    def test_the_three_limits_are_each_enough_alone(self) -> None:
        entries, sizes = self.sized()

        by_count = select_expired(entries, count=4, days=None, now=NOW, max_bytes=1000, sizes=sizes)
        by_age = select_expired(entries, count=None, days=2, now=NOW, max_bytes=1000, sizes=sizes)
        by_size = select_expired(entries, count=None, days=None, now=NOW, max_bytes=45, sizes=sizes)

        assert by_count == ["d4"] and by_age == ["d3", "d4"] and by_size == ["d4"]

    def test_an_entry_with_no_known_size_weighs_nothing(self) -> None:
        entries = [("a", days_ago(0)), ("b", days_ago(1))]

        assert select_expired(entries, count=None, days=None, now=NOW, max_bytes=5, sizes={}) == []

    def test_no_size_given_changes_nothing_from_before(self) -> None:
        entries, _ = self.sized()

        assert select_expired(entries, count=2, days=None, now=NOW) == ["d2", "d3", "d4"]


class TestAPolicy:
    def test_the_size_is_stored_and_shown_in_gigabytes(self, backups: DatabaseBackups) -> None:
        view = backups.set_policy("postgresql", "shop", retention_gb=2.5)

        data = view.to_dict()

        assert data["retention_gb"] == 2.5
        assert backups.records.policy("postgresql", "shop").retention_bytes == int(2.5 * GB)  # type: ignore[union-attr]

    def test_none_is_no_limit_by_size_and_a_policy_keeps_what_it_was_given(
        self, backups: DatabaseBackups
    ) -> None:
        backups.set_policy("postgresql", "shop", retention_gb=1)
        again = backups.set_policy("postgresql", "shop")

        assert again.to_dict()["retention_gb"] is None

    @pytest.mark.parametrize("bad", [0, -1, 1_000_001, "5", True])
    def test_a_value_that_is_not_a_size_is_refused(
        self, backups: DatabaseBackups, bad: Any
    ) -> None:
        with pytest.raises(ValidationError, match="retention_gb"):
            backups.set_policy("postgresql", "shop", retention_gb=bad)

    def test_a_destination_may_have_a_size_of_its_own(
        self, backups: DatabaseBackups, runner: RcloneRunner
    ) -> None:
        add_destination(runner)

        view = backups.set_policy(
            "postgresql",
            "shop",
            destinations=[{"name": "nas", "retention_count": 4, "retention_gb": 0.5}],
        )

        [entry] = view.to_dict()["destinations"]
        assert entry["retention_gb"] == 0.5 and entry["retention_count"] == 4


class TestRunningIt:
    def test_the_dumps_kept_here_stop_at_the_budget_and_by_hand_ones_are_never_touched(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        one = files["scheduled-4"].stat().st_size
        # Room for the new dump and two of the old ones.
        backups.set_policy(
            "postgresql",
            "shop",
            retention_count=None,
            retention_days=None,
            retention_gb=(one * 3 + one // 2) / GB,
        )

        result = backups.run_policy("postgresql", "shop")

        assert sorted(result["local_deleted"]) == sorted(
            files[role].name for role in ("scheduled-0", "scheduled-1", "scheduled-2")
        )
        assert files["scheduled-4"].exists() and files["scheduled-3"].exists()
        assert files["manual"].exists() and files["safety"].exists()

    def test_a_policy_with_only_a_size_limit_still_prunes(
        self, backups: DatabaseBackups, dumps_dir: Path
    ) -> None:
        files = set_up_history(backups, dumps_dir)
        backups.set_policy(
            "postgresql", "shop", retention_count=None, retention_days=None, retention_gb=1e-9
        )

        backups.run_policy("postgresql", "shop")

        # Everything scheduled but the newest is over a one-byte budget.
        assert not any(files[f"scheduled-{index}"].exists() for index in range(5))


class TestTheCommandLine:
    def test_to_reads_a_fourth_segment_as_gigabytes(self) -> None:
        from noust.cli.commands.db_backups import _destination

        assert _destination("nas:7:30:50") == {
            "name": "nas",
            "retention_count": 7,
            "retention_days": 30,
            "retention_gb": 50.0,
        }
        assert _destination("nas:7")["retention_gb"] is None

    def test_a_size_that_is_not_a_number_is_a_usage_error(self) -> None:
        import click

        from noust.cli.commands.db_backups import _gigabytes

        with pytest.raises(click.BadParameter):
            _gigabytes("lots", "--keep-gb")
        assert _gigabytes("none", "--keep-gb") is None
        assert _gigabytes("2.5", "--keep-gb") == 2.5
