# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A backup of any kind that stopped and a destination that went away are announced (checklist H-09).

One "backup missing" serves every kind of backup (rule 3): the application schedules
are the monitor's ``BackupWatch`` (K-17, ``test_monitor_watches.py``); here are the
database, data and state policies it also judges from the time of their last good
run, and the watch of the destinations those policies send to.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import BackupError
from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification, State
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.managers.backup_watch import destinations_in_use, policy_backups
from noust.managers.database.backup_records import BackupPolicy, BackupRecords
from noust.managers.self_backup_records import SelfBackupRecords, SelfBackupSchedule
from noust.managers.volume_backup_records import VolumePolicy, VolumeRecords
from noust.monitor.watch_backups import BackupWatch
from noust.monitor.watch_destinations import DestinationWatch
from noust.monitor.watches import WatchServices

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
DAY = 86_400.0


def ago(**delta: float) -> str:
    """An ISO moment this long before NOW."""
    return (NOW - timedelta(**delta)).isoformat(timespec="seconds")


class Settings:
    """A configuration answering what a test set and the default otherwise."""

    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self.values.get(key, default)


class Rig:
    """The services a watch is lent, the clock it reads and what it published."""

    def __init__(self, store: NoustStore) -> None:
        self.now = NOW.timestamp()
        self.sent: list[Notification] = []
        self.settings = Settings()
        self.services = WatchServices(
            publish=lambda build: self.sent.append(build(NotificationContext(server="web-1"))),
            state=NotificationState(store, clock=lambda: self.now),
            runner=FakeRunner(),
            config=self.settings,  # type: ignore[arg-type]
            clock=lambda: self.now,
        )

    @property
    def codes(self) -> list[str]:
        return [note.code for note in self.sent]

    def later(self, **delta: float) -> None:
        self.now += timedelta(**delta).total_seconds()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    """A store of this test's own, the process-wide one for the length of it."""
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    NoustStore._instance = instance
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def rig(store: NoustStore) -> Rig:
    return Rig(store)


def database_policy(
    store: NoustStore,
    name: str = "shop",
    *,
    schedule: str = "daily",
    enabled: bool = True,
    destinations: list[dict[str, object]] | None = None,
    last_success_at: str | None = None,
    created_at: str | None = None,
) -> None:
    """Store a database policy, then set what its last run recorded."""
    policy = BackupPolicy(engine="postgresql", db_name=name, schedule=schedule, enabled=enabled)
    if destinations is not None:
        policy.destinations = destinations
    BackupRecords(store).save_policy(policy)
    with store._transaction() as cursor:
        cursor.execute(
            "UPDATE database_backup_policies SET last_success_at = ?, created_at = ? "
            "WHERE engine = 'postgresql' AND db_name = ?",
            (last_success_at, created_at or ago(days=30), name),
        )


def watch(**kwargs: Any) -> BackupWatch:
    """The backup watch with no application backups to list."""
    return BackupWatch(lambda: [], **kwargs)


class TestThePoliciesTheWatchReads:
    def test_each_kind_is_listed_with_the_command_that_takes_a_backup_now(
        self, store: NoustStore
    ) -> None:
        database_policy(store, last_success_at=ago(hours=3))
        VolumeRecords(store).save_policy(
            VolumePolicy(app_domain="shop.example.com", schedule="daily")
        )
        SelfBackupRecords(store).save(SelfBackupSchedule(schedule="daily"))

        found = {item.key: item for item in policy_backups(store)}

        assert set(found) == {
            "database:postgresql/shop",
            "data:shop.example.com",
            "noust:state",
        }
        assert found["database:postgresql/shop"].command == "noust db backup-run shop -e postgresql"
        assert found["data:shop.example.com"].command == "noust backup volumes run shop.example.com"
        assert found["noust:state"].command == "noust self-backup run"
        assert found["database:postgresql/shop"].last_success is not None
        assert found["data:shop.example.com"].application is True
        assert found["database:postgresql/shop"].application is False

    def test_a_disabled_policy_has_no_timer_and_is_not_listed(self, store: NoustStore) -> None:
        database_policy(store, enabled=False)

        assert policy_backups(store) == []

    def test_the_destinations_in_use_are_those_some_policy_sends_to(
        self, store: NoustStore
    ) -> None:
        database_policy(store, destinations=[{"name": "nas"}])
        SelfBackupRecords(store).save(
            SelfBackupSchedule(schedule="daily", destinations=[{"name": "s3"}])
        )

        assert destinations_in_use(store) == {"nas", "s3"}

    def test_a_server_with_no_policy_has_none(self, store: NoustStore) -> None:
        assert policy_backups(store) == [] and destinations_in_use(store) == set()


