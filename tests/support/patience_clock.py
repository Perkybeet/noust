# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The clock a patient call to a destination waits by, in a test (item 201).

A call that works (``noust.managers.destination_patience``) waits half a minute and then two between
its attempts and is held to a ceiling of two hours. A test never lets it: the clock and the pause it
reads are replaced by this one, whose pause only moves the clock. ``tests/conftest.py`` installs it
for every test, so that no test of anything that sends a backup can wait for real; a test that wants
to look at what was waited asks for the ``patience_clock`` fixture.
"""

from __future__ import annotations


class PatienceClock:
    """
    A monotonic clock whose pause moves it and records how long.

    Attributes:
        now: Seconds since the test began.
        slept: Every pause asked for, in order.
        timeouts: The deadline of every call a test's runner recorded with :meth:`note_timeout`.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []
        self.timeouts: list[float] = []

    def monotonic(self) -> float:
        """
        Returns:
            Seconds on the clock.
        """
        return self.now

    def sleep(self, seconds: float) -> None:
        """
        Pause: the clock moves and nothing waits.

        Args:
            seconds: How long the caller would have waited.
        """
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds: float) -> None:
        """
        Spend time as a call does that runs until its deadline, and remember its deadline.

        Args:
            seconds: How much to spend.
        """
        self.timeouts.append(seconds)
        self.now += seconds

    def elapsed(self) -> float:
        """
        Returns:
            Seconds since the test began.
        """
        return self.now
