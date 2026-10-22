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
from noust.managers.backup_watch import destinations_in_use
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the moment each destination was announced.
SCOPE = "destinations"

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
        return max(hours, 0.05) * 3600

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
                said = services.state.get(SCOPE, name)
                if said is not None and services.clock() - float(said) < renotify * 3600:
                    continue
                services.state.put(SCOPE, name, str(services.clock()))
                services.publish(partial(compose_destination_unreachable, name, _words(exc)))
                continue
            if services.state.get(SCOPE, name) is not None:
                services.state.delete(SCOPE, name)
                services.publish(partial(compose_destination_recovered, name))
        services.state.keep_only(SCOPE, used)
