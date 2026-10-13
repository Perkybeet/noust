# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two things nobody notices going wrong: a backup that stopped, and a destination that went away.

A backup that fails sends ``backup_failed``. A backup that is never taken fails
nothing: the timer was removed, the unit is masked, the server was off at 3 a.m.
for a month. And a destination whose key expired, or whose bucket was deleted,
fails only the next upload, which may be days away. Coolify added
``missing_backup_notification_days`` and an hourly job that re-checks S3 storages
in 4.3.18; this is both, for every kind of backup Noust takes:

- **A missing backup**: an application's schedule, a database's policy, an
  application's data policy and Noust's own state backup each say how often they
  run. When no *successful* run is newer than twice that (at least three hours),
  ``backup_missing`` is sent once, then again every day while it stays true, and
  a ``backup.recovered`` note closes it. ``backup.missing_days`` replaces the rule
  with a number of days for every policy; 0 turns the alert off.
- **A destination that does not answer**: every destination in use is asked
  (``rclone mkdir`` and ``lsf``, as ``noust backup destination test`` does) every
  six hours, and ``destination_unreachable`` is sent once per outage, with what
  rclone said, again daily, and a note when it answers again.

What has already been announced is a row of ``backup_alerts``
(``schema_v14.BACKUPS_SQL``), so a restart of the monitor does not announce twice.
The module decides and returns what to say; the monitor says it
(:mod:`noust.monitor.process_monitor`).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial

from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.notifications.composers import (
    compose_backup_missing,
    compose_backup_recovered,
    compose_destination_recovered,
    compose_destination_unreachable,
)
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.store import NoustStore, get_store

#: What the alerts are kept as.
KIND_APPLICATION = "application"
KIND_DATABASE = "database"
KIND_DATA = "data"
KIND_NOUST = "noust"

#: How often each check is made at most; the monitor calls on every scan.
BACKUPS_EVERY = timedelta(hours=1)
DESTINATIONS_EVERY = timedelta(hours=6)

#: An alert still true is said again after this long.
REMIND_AFTER = timedelta(hours=24)

#: A schedule is given two periods before a backup counts as missing, and never less than this.
GRACE_PERIODS = 2
MINIMUM_THRESHOLD_HOURS = 3.0

_KEYWORDS: dict[str, float] = {
    "minutely": 1 / 60,
    "hourly": 1,
    "daily": 24,
    "weekly": 168,
    "monthly": 744,
    "quarterly": 2232,
    "semiannually": 4464,
    "yearly": 8784,
    "annually": 8784,
}
_EVERY_N_HOURS = re.compile(r"^\*-\*-\*\s+(?:\*|\*/(\d+)|\d{1,2}(?:,\d{1,2})+):")
_WEEKDAY = re.compile(r"^(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*\s+\S", re.IGNORECASE)
_DAY_OF_MONTH = re.compile(r"^\*-\*-(0?[1-9]|[12]\d|3[01])\b")


def interval_hours(schedule: str) -> float | None:
    """
    Say how often a systemd ``OnCalendar`` expression fires, roughly.

    Args:
        schedule: ``daily``, ``*-*-* 03:00:00``, ``Mon *-*-* 02:00``,
            ``*-*-01 04:00``, ``*-*-* */6:00:00``...

    Returns:
        Hours between runs; None for an expression this does not understand
        (the caller then falls back to ``backup.missing_days``).
    """
    text = schedule.strip().lower()
    if text in _KEYWORDS:
        return _KEYWORDS[text]
    if _WEEKDAY.match(text):
        return 168
    if _DAY_OF_MONTH.match(text):
        return 744
    match = _EVERY_N_HOURS.match(text)
    if match:
        if match.group(0).count(",") or "*/" not in match.group(0):
            return 24 if "*:" not in match.group(0) else 1
        return float(match.group(1))
    if re.match(r"^\*-\*-\*\s+\d{1,2}:\d{2}", text):
        return 24
    return None


