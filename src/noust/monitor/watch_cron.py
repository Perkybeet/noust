# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cron jobs that ended with an error (checklist K-18 and G-20).

A cron job is a systemd timer and a service. When the service fails, systemd
records it and nothing else happens: no event, no message, a script that has not
done its work since Tuesday. This watch looks at every owned job once a minute
(two commands for all of them, :meth:`~noust.managers.cron_manager.CronManager.last_outcomes`)
and announces each run it has not announced yet:

- a run that ended with an error is ``cron_failed``, with the job's own last
  lines of output as evidence;
- a run that ended well is ``cron_success``, a heartbeat that ships switched
  off (``notifications.events.cron_success``), for whoever wants to know the
  nightly job did run.

What it remembers is systemd's id of the last run it saw, per job. The first
time it sees a job it only remembers (it will not announce a failure from
before it was looking), and a job that has not finished yet is left alone until
it has. A job that was deleted is forgotten.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from functools import partial

from noust.core.exceptions import NoustError
from noust.core.notifications.composers_ops import compose_cron
from noust.managers.cron_manager import CronManager, CronOutcome
from noust.monitor.watches import Watch, WatchServices

#: The scope of what this watch remembers: the last run seen of each job.
SCOPE = "cron"

#: Lines of a failed run's output that go into its notification.
OUTPUT_LINES = 15


class CronWatch(Watch):
    """Announces the runs of cron jobs, failed ones above all."""

    name = "cron"
    default_interval_s = 60.0

    def __init__(
        self,
        manager: CronManager | None = None,
        output: Callable[[str], str | None] | None = None,
    ) -> None:
        """
        Args:
            manager: The cron manager; one over the monitor's runner by default.
            output: Returns the last lines a job printed; the manager's journal
                reader by default. Tests inject one.
        """
        self._manager = manager
        self._output = output

    def _last_lines(self, manager: CronManager, job: str) -> str | None:
        """
        Read what a failed job printed in its last run.

        Args:
            manager: The cron manager.
            job: The job.

        Returns:
            Its last lines, verbatim; None when the journal has none or cannot
            be read (the notification is worth sending without them).
        """
        if self._output is not None:
            return self._output(job)
        try:
            runs = manager.runs(job, limit=1, lines=OUTPUT_LINES)
        except NoustError:
            return None
        return "\n".join(runs[0]["lines"]) if runs and runs[0]["lines"] else None

    def check(self, services: WatchServices) -> None:
        """
        Announce the runs that ended since the last look.

        Args:
            services: What the monitor lends.
        """
        manager = self._manager or CronManager(runner=services.runner)
        outcomes = manager.last_outcomes()
        state = services.state
        for outcome in outcomes:
            if outcome.running:
                continue
            seen = state.get(SCOPE, outcome.name)
            if seen == outcome.invocation:
                continue
            state.put(SCOPE, outcome.name, outcome.invocation)
            if seen is None:
                # First look at this job: remember, do not announce the past.
                continue
            self._announce(manager, outcome, services)
        state.keep_only(SCOPE, {outcome.name for outcome in outcomes})

    def _announce(
        self, manager: CronManager, outcome: CronOutcome, services: WatchServices
    ) -> None:
        """
        Publish one run.

        Args:
            manager: The cron manager, for the output of a failed run.
            outcome: How the run ended.
            services: What the monitor lends.
        """
        if not outcome.invocation:
            return
        output = None if outcome.ok else self._last_lines(manager, outcome.name)
        services.publish(
            partial(
                compose_cron,
                outcome.name,
                outcome.ok,
                exit_code=outcome.exit_code,
                result=outcome.result,
                ran_at=datetime.fromtimestamp(services.clock(), tz=timezone.utc),
                output=output,
                domain=outcome.app_domain or None,
            )
        )
