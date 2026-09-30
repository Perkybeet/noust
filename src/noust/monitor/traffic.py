"""
Requests and server errors of a site, read from its web server's access log.

A static site has no process, so the only useful thing to measure about it is
its traffic. The site templates write ``<domain>.access.log`` in the combined
format for nginx and for Apache; this module counts the requests and the 5xx
responses that landed in it since the last look.

The work is bounded on purpose, because it runs inside the collector's tick:

- The log is **read incrementally**: where the last read stopped is kept (inode
  and byte offset) in the metrics database, so a restart neither counts a line
  twice nor rereads the file.
- At most :data:`MAX_BYTES_PER_LOG` bytes of one log and
  :data:`TICK_BUDGET_BYTES` of all logs are read in a tick; the rest is read on
  the next ones, and a backlog larger than :data:`MAX_BACKLOG_BYTES` (a daemon
  that was down for a day) is skipped to its newest part instead of replayed.
- A log seen for the first time is read from its end: its history is not a burst
  of traffic that happened just now.
- A line is counted only when it is complete; a half-written one waits.

Log rotation replaces the file (a new inode) or truncates it (a smaller size):
either starts the new file from its beginning. The lines written between the
last read and the rotation are lost, which costs at most one tick of traffic.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from noust.monitor.plan import SamplingPlan
from noust.monitor.timeseries import MetricsStore

log = logging.getLogger(__name__)

#: The most bytes of one log read in a tick.
MAX_BYTES_PER_LOG = 512 * 1024

#: The most bytes read across every log in a tick.
TICK_BUDGET_BYTES = 2 * 1024 * 1024

#: Past this much unread, the reader skips ahead instead of replaying it.
MAX_BACKLOG_BYTES = 8 * 1024 * 1024

#: ``ip ident user [time] "request" status``: the combined format nginx and
#: Apache share. The request is quoted and may hold escaped quotes.
_LINE = re.compile(rb'^\S+ \S+ \S+ \[[^\]]*\] "(?:[^"\\]|\\.)*" (\d{3}) ')

REQUESTS_SUFFIX = "http.requests_per_min"
ERRORS_SUFFIX = "http.errors_5xx_per_min"


def traffic_metric_names(domain: str) -> tuple[str, str]:
    """
    Name the two traffic series of an application.

    Args:
        domain: The application's domain.

    Returns:
        ``(requests per minute, 5xx responses per minute)`` metric names.
    """
    return f"app.{domain}.{REQUESTS_SUFFIX}", f"app.{domain}.{ERRORS_SUFFIX}"


@dataclass(frozen=True)
class LogDelta:
    """
    What one read of a log found.

    Attributes:
        requests: Lines counted.
        errors_5xx: Of those, the ones with a 5xx status.
        inode: The file's inode now.
        position: Where the next read resumes.
        first_read: True when the log had never been read: nothing is counted,
            and the caller has no interval to turn the count into a rate.
        rotated: True when the file was replaced or truncated.
        skipped: Bytes skipped because the backlog was too large.
    """

    requests: int
    errors_5xx: int
    inode: int
    position: int
    first_read: bool = False
    rotated: bool = False
    skipped: int = 0


def count_lines(data: bytes) -> tuple[int, int]:
    """
    Count requests and 5xx responses in complete access log lines.

    Args:
        data: Log lines, newline separated.

    Returns:
        ``(requests, errors_5xx)``. A line that is not in the combined format
        is not a request and is not counted.
    """
    requests = errors = 0
    for line in data.split(b"\n"):
        match = _LINE.match(line)
        if match is None:
            continue
        requests += 1
        if match.group(1).startswith(b"5"):
            errors += 1
    return requests, errors


def read_log_delta(
    path: Path,
    cursor: tuple[int, int] | None,
    *,
    max_bytes: int = MAX_BYTES_PER_LOG,
) -> LogDelta:
    """
    Read what a log gained since the cursor.

    Args:
        path: The access log.
        cursor: ``(inode, position)`` where the last read stopped, or None for
            a log never read.
        max_bytes: Most bytes to read.

    Returns:
        The counts and the new cursor.

    Raises:
        OSError: When the log cannot be opened or read (it vanished, or
            permission).
    """
    stat = os.stat(path)
    inode, size = stat.st_ino, stat.st_size

    if cursor is None:
        return LogDelta(0, 0, inode, size, first_read=True)

    last_inode, position = cursor
    rotated = inode != last_inode or size < position
    if rotated:
        position = 0
    skipped = 0
    if size - position > MAX_BACKLOG_BYTES:
        skipped = (size - max_bytes) - position
        position = size - max_bytes
    if size <= position:
        return LogDelta(0, 0, inode, position, rotated=rotated, skipped=skipped)

    with open(path, "rb") as handle:
        handle.seek(position)
        chunk = handle.read(min(max_bytes, size - position))

    if skipped:
        # Landed mid-line: drop the fragment up to the first newline.
        cut = chunk.find(b"\n")
        dropped = cut + 1 if cut >= 0 else len(chunk)
        chunk = chunk[dropped:]
        position += dropped

    end = chunk.rfind(b"\n")
    if end < 0:
        # Not a single complete line yet. An oversized line would stall the
        # reader for good, so a chunk that fills the budget without a newline
        # is abandoned.
        consumed = len(chunk) if len(chunk) >= max_bytes else 0
        return LogDelta(0, 0, inode, position + consumed, rotated=rotated, skipped=skipped)

    requests, errors = count_lines(chunk[: end + 1])
    return LogDelta(
        requests,
        errors,
        inode,
        position + end + 1,
        rotated=rotated,
        skipped=skipped,
    )


class TrafficSampler:
    """
    Turns the access logs of the applications that have one into rate series.

    The cursor of each log lives in the metrics store, so the reader survives
    a restart of the process that runs it.
    """

    def __init__(
        self,
        store: MetricsStore,
        *,
        budget_bytes: int = TICK_BUDGET_BYTES,
        reader: Callable[..., LogDelta] = read_log_delta,
    ) -> None:
        """
        Args:
            store: Where the cursors are kept.
            budget_bytes: Most bytes to read across all logs in a tick.
            reader: Reads one log; injected so tests need no files.
        """
        self.store = store
        self.budget_bytes = budget_bytes
        self._reader = reader
        self._last_read: dict[str, float] = {}
        self._turn = 0

    def sample(self, plans: Mapping[str, SamplingPlan], now: float) -> list[tuple[str, float]]:
        """
        Count the traffic every planned log gained since it was last read.

        Args:
            plans: The applications' plans; those with a ``traffic_log``.
            now: The current monotonic time, to turn counts into rates.

        Returns:
            ``(metric, value)`` pairs: requests per minute and 5xx per minute
            of each log read. A log read for the first time, one that cannot
            be read, and one skipped for the budget contribute nothing this
            tick. Requests are a count of lines over the seconds since the
            previous read, expressed per minute.
        """
        logged = sorted(
            ((plan, plan.traffic_log) for plan in plans.values() if plan.traffic_log is not None),
            key=lambda item: item[0].domain,
        )
        if not logged:
            self.store.prune_log_cursors([])
            return []

        # Start each tick one log further, so that when the budget runs out it
        # is not always the same sites at the end that go unread.
        self._turn = (self._turn + 1) % len(logged)
        ordered = logged[self._turn :] + logged[: self._turn]

        pairs: list[tuple[str, float]] = []
        budget = self.budget_bytes
        for plan, path in ordered:
            if budget <= 0:
                break
            key = str(path)
            try:
                cursor = self.store.read_log_cursor(key)
                delta = self._reader(path, cursor, max_bytes=min(MAX_BYTES_PER_LOG, budget))
            except OSError as exc:
                log.debug("access log %s could not be read: %s", path, exc)
                continue
            if not delta.first_read:
                started = 0 if cursor is None or delta.rotated else cursor[1]
                budget -= max(0, delta.position - started)
            self.store.write_log_cursor(key, delta.inode, delta.position)

            previous = self._last_read.get(key)
            self._last_read[key] = now
            if delta.first_read or previous is None or now <= previous:
                continue
            minutes = (now - previous) / 60.0
            requests_name, errors_name = traffic_metric_names(plan.domain)
            pairs.append((requests_name, delta.requests / minutes))
            pairs.append((errors_name, delta.errors_5xx / minutes))

        self.store.prune_log_cursors(str(path) for _plan, path in logged)
        return pairs