def threshold(schedule: str, *, missing_days: float | None = None) -> timedelta | None:
    """
    Say how long without a good backup is too long for a schedule.

    Args:
        schedule: The policy's calendar.
        missing_days: ``backup.missing_days`` when the operator set it: one
            number of days for every policy; 0 turns the alert off.

    Returns:
        The longest age of the newest good backup that is still fine, or None
        when the alert is off or the schedule cannot be read and no number of
        days was given.
    """
    if missing_days is not None:
        return timedelta(days=missing_days) if missing_days > 0 else None
    interval = interval_hours(schedule)
    if interval is None:
        return timedelta(days=2)
    return timedelta(hours=max(interval * GRACE_PERIODS, MINIMUM_THRESHOLD_HOURS))


@dataclass(frozen=True)
class MissingBackup:
    """
    A backup that should have been taken and was not.

    Attributes:
        key: What the alert is kept under (``database:postgresql/shop``).
        kind: ``application``, ``database``, ``data`` or ``noust``.
        subject: What it is about, as the operator reads it.
        schedule: The policy's calendar.
        last_success: When a backup last succeeded, or None if none ever did.
        overdue: How long past the allowed age it is.
        command: What to run to take one now.
    """

    key: str
    kind: str
    subject: str
    schedule: str
    last_success: datetime | None
    overdue: timedelta
    command: str


def _moment(text: str | None) -> datetime | None:
    """
    Read a timestamp the store keeps.

    Args:
        text: ISO 8601 text, naive (UTC) or with an offset.

    Returns:
        An aware datetime; None for nothing or something unreadable.
    """
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _policies(
    store: NoustStore,
) -> Iterable[tuple[str, str, str, str, str, str | None, str | None]]:
    """
    List every enabled backup policy of every kind.

    Args:
        store: The store.

    Yields:
        ``(kind, subject, key, schedule, command, last_success, created)``.
    """
    from noust.managers.database.backup_records import BackupRecords
    from noust.managers.self_backup_records import SelfBackupRecords
    from noust.managers.volume_backup_records import VolumeRecords

    for policy in BackupRecords(store).policies():
        if not policy.enabled:
            continue
        subject = f"{policy.engine}/{policy.db_name}"
        yield (
            KIND_DATABASE,
            subject,
            f"{KIND_DATABASE}:{subject}",
            policy.schedule,
            f"noust db backup-run {policy.db_name} -e {policy.engine}",
            policy.last_success_at,
            policy.created_at,
        )
    for volume in VolumeRecords(store).policies():
        if not volume.enabled:
            continue
        yield (
            KIND_DATA,
            volume.app_domain,
            f"{KIND_DATA}:{volume.app_domain}",
            volume.schedule,
            f"noust backup volumes run {volume.app_domain}",
            volume.last_success_at,
            volume.created_at,
        )
    own = SelfBackupRecords(store).get()
    if own is not None and own.enabled:
        yield (
            KIND_NOUST,
            "noust",
            f"{KIND_NOUST}:state",
            own.schedule,
            "noust self-backup run",
            own.last_success_at,
            own.created_at,
        )
    for schedule in store.list_backup_schedules():
        yield (
            KIND_APPLICATION,
            schedule.app_domain,
            f"{KIND_APPLICATION}:{schedule.app_domain}",
            schedule.schedule,
            f"noust backup run-schedule {schedule.app_domain}",
            None,
            schedule.created_at,
        )


def newest_application_backup(domain: str) -> datetime | None:
    """
    Find when an application's schedule last took a backup.

    Args:
        domain: The application.

    Returns:
        The newest scheduled backup's time, or None.
    """
    from noust.managers.backup_manager import BackupManager

    newest: datetime | None = None
    try:
        backups = BackupManager().list_backups(domain)
    except (NoustError, OSError):
        return None
    for backup in backups:
        if "scheduled" not in (backup.tags or []):
            continue
        moment = _moment(backup.created_at)
        if moment is not None and (newest is None or moment > newest):
            newest = moment
    return newest