class TestWhatIsMissing:
    def test_a_policy_that_ran_recently_is_nothing_to_say(
        self, store: NoustStore, rig: Rig
    ) -> None:
        database_policy(store, last_success_at=ago(hours=20))

        watch().check(rig.services)

        assert rig.sent == []

    def test_no_good_run_for_days_is_announced_with_the_last_one_and_the_command(
        self, store: NoustStore, rig: Rig
    ) -> None:
        database_policy(store, last_success_at=ago(days=5))

        watch().check(rig.services)

        (note,) = rig.sent
        assert (note.kind, note.code, note.state) == (
            "backup_missing",
            "backup.missing",
            State.WARNING,
        )
        assert note.subject == "postgresql/shop" and note.domain is None
        assert "last 5 days" in note.summary
        assert note.command is not None
        assert note.command.value == "noust db backup-run shop -e postgresql"
        assert {fact.key: fact.value for fact in note.facts}["schedule"] == "daily"

    def test_a_policy_that_never_ran_is_announced_once_it_is_old_enough(
        self, store: NoustStore, rig: Rig
    ) -> None:
        database_policy(store, "old", created_at=ago(days=5))
        database_policy(store, "new", created_at=ago(hours=5))

        watch().check(rig.services)

        (note,) = rig.sent
        assert (note.code, note.subject) == ("backup.never", "postgresql/old")

    def test_data_policies_name_their_application_and_noust_its_own_state(
        self, store: NoustStore, rig: Rig
    ) -> None:
        VolumeRecords(store).save_policy(
            VolumePolicy(app_domain="shop.example.com", schedule="daily")
        )
        SelfBackupRecords(store).save(SelfBackupSchedule(schedule="daily"))
        with store._transaction() as cursor:
            cursor.execute("UPDATE volume_backup_policies SET created_at = ?", (ago(days=9),))
            cursor.execute("UPDATE self_backup_schedule SET created_at = ?", (ago(days=9),))

        watch().check(rig.services)

        by_subject = {note.subject: note for note in rig.sent}
        assert set(by_subject) == {"shop.example.com", "noust"}
        assert by_subject["shop.example.com"].domain == "shop.example.com"
        assert by_subject["noust"].domain is None

    def test_the_configured_days_decide_for_every_kind(self, store: NoustStore, rig: Rig) -> None:
        database_policy(store, last_success_at=ago(days=3))
        rig.settings.values["monitor.backup_missing_days"] = 7

        watch().check(rig.services)

        assert rig.sent == []

    def test_it_is_said_again_only_after_the_renotify_hours(
        self, store: NoustStore, rig: Rig
    ) -> None:
        database_policy(store, last_success_at=ago(days=5))
        backups = watch()
        backups.check(rig.services)

        rig.later(hours=23)
        backups.check(rig.services)
        assert len(rig.sent) == 1

        rig.later(hours=2)
        backups.check(rig.services)
        assert len(rig.sent) == 2

    def test_a_restart_does_not_say_it_twice(self, store: NoustStore, rig: Rig) -> None:
        database_policy(store, last_success_at=ago(days=5))
        watch().check(rig.services)

        rig.later(hours=2)
        watch().check(rig.services)

        assert len(rig.sent) == 1

    def test_a_backup_that_comes_back_is_forgotten(self, store: NoustStore, rig: Rig) -> None:
        database_policy(store, last_success_at=ago(days=5))
        watch().check(rig.services)
        assert rig.services.state.get("backups", "database:postgresql/shop") is not None
        with store._transaction() as cursor:
            cursor.execute(
                "UPDATE database_backup_policies SET last_success_at = ?", (ago(hours=1),)
            )

        watch().check(rig.services)

        assert rig.services.state.get("backups", "database:postgresql/shop") is None

    def test_a_policy_that_is_gone_is_no_longer_remembered(
        self, store: NoustStore, rig: Rig
    ) -> None:
        database_policy(store, last_success_at=ago(days=5))
        watch().check(rig.services)
        with store._transaction() as cursor:
            cursor.execute("DELETE FROM database_backup_policies")

        watch().check(rig.services)

        assert rig.services.state.items("backups") == {}

    def test_a_policy_list_can_be_given(self, rig: Rig) -> None:
        from noust.managers.backup_watch import PolicyBackup

        stale = PolicyBackup(
            key="database:mysql/blog",
            kind="database",
            subject="mysql/blog",
            schedule="daily",
            last_success=NOW - timedelta(days=4),
            created=None,
            command="noust db backup-run blog -e mysql",
            application=False,
        )

        BackupWatch(lambda: [], lambda: [stale]).check(rig.services)

        assert [note.subject for note in rig.sent] == ["mysql/blog"]


