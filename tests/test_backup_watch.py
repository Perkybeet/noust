# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A backup that stopped and a destination that went away are announced (checklist H-09)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from noust.core.exceptions import BackupError
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.store import BackupScheduleRecord, NoustStore
from noust.managers.backup_watch import (
    BACKUPS_EVERY,
    DESTINATIONS_EVERY,
    REMIND_AFTER,
    BackupWatch,
    find_missing,
    interval_hours,
    threshold,
)
from noust.managers.database.backup_records import BackupPolicy, BackupRecords
from noust.managers.self_backup_records import SelfBackupRecords, SelfBackupSchedule
from noust.managers.volume_backup_records import VolumePolicy, VolumeRecords

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)


def ago(**delta: float) -> str:
    """An ISO moment this long before NOW."""
    return (NOW - timedelta(**delta)).isoformat(timespec="seconds")


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


def database_policy(
    store: NoustStore, name: str = "shop", *, schedule: str = "daily", **fields: object
) -> None:
    """Store a database policy, then set what its last run recorded."""
    records = BackupRecords(store)
    policy = BackupPolicy(engine="postgresql", db_name=name, schedule=schedule)
    for key, value in fields.items():
        if key == "destinations":
            policy.destinations = value  # type: ignore[assignment]
    records.save_policy(policy)
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE database_backup_policies SET last_success_at = ?, created_at = ? "
            "WHERE engine = 'postgresql' AND db_name = ?",
            (fields.get("last_success_at"), fields.get("created_at", ago(days=30)), name),
        )


class TestTheSchedule:
    @pytest.mark.parametrize(
        ("schedule", "hours"),
        [
            ("hourly", 1),
            ("daily", 24),
            ("weekly", 168),
            ("monthly", 744),
            ("*-*-* 03:00:00", 24),
            ("*-*-* 03:30", 24),
            ("*-*-* *:00:00", 1),
            ("*-*-* */6:00:00", 6),
            ("Mon *-*-* 02:00:00", 168),
            ("*-*-01 04:00:00", 744),
        ],
    )
    def test_how_often_a_calendar_fires(self, schedule: str, hours: float) -> None:
        assert interval_hours(schedule) == hours

    def test_a_calendar_it_cannot_read_has_no_interval(self) -> None:
        assert interval_hours("Mon..Fri *-*-* 03,15:00:00") is None

    def test_two_periods_are_allowed_and_never_less_than_three_hours(self) -> None:
        assert threshold("daily") == timedelta(hours=48)
        assert threshold("weekly") == timedelta(hours=336)
        assert threshold("hourly") == timedelta(hours=3)

    def test_a_calendar_it_cannot_read_is_given_two_days(self) -> None:
        assert threshold("Mon..Fri *-*-* 03,15:00:00") == timedelta(days=2)

    def test_the_operator_can_set_one_number_of_days_or_turn_it_off(self) -> None:
        assert threshold("hourly", missing_days=5) == timedelta(days=5)
        assert threshold("daily", missing_days=0) is None


class TestWhatIsMissing:
    def test_a_policy_that_ran_recently_is_fine(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(hours=20))

        assert find_missing(store=store, now=NOW) == []

    def test_a_policy_with_no_good_run_in_two_periods_is_missing(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(days=3))

        [item] = find_missing(store=store, now=NOW)

        assert (item.kind, item.subject) == ("database", "postgresql/shop")
        assert item.overdue == timedelta(hours=72 - 48)
        assert item.command == "noust db backup-run shop -e postgresql"

    def test_a_policy_that_never_ran_is_missing_once_it_is_older_than_the_allowance(
        self, store: NoustStore
    ) -> None:
        database_policy(store, "old", created_at=ago(days=5))
        database_policy(store, "new", created_at=ago(hours=5))

        names = {item.subject for item in find_missing(store=store, now=NOW)}

        assert names == {"postgresql/old"}

    def test_a_disabled_policy_has_no_timer_and_is_not_missing(self, store: NoustStore) -> None:
        records = BackupRecords(store)
        records.save_policy(
            BackupPolicy(engine="postgresql", db_name="shop", schedule="daily", enabled=False)
        )
        with store._transaction() as cursor:
            cursor.execute("UPDATE database_backup_policies SET created_at = ?", (ago(days=30),))

        assert find_missing(store=store, now=NOW) == []

    def test_data_and_noust_own_state_are_watched_too(self, store: NoustStore) -> None:
        VolumeRecords(store).save_policy(
            VolumePolicy(app_domain="shop.example.com", schedule="daily")
        )
        SelfBackupRecords(store).save(SelfBackupSchedule(schedule="daily"))
        with store._transaction() as cursor:
            cursor.execute("UPDATE volume_backup_policies SET created_at = ?", (ago(days=9),))
            cursor.execute("UPDATE self_backup_schedule SET created_at = ?", (ago(days=9),))

        found = {(item.kind, item.subject) for item in find_missing(store=store, now=NOW)}

        assert found == {("data", "shop.example.com"), ("noust", "noust")}

    def test_an_application_schedule_is_judged_by_its_newest_scheduled_backup(
        self, store: NoustStore
    ) -> None:
        store.save_backup_schedule(
            BackupScheduleRecord(app_domain="shop.example.com", schedule="daily")
        )
        with store._transaction() as cursor:
            cursor.execute("UPDATE backup_schedules SET created_at = ?", (ago(days=30),))

        stale = find_missing(
            store=store, now=NOW, last_application_backup=lambda domain: NOW - timedelta(days=4)
        )
        fresh = find_missing(
            store=store, now=NOW, last_application_backup=lambda domain: NOW - timedelta(hours=5)
        )

        assert [item.subject for item in stale] == ["shop.example.com"] and fresh == []

    def test_the_config_can_replace_the_rule_for_every_policy(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(days=3))

        class Settings:
            def get(self, key: str, default: object = None) -> object:
                return {"backup.missing_days": 7}.get(key, default)

        assert find_missing(store=store, now=NOW, config=Settings()) == []  # type: ignore[arg-type]


