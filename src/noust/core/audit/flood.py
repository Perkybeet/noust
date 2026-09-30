# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Flood protection for anonymous events.

Anyone who can reach the console can make it write an audit event: a refused
sign-in, a refused handshake. Before 3.1 the log was rotated by size, so a
flood of those erased the older, interesting history (finding H3). Retention
is by time now and nothing inside it is deleted, which moves the problem to
the disk; this guard is what keeps it bounded.

Only events whose actor is ``anonymous`` are ever held back, and never a
critical one (a lockout). Within a window, the first :attr:`FloodGuard.burst`
occurrences with the same event, source and target are written in full, and
the first ten times that many with the same event and target from any source;
the rest are *counted*, and the count is written as one ``audit.coalesced``
event when the window closes. A flood therefore costs a few lines and leaves
its size, its sources and its duration on record: nothing is dropped without
saying so.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: Distinct sources a summary names; the rest are only counted.
SOURCE_SAMPLE = 16

#: Keys tracked at once; beyond this, new keys share one bucket per event.
MAX_KEYS = 4096

#: How many times the per-source burst may arrive from all sources together.
AGGREGATE_FACTOR = 10


@dataclass
class _Bucket:
    started: float
    written: int = 0
    suppressed: int = 0
    first_suppressed: float | None = None
    last_suppressed: float | None = None
    sources: set[str] = field(default_factory=set)
    distinct: int = 0


@dataclass(frozen=True)
class FloodSummary:
    """
    What a window held back.

    Attributes:
        event: The event that was repeated.
        target: What it was aimed at.
        count: Occurrences not written individually.
        sources: Up to :data:`SOURCE_SAMPLE` of the addresses they came from.
        distinct_sources: How many different addresses there were.
        first: Monotonic time of the first occurrence held back.
        last: Monotonic time of the last one.
        window_seconds: The window's length.
    """

    event: str
    target: str | None
    count: int
    sources: tuple[str, ...]
    distinct_sources: int
    first: float
    last: float
    window_seconds: float


class FloodGuard:
    """
    Decide which anonymous events are written and count the rest.

    Thread-safe: the console's request threads share one guard.
    """

    def __init__(self, window: float, burst: int, max_keys: int = MAX_KEYS) -> None:
        """
        Args:
            window: Seconds a window lasts.
            burst: Occurrences per event, source and target written in full
                in one window.
            max_keys: Distinct keys tracked before new ones share a bucket.
        """
        self.window = float(window)
        self.burst = max(1, int(burst))
        self.max_keys = max_keys
        self._lock = threading.Lock()
        self._per_source: dict[tuple[str, str, str], _Bucket] = {}
        self._aggregate: dict[tuple[str, str], _Bucket] = {}

    def admit(
        self, event: str, source: str | None, target: str | None, now: float, *, tight: bool = False
    ) -> tuple[bool, list[FloodSummary]]:
        """
        Decide about one anonymous event.

        Args:
            event: The event name.
            source: Where it came from.
            target: What it was aimed at.
            now: Monotonic time.
            tight: The log is over its size limit: allow one occurrence per
                window instead of a burst.

        Returns:
            Whether to write this event, and the summaries of windows that
            closed and must be written first.
        """
        burst = 1 if tight else self.burst
        origin = source or "-"
        aim = target or "-"
        with self._lock:
            summaries = self._expire(now)
            aggregate_key = (event, aim)
            aggregate = self._aggregate.get(aggregate_key)
            if aggregate is None:
                aggregate = self._aggregate[aggregate_key] = _Bucket(started=now)

            source_key = (event, origin, aim)
            bucket = self._per_source.get(source_key)
            if bucket is None:
                if len(self._per_source) >= self.max_keys:
                    source_key = (event, "*", aim)
                    bucket = self._per_source.get(source_key)
                if bucket is None:
                    bucket = self._per_source[source_key] = _Bucket(started=now)

            if bucket.written < burst and aggregate.written < burst * AGGREGATE_FACTOR:
                bucket.written += 1
                aggregate.written += 1
                return True, summaries

            aggregate.suppressed += 1
            aggregate.first_suppressed = aggregate.first_suppressed or now
            aggregate.last_suppressed = now
            if origin not in aggregate.sources:
                aggregate.distinct += 1
                if len(aggregate.sources) < SOURCE_SAMPLE:
                    aggregate.sources.add(origin)
            return False, summaries

    def flush(self, now: float, *, force: bool = False) -> list[FloodSummary]:
        """
        Close the windows that have ended.

        Args:
            now: Monotonic time.
            force: Close every window, ended or not: the process is stopping.

        Returns:
            A summary for each closed window that held something back.
        """
        with self._lock:
            return self._expire(now, force=force)

    def _expire(self, now: float, *, force: bool = False) -> list[FloodSummary]:
        summaries: list[FloodSummary] = []
        for key, bucket in list(self._aggregate.items()):
            if force or now - bucket.started >= self.window:
                del self._aggregate[key]
                if bucket.suppressed:
                    summaries.append(
                        FloodSummary(
                            event=key[0],
                            target=None if key[1] == "-" else key[1],
                            count=bucket.suppressed,
                            sources=tuple(sorted(bucket.sources)),
                            distinct_sources=bucket.distinct,
                            first=bucket.first_suppressed or bucket.started,
                            last=bucket.last_suppressed or bucket.started,
                            window_seconds=self.window,
                        )
                    )
        for source_key, source_bucket in list(self._per_source.items()):
            if force or now - source_bucket.started >= self.window:
                del self._per_source[source_key]
        return summaries
