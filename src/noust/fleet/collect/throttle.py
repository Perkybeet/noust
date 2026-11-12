# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A speed limit for what a central reads from a server (item 195).

The office line is not the central's: a copy of several gigabytes taken at full speed starves
whatever else the line carries. The limit is the central's own pacing of its reads, in bytes a
second over a transfer, and not something ssh does: it works the same on every client, and a limit
changed in the console applies to the next piece.
"""

from __future__ import annotations

import time
from collections.abc import Callable

__all__ = ["Throttle"]


class Throttle:
    """
    Pace reads to at most a rate.

    Args:
        bytes_per_second: The most to read a second; None for no limit.
        clock: Monotonic seconds (tests move it).
        sleep: Waits that many seconds (tests record it).
    """

    def __init__(
        self,
        bytes_per_second: float | None,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.rate = bytes_per_second if bytes_per_second and bytes_per_second > 0 else None
        self._clock = clock
        self._sleep = sleep
        self._due = 0.0

    def take(self, count: int) -> float:
        """
        Account for bytes that were just read, waiting until the rate allows the next.

        Args:
            count: How many bytes were read.

        Returns:
            Seconds waited.
        """
        if self.rate is None or count <= 0:
            return 0.0
        now = self._clock()
        self._due = max(self._due, now) + count / self.rate
        wait = self._due - now
        if wait > 0:
            self._sleep(wait)
        return max(0.0, wait)