class TestDestinations:
    def policy_sending_to(self, store: NoustStore, *names: str) -> None:
        database_policy(
            store,
            last_success_at=ago(hours=1),
            destinations=[{"name": name} for name in names],
        )

    def test_one_that_does_not_answer_is_said_once_with_what_rclone_said(
        self, store: NoustStore, rig: Rig
    ) -> None:
        self.policy_sending_to(store, "nas")

        def broken(name: str) -> object:
            raise BackupError(f"Could not reach backup destination {name!r}", details="timeout")

        destinations = DestinationWatch(broken)
        destinations.check(rig.services)
        rig.later(hours=6)
        destinations.check(rig.services)

        assert rig.codes == ["destination.unreachable"]
        excerpt = rig.sent[0].excerpt
        assert excerpt is not None and "timeout" in " ".join(excerpt.lines)
        assert rig.sent[0].kind == "destination_unreachable"

    def test_it_is_said_again_after_a_day_and_closed_when_it_answers(
        self, store: NoustStore, rig: Rig
    ) -> None:
        self.policy_sending_to(store, "nas")
        answering = {"up": False}

        def ask(name: str) -> object:
            if not answering["up"]:
                raise BackupError("down")
            return {"ok": True}

        destinations = DestinationWatch(ask)
        destinations.check(rig.services)
        rig.later(hours=25)
        destinations.check(rig.services)
        answering["up"] = True
        rig.later(hours=6)
        destinations.check(rig.services)
        rig.later(hours=6)
        destinations.check(rig.services)

        assert rig.codes == [
            "destination.unreachable",
            "destination.unreachable",
            "destination.recovered",
        ]
        assert rig.sent[-1].state == State.OK

    def test_a_restart_does_not_say_it_twice(self, store: NoustStore, rig: Rig) -> None:
        self.policy_sending_to(store, "nas")

        def broken(name: str) -> object:
            raise BackupError("down")

        DestinationWatch(broken).check(rig.services)
        rig.later(hours=6)
        DestinationWatch(broken).check(rig.services)

        assert rig.codes == ["destination.unreachable"]

    def test_a_destination_nothing_sends_to_is_not_asked(self, rig: Rig) -> None:
        asked: list[str] = []

        DestinationWatch(lambda name: asked.append(name)).check(rig.services)

        assert asked == []

    def test_every_destination_in_use_is_asked_once(self, store: NoustStore, rig: Rig) -> None:
        self.policy_sending_to(store, "nas", "offsite")
        asked: list[str] = []

        DestinationWatch(lambda name: asked.append(name)).check(rig.services)

        assert asked == ["nas", "offsite"]

    def test_it_runs_as_often_as_the_monitor_looks_for_backups(self, rig: Rig) -> None:
        assert DestinationWatch().interval_s(rig.services) == 6 * 3600

        rig.settings.values["monitor.backup_check_hours"] = 2

        assert DestinationWatch().interval_s(rig.services) == 2 * 3600