class Said:
    """What the monitor was asked to send."""

    def __init__(self) -> None:
        self.notifications: list[Notification] = []

    def __call__(self, build: Callable[[NotificationContext], Notification]) -> None:
        self.notifications.append(build(NotificationContext(server="web-1")))

    @property
    def codes(self) -> list[str]:
        return [notification.code for notification in self.notifications]


class TestAnnouncing:
    def test_a_missing_backup_is_said_once_and_again_after_a_day(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(days=3))
        said = Said()
        watch = BackupWatch(said, store=store)

        watch.check_backups(now=NOW)
        watch.check_backups(now=NOW + BACKUPS_EVERY)
        later = NOW + REMIND_AFTER + timedelta(minutes=1)
        watch.check_backups(now=later)

        assert said.codes == ["backup.missing", "backup.missing"]
        first = said.notifications[0]
        assert first.kind == "backup_missing" and first.subject == "postgresql/shop"
        assert first.command is not None and "backup-run" in first.command.value

    def test_a_restart_does_not_say_it_twice(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(days=3))
        BackupWatch(Said(), store=store).check_backups(now=NOW)
        second = Said()

        BackupWatch(second, store=store).check_backups(now=NOW + timedelta(hours=2))

        assert second.codes == []

    def test_it_asks_at_most_once_an_hour(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(days=3))
        said = Said()
        watch = BackupWatch(said, store=store)

        first = watch.check_backups(now=NOW)
        again = watch.check_backups(now=NOW + timedelta(minutes=5))

        assert len(first) == 1 and again == []

    def test_a_backup_that_comes_back_closes_the_alert(self, store: NoustStore) -> None:
        database_policy(store, last_success_at=ago(days=3))
        said = Said()
        watch = BackupWatch(said, store=store)
        watch.check_backups(now=NOW)
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE database_backup_policies SET last_success_at = ?",
                ((NOW + timedelta(hours=1)).isoformat(timespec="seconds"),),
            )

        watch.check_backups(now=NOW + timedelta(hours=2, minutes=1))

        assert said.codes == ["backup.missing", "backup.recovered"]
        assert said.notifications[1].state.value == "ok"
        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM backup_alerts")
            assert cursor.fetchone()[0] == 0


class TestDestinations:
    def policy_sending_to(self, store: NoustStore, *names: str) -> None:
        database_policy(
            store,
            last_success_at=ago(hours=1),
            destinations=[{"name": name} for name in names],
        )

    def test_a_destination_that_does_not_answer_is_said_once_with_what_rclone_said(
        self, store: NoustStore
    ) -> None:
        self.policy_sending_to(store, "nas")
        said = Said()

        def broken(name: str) -> object:
            raise BackupError(f"Could not reach backup destination {name!r}", details="timeout")

        watch = BackupWatch(said, store=store, destination_test=broken)

        down = watch.check_destinations(now=NOW)
        watch.check_destinations(now=NOW + DESTINATIONS_EVERY)

        assert down == ["nas"]
        assert said.codes == ["destination.unreachable"]
        excerpt = said.notifications[0].excerpt
        assert excerpt is not None and "timeout" in " ".join(excerpt.lines)

    def test_it_is_said_again_after_a_day_and_closed_when_it_answers(
        self, store: NoustStore
    ) -> None:
        self.policy_sending_to(store, "nas")
        said = Said()
        answering = {"up": False}

        def test(name: str) -> object:
            if not answering["up"]:
                raise BackupError("down")
            return {"ok": True}

        watch = BackupWatch(said, store=store, destination_test=test)
        watch.check_destinations(now=NOW)
        watch.check_destinations(now=NOW + REMIND_AFTER + timedelta(minutes=1))
        answering["up"] = True
        watch.check_destinations(now=NOW + 2 * REMIND_AFTER)

        assert said.codes == [
            "destination.unreachable",
            "destination.unreachable",
            "destination.recovered",
        ]

    def test_a_destination_nothing_sends_to_is_not_asked(self, store: NoustStore) -> None:
        asked: list[str] = []
        watch = BackupWatch(Said(), store=store, destination_test=lambda name: asked.append(name))

        watch.check_destinations(now=NOW)

        assert asked == []

    def test_every_destination_in_use_is_asked_once(self, store: NoustStore) -> None:
        self.policy_sending_to(store, "nas", "offsite")
        asked: list[str] = []
        watch = BackupWatch(Said(), store=store, destination_test=lambda name: asked.append(name))

        watch.check_destinations(now=NOW)

        assert asked == ["nas", "offsite"]