def find_missing(
    *,
    store: NoustStore | None = None,
    now: datetime | None = None,
    config: Config | None = None,
    last_application_backup: Callable[[str], datetime | None] = newest_application_backup,
) -> list[MissingBackup]:
    """
    Find the backups that should have been taken and were not.

    Args:
        store: The store; the process-wide one by default.
        now: The clock, for tests.
        config: The configuration, for ``backup.missing_days``.
        last_application_backup: How to learn when an application's schedule
            last took one (its backups are files, not rows).

    Returns:
        One entry per policy whose newest good backup is older than its
        schedule allows. A policy younger than the allowance is not missing
        anything yet.
    """
    target = store or get_store()
    moment = now or datetime.now(timezone.utc)
    raw_days = (config or Config()).get("backup.missing_days", None)
    try:
        missing_days = float(raw_days) if raw_days is not None else None
    except (TypeError, ValueError):
        missing_days = None
    found: list[MissingBackup] = []
    for kind, subject, key, schedule, command, success_text, created_text in _policies(target):
        allowed = threshold(schedule, missing_days=missing_days)
        if allowed is None:
            continue
        last = (
            last_application_backup(subject) if kind == KIND_APPLICATION else _moment(success_text)
        )
        reference = last or _moment(created_text)
        if reference is None or moment - reference <= allowed:
            continue
        found.append(
            MissingBackup(
                key=key,
                kind=kind,
                subject=subject,
                schedule=schedule,
                last_success=last,
                overdue=moment - reference - allowed,
                command=command,
            )
        )
    return found


# ------------------------------------------------------------------ memory


def _remembered(store: NoustStore) -> dict[str, tuple[str, datetime | None]]:
    """
    Read what has been announced and not closed.

    Args:
        store: The store.

    Returns:
        Alert key to ``(kind, when it was last said)``.
    """
    with store._transaction() as cursor:
        cursor.execute("SELECT alert_key, kind, notified_at FROM backup_alerts")
        return {row[0]: (row[1], _moment(row[2])) for row in cursor.fetchall()}


def _remember(store: NoustStore, key: str, kind: str, moment: datetime) -> None:
    """
    Record that an alert was said.

    Args:
        store: The store.
        key: The alert's key.
        kind: ``backup_missing`` or ``destination_unreachable``.
        moment: When it was said.
    """
    now = moment.isoformat(timespec="seconds")
    with store._transaction() as cursor:
        cursor.execute(
            "INSERT INTO backup_alerts (alert_key, kind, since, notified_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(alert_key) DO UPDATE SET notified_at = excluded.notified_at",
            (key, kind, now, now),
        )


def _forget(store: NoustStore, key: str) -> None:
    """
    Close an alert.

    Args:
        store: The store.
        key: The alert's key.
    """
    with store._transaction() as cursor:
        cursor.execute("DELETE FROM backup_alerts WHERE alert_key = ?", (key,))


Publisher = Callable[[Callable[[NotificationContext], Notification]], None]


