# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backup destinations that stopped answering (checklist H-09).

A destination whose key expired, whose bucket was deleted or whose host went away
fails only the next upload, which may be days off, and by then the backups it
should have been holding are the ones that cannot be found. This watch asks each
destination some policy sends to the same question ``noust backup destination
test`` asks (rclone ``mkdir`` and ``lsf``) and says so, once per outage, with
rclone's own words; it says it again every ``monitor.backup_missing_renotify_hours``
while the outage lasts, and once more when the destination answers.

**One failed probe is not an outage** (item 201): a night of bad network lost packets for hours and
the first probe that happened to fall in it said the destination was unreachable, after the runs
had said it for every copy. It takes :data:`PROBES_BEFORE_SAYING` failed probes in a row, like
``app_unreachable`` takes three failed answers, and while a destination is in doubt (or is said to be
down, to catch its return) the watch looks again every :data:`SUSPECT_INTERVAL_S` instead of every
``monitor.backup_check_hours``. And it is **silent when the runs have already said it**: the copies
that could not be sent to the destination are announced for it (:mod:`noust.managers.backup_sending`)
and one message is enough; each of the two is silent while the other has spoken within the window.

It runs as often as the monitor looks for missing backups
(``monitor.backup_check_hours``) and remembers what it announced in the monitor's
memory, so a restart does not announce it twice.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from functools import partial

from noust.core.exceptions import NoustError
from noust.core.notifications.composers import (
    compose_destination_recovered,
    compose_destination_unreachable,
)
from noust.managers.backup_sending import SENDS_SCOPE, WATCH_SCOPE
from noust.managers.backup_watch import destinations_in_use
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the moment each destination was announced. The sends of the
#: runs read it (``noust.managers.backup_sending``), which is why the name is theirs.
SCOPE = WATCH_SCOPE

#: And the failed probes in a row of each destination that did not answer, with the moment of the first.
PROBES_SCOPE = "destinations-probes"

#: Failed probes in a row before a destination is said to be unreachable.
PROBES_BEFORE_SAYING = 3

#: Seconds between two looks while a destination is in doubt or said to be down. Five minutes is long
#: enough for a glitch to be over and short enough that three of them are a quarter of an hour.
SUSPECT_INTERVAL_S = 300.0

DEFAULT_CHECK_HOURS = 6.0
DEFAULT_RENOTIFY_HOURS = 24.0


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


class DestinationWatch(Watch):
    """Announces a backup destination that does not answer, and when it does again."""

    name = "destinations"
    default_interval_s = DEFAULT_CHECK_HOURS * 3600

    def __init__(
        self,
        tester: Callable[[str], object] | None = None,
        in_use: Callable[[], set[str]] | None = None,
    ) -> None:
        """
        Args:
            tester: Asks one destination whether it answers and raises a
                :class:`~noust.core.exceptions.NoustError` when it does not; the
                destination manager's ``test`` by default. Tests inject one.
            in_use: Names the destinations some policy sends to; the store's by
                default.
        """
        self._tester = tester
        self._in_use = in_use if in_use is not None else destinations_in_use

    def interval_s(self, services: WatchServices) -> float:
        """
        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            ``monitor.backup_check_hours`` in seconds.
        """
        hours = float(services.config.get("monitor.backup_check_hours", DEFAULT_CHECK_HOURS))
        usual = max(hours, 0.05) * 3600
        if services.state.items(PROBES_SCOPE) or services.state.items(SCOPE):
            return min(usual, SUSPECT_INTERVAL_S)
        return usual

    def _ask(self, name: str) -> object:
        """
        Ask one destination whether it answers.

        Args:
            name: The destination.

        Returns:
            Whatever the test answered.
        """
        if self._tester is not None:
            return self._tester(name)
        from noust.managers.backup_destinations import BackupDestinationManager

        return BackupDestinationManager().test(name)

    def check(self, services: WatchServices) -> None:
        """
        Ask each destination in use and announce a change.

        Args:
            services: What the monitor lends.
        """
        renotify = float(
            services.config.get("monitor.backup_missing_renotify_hours", DEFAULT_RENOTIFY_HOURS)
        )
        used = self._in_use()
        for name in sorted(used):
            try:
                self._ask(name)
            except (NoustError, OSError, sqlite3.Error) as exc:
                if self._failures(services, name) < PROBES_BEFORE_SAYING:
                    continue
                said = services.state.get(SCOPE, name)
                if said is not None and services.clock() - float(said) < renotify * 3600:
                    continue
                if self._runs_said(services, name, renotify * 3600):
                    continue
                services.state.put(SCOPE, name, str(services.clock()))
                services.publish(partial(compose_destination_unreachable, name, _words(exc)))
                continue
            services.state.delete(PROBES_SCOPE, name)
            if services.state.get(SCOPE, name) is not None:
                services.state.delete(SCOPE, name)
                services.publish(partial(compose_destination_recovered, name))
        services.state.keep_only(SCOPE, used)
        services.state.keep_only(PROBES_SCOPE, used)

    @staticmethod
    def _failures(services: WatchServices, name: str) -> int:
        """
        Count one more failed probe of a destination.

        Args:
            services: What the monitor lends.
            name: The destination.

        Returns:
            How many probes in a row have failed, this one included.
        """
        counted = services.state.get_json(PROBES_SCOPE, name)
        if not isinstance(counted, dict) or not isinstance(counted.get("fails"), int):
            counted = {}
        fails = int(counted.get("fails", 0)) + 1
        services.state.put_json(
            PROBES_SCOPE, name, {"fails": fails, "since": counted.get("since", services.clock())}
        )
        return fails

    @staticmethod
    def _runs_said(services: WatchServices, name: str, window: float) -> bool:
        """
        Whether the runs that could not send to a destination have already said so.

        Args:
            services: What the monitor lends.
            name: The destination.
            window: Seconds within which what was said is not said again.

        Returns:
            True when the sends announced the destination within the window and have not closed it.
        """
        told = services.state.get_json(SENDS_SCOPE, name)
        if not isinstance(told, dict) or not isinstance(told.get("at"), (int, float)):
            return False
        return bool(services.clock() - float(told["at"]) < window)
