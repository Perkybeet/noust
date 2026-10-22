# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Waiting for a job the server runs.

Updates, rollbacks, backups and first deployments are jobs: the API answers
``202`` with the job and the work goes on. A client that wants the outcome (the
remote command line, a manifest being applied, a pipeline step) waits here, in
one place, printing the job's log as it grows and giving up when the time runs
out - the job keeps running; only the waiting stops.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, Protocol

#: A job is over when its state is one of these.
FINAL_STATES = frozenset({"succeeded", "failed", "cancelled"})
#: How long a wait lasts at most by default, and how often the job is looked at.
DEFAULT_WAIT = 1800.0
POLL_FIRST = 1.0
POLL_LONGEST = 5.0
#: The most of a job's log read at once.
LOG_TAIL = 20000


class Getter(Protocol):
    """Anything that can read a JSON route: an API client, or the console's own gateway."""

    def get(self, path: str, **query: Any) -> Any:
        """
        Args:
            path: A path below the API.
            **query: Query parameters.

        Returns:
            The decoded body.
        """


def follow(
    client: Getter,
    job_id: str,
    *,
    on_line: Callable[[str], None] | None = None,
    timeout: float = DEFAULT_WAIT,
    sleep: Callable[[float], None] | None = None,
    clock: Callable[[], float] | None = None,
) -> dict[str, Any]:
    """
    Wait for a job, handing its log lines to ``on_line`` as they appear.

    Args:
        client: Reads the server's routes.
        job_id: The job.
        on_line: Called once with each new line of the job's log; None skips the log.
        timeout: Seconds to wait before giving up.
        sleep: Replaces waiting, for tests (``time.sleep`` by default).
        clock: Replaces the monotonic clock, for tests.

    Returns:
        The job as the server last described it; its ``state`` says how it
        ended, or that it is still running when the wait ran out.
    """
    pause = sleep or time.sleep
    now = clock or time.monotonic
    deadline = now() + timeout
    seen = 0
    interval = POLL_FIRST
    while True:
        job: dict[str, Any] = client.get(f"/jobs/{job_id}")
        if on_line is not None:
            log = client.get(f"/jobs/{job_id}/log", tail=LOG_TAIL)
            lines = str(log.get("content") or "").splitlines()
            for line in lines[seen:]:
                on_line(line)
            seen = max(seen, len(lines))
        if job.get("state") in FINAL_STATES or now() >= deadline:
            return job
        pause(interval)
        interval = min(POLL_LONGEST, interval * 1.5)