class BackupWatch:
    """
    The monitor's backup alerts: decide, say it once, and say when it is over.

    Args:
        publish: What the monitor uses to send a notification: it takes the
            composer (a callable from a context to a notification).
        store: The store; the process-wide one by default.
        destination_test: How to ask a destination whether it answers; raises
            a :class:`~noust.core.exceptions.NoustError` when it does not.
    """

    def __init__(
        self,
        publish: Publisher,
        *,
        store: NoustStore | None = None,
        destination_test: Callable[[str], object] | None = None,
    ) -> None:
        self._publish = publish
        self._store = store or get_store()
        self._test = destination_test
        self._backups_checked: datetime | None = None
        self._destinations_checked: datetime | None = None

    def check_backups(
        self, *, now: datetime | None = None, config: Config | None = None
    ) -> list[MissingBackup]:
        """
        Announce backups that went missing and close the ones that came back.

        At most hourly, however often the monitor calls.

        Args:
            now: The clock, for tests.
            config: The configuration, for tests.

        Returns:
            What is missing now.
        """
        moment = now or datetime.now(timezone.utc)
        if self._backups_checked is not None and moment - self._backups_checked < BACKUPS_EVERY:
            return []
        self._backups_checked = moment
        missing = find_missing(store=self._store, now=moment, config=config)
        said = _remembered(self._store)
        current = {item.key for item in missing}
        for item in missing:
            previous = said.get(item.key)
            if previous is None or previous[1] is None or moment - previous[1] >= REMIND_AFTER:
                self._publish(
                    partial(
                        compose_backup_missing,
                        item.kind,
                        item.subject,
                        item.last_success,
                        item.command,
                    )
                )
                _remember(self._store, item.key, "backup_missing", moment)
        for key, (kind, _when) in said.items():
            if kind == "backup_missing" and key not in current:
                self._publish(partial(compose_backup_recovered, key.split(":", 1)[-1]))
                _forget(self._store, key)
        return missing

    def check_destinations(self, *, now: datetime | None = None) -> list[str]:
        """
        Ask each destination in use whether it answers, and announce a change.

        At most every six hours.

        Args:
            now: The clock, for tests.

        Returns:
            The names of the destinations that did not answer.
        """
        moment = now or datetime.now(timezone.utc)
        if (
            self._destinations_checked is not None
            and moment - self._destinations_checked < DESTINATIONS_EVERY
        ):
            return []
        self._destinations_checked = moment
        said = _remembered(self._store)
        down: list[str] = []
        for name in sorted(self._destinations_in_use()):
            key = f"destination:{name}"
            try:
                self._ask(name)
            except (NoustError, OSError, sqlite3.Error) as exc:
                down.append(name)
                previous = said.get(key)
                if previous is None or previous[1] is None or moment - previous[1] >= REMIND_AFTER:
                    error = _words(exc)
                    self._publish(partial(compose_destination_unreachable, name, error))
                    _remember(self._store, key, "destination_unreachable", moment)
                continue
            if key in said:
                self._publish(partial(compose_destination_recovered, name))
                _forget(self._store, key)
        return down

    def _ask(self, name: str) -> object:
        """
        Ask one destination whether it answers.

        Args:
            name: The destination.

        Returns:
            Whatever the test answered.
        """
        if self._test is not None:
            return self._test(name)
        from noust.managers.backup_destinations import BackupDestinationManager

        return BackupDestinationManager().test(name)

    def _destinations_in_use(self) -> set[str]:
        """
        Name the destinations some enabled policy sends to.

        Returns:
            Their names; a destination nothing uses is not worth a message.
        """
        from noust.managers.database.backup_records import BackupRecords
        from noust.managers.self_backup_records import SelfBackupRecords
        from noust.managers.volume_backup_records import VolumeRecords

        used: set[str] = set()
        lists: list[list[dict[str, object]]] = [
            policy.destinations for policy in BackupRecords(self._store).policies()
        ]
        lists.extend(policy.destinations for policy in VolumeRecords(self._store).policies())
        own = SelfBackupRecords(self._store).get()
        if own is not None:
            lists.append(own.destinations)
        lists.extend(schedule.destinations for schedule in self._store.list_backup_schedules())
        for entries in lists:
            used.update(str(entry["name"]) for entry in entries if entry.get("name"))
        return used


def _words(error: Exception) -> str:
    """
    Say what a failure said, with its advice.

    Args:
        error: What was raised.

    Returns:
        ``message`` and, when there is one, ``Details: ...``, as the
        notification composers read it.
    """
    message = getattr(error, "message", None) or str(error)
    details = getattr(error, "details", None)
    return f"{message}\n  Details: {details}" if details else str(message)
