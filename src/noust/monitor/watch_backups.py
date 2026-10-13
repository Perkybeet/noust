# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Scheduled backups that did not run (checklist K-17).

A backup that fails says so (``backup_failed``). One that never starts says
nothing: the timer was disabled, the machine was off at the hour and the timer
was not persistent, the unit cannot be found, the service fails before it can
notify. The Overview shows backups in the last 24 hours, but nobody reads the
Overview at 3 a.m. This watch is the event for that silence: for every
application with a backup schedule it asks when the newest backup of it was
made, and says so when that is longer ago than the schedule allows.

"Longer than the schedule allows" is ``monitor.backup_missing_days`` (2 by
default; 0 turns the check off), stretched for the weekly and monthly aliases to
their period and a day. A schedule written as a calendar expression is judged by
the configured days: Noust does not guess a period out of an expression.

It says it once and again every ``monitor.backup_missing_renotify_hours`` (24)
while the backup stays missing, and forgets an application the day a backup
exists again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from functools import partial
from typing import Any

from noust.core.notifications.composers_ops import compose_backup_missing
from noust.core.store import get_store
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the moment each application was last announced.
SCOPE = "backups"

#: Days a schedule alias needs between two backups, before the day of grace.
_ALIAS_PERIOD_DAYS = {"hourly": 0, "daily": 1, "weekly": 7, "monthly": 31}

DEFAULT_MISSING_DAYS = 2
DEFAULT_CHECK_HOURS = 6.0
DEFAULT_RENOTIFY_HOURS = 24.0


def _moment(text: str | None) -> datetime | None:
    """
    Args:
        text: A stored timestamp, naive (local) or with an offset.

    Returns:
        It as an aware datetime, or None for anything else.
    """
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else moment.astimezone()


def allowed_days(schedule: str, configured: int) -> int:
    """
    Say how many days without a backup a schedule tolerates.

    Args:
        schedule: The schedule as the operator wrote it (an alias or a calendar).
        configured: ``monitor.backup_missing_days``.

    Returns:
        The configured days, or for ``weekly`` and ``monthly`` their period plus
        a day when that is more.
    """
    period = _ALIAS_PERIOD_DAYS.get(schedule.strip().lower(), 0)
    return max(configured, period + 1) if period else configured


class BackupWatch(Watch):
    """Announces a scheduled application that has no recent backup."""

    name = "backups"
    default_interval_s = DEFAULT_CHECK_HOURS * 3600

    def __init__(self, lister: Callable[[], Iterable[Any]] | None = None) -> None:
        """
        Args:
            lister: Lists the backups taken, as objects with ``domain`` and
                ``created_at``; the backup manager's by default. Tests inject one.
        """
        self._lister = lister

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.backup_check_hours`` in seconds; zero (off) when
            ``monitor.backup_missing_days`` is zero.
        """
        config = services.config
        if int(config.get("monitor.backup_missing_days", DEFAULT_MISSING_DAYS) or 0) <= 0:
            return 0
        hours = float(config.get("monitor.backup_check_hours", DEFAULT_CHECK_HOURS))
        return max(hours, 0.05) * 3600

    def _backups(self) -> list[Any]:
        """
        Returns:
            Every backup taken, newest first.
        """
        if self._lister is not None:
            return list(self._lister())
        from noust.managers.backup_manager import BackupManager

        return list(BackupManager(verbose=False).list_backups())

    def check(self, services: WatchServices) -> None:
        """
        Compare each backup schedule with the newest backup of its application.

        Args:
            services: What the monitor lends.
        """
        config = services.config
        days = int(config.get("monitor.backup_missing_days", DEFAULT_MISSING_DAYS))
        renotify = float(
            config.get("monitor.backup_missing_renotify_hours", DEFAULT_RENOTIFY_HOURS)
        )
        now = datetime.fromtimestamp(services.clock(), tz=timezone.utc)

        schedules = get_store().list_backup_schedules()
        newest: dict[str, datetime] = {}
        for backup in self._backups():
            taken = _moment(getattr(backup, "created_at", None))
            domain = getattr(backup, "domain", "")
            if taken is not None and (domain not in newest or taken > newest[domain]):
                newest[domain] = taken

        watched: set[str] = set()
        for schedule in schedules:
            domain = schedule.app_domain
            watched.add(domain)
            limit = timedelta(days=allowed_days(schedule.schedule, days))
            last = newest.get(domain)
            created = _moment(getattr(schedule, "created_at", None))
            # A schedule made a moment ago has not had its chance yet.
            since = last or created
            if since is not None and now - since <= limit:
                services.state.delete(SCOPE, domain)
                continue
            said = services.state.get(SCOPE, domain)
            if said is not None and services.clock() - float(said) < renotify * 3600:
                continue
            services.state.put(SCOPE, domain, str(services.clock()))
            services.publish(
                partial(
                    compose_backup_missing,
                    domain,
                    (now - since).days if since is not None else days,
                    last_backup=last.date().isoformat() if last is not None else None,
                    schedule=schedule.schedule,
                )
            )
        services.state.keep_only(SCOPE, watched)
