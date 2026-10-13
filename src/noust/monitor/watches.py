# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The checks the monitor runs on a clock of their own, and what they share.

The monitor's older checks (units, applications, certificates, disks) run on
every scan and each keeps its own JSON sidecar. The checks added in 3.4 are not
all for every scan: asking a package manager what is pending is a daily
question, listing backups an hourly one, reading cron timers a per-minute one.
A :class:`Watch` is one such check: it names itself, says how often it wants to
run, and does its work with the services the monitor hands it
(:class:`WatchServices`): a way to publish a notification, the store's memory
(:class:`~noust.core.notification_state.NotificationState`), the runner and the
configuration. :func:`run_due` runs the ones whose time has come.

Three rules hold for every watch:

- **One announcement per episode.** A watch remembers what it already said
  (in ``notification_state``, so a restart of the daemon does not repeat it)
  and says it again only when the situation changes or a long time passed.
- **Not knowing is not a change.** A watch that could not look (the package
  manager is locked, the journal unreadable) leaves what it remembered alone.
- **A watch never acts.** Like the rest of the monitor it reports and does
  nothing else.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field

from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.core.notification_state import NotificationState
from noust.core.notifications.context import NotificationContext
from noust.core.notifications.model import Notification
from noust.core.runner import CommandRunner

log = logging.getLogger(__name__)

#: What a watch can raise while it looks at the machine or the store: logged
#: by :func:`run_due`, never allowed to stop the monitor's loop. Anything else
#: is a defect, and the daemon's supervisor restarts it.
WATCH_ERRORS: tuple[type[Exception], ...] = (NoustError, OSError, sqlite3.Error)

#: The scope of the memory :func:`run_due` itself keeps (when each watch last ran).
SCHEDULE_SCOPE = "watch"

Publish = Callable[[Callable[[NotificationContext], Notification]], None]


@dataclass
class WatchServices:
    """
    What the monitor lends a watch.

    Attributes:
        publish: Composes a notification from the context and delivers it.
        state: What watches remember between scans.
        runner: The one way a process is run.
        config: The configuration, as of this scan.
        clock: Epoch seconds. Tests move it.
    """

    publish: Publish
    state: NotificationState
    runner: CommandRunner
    config: Config
    clock: Callable[[], float] = field(default=time.time)


class Watch(ABC):
    """One check of the monitor, run on its own interval."""

    #: Names the watch in the monitor's memory and logs.
    name: str = ""

    #: Seconds between two runs when the configuration does not say.
    default_interval_s: float = 60.0

    def interval_s(self, services: WatchServices) -> float:
        """
        Say how often this watch runs.

        Args:
            services: What the monitor lends, for the configuration.

        Returns:
            Seconds between runs; zero or less turns the watch off.
        """
        return self.default_interval_s

    @abstractmethod
    def check(self, services: WatchServices) -> None:
        """
        Look, remember and, when something changed, publish.

        Args:
            services: What the monitor lends.
        """


def run_due(watches: list[Watch], services: WatchServices) -> list[str]:
    """
    Run the watches whose time has come.

    The moment a watch ran is written *before* it runs, so one that fails does
    not try again on the next scan and the one after: it waits its interval
    like one that worked, and its failure is in the log.

    Args:
        watches: Every watch the monitor has.
        services: What the monitor lends them.

    Returns:
        The names of the watches that ran.
    """
    now = services.clock()
    ran: list[str] = []
    for watch in watches:
        interval = watch.interval_s(services)
        if interval <= 0:
            continue
        try:
            last = float(services.state.get(SCHEDULE_SCOPE, watch.name) or 0)
            if now - last < interval:
                continue
            services.state.put(SCHEDULE_SCOPE, watch.name, str(now))
            watch.check(services)
        except WATCH_ERRORS as exc:
            log.warning("The %s check failed: %s", watch.name, exc)
            continue
        ran.append(watch.name)
    return ran
