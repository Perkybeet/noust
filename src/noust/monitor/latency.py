# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How long requests took, kept as a histogram that can be added and read as percentiles.

A percentile cannot be added: the p95 of an hour and the p95 of the next are not the p95 of the
two. A histogram can: how many requests took up to 5 ms, up to 10 ms, up to 25 ms... Two hours
are the sum of their buckets, a day is the sum of its hours, and the p50 and the p95 of any
stretch are read from the sum by walking up the buckets until the wanted share of the requests
has been passed and interpolating inside the one that passed it.

The bounds are the ones web latency is read in (a fast page, a slow query, a timeout). Between two
bounds the reading is an estimate, never off by more than the width of its bucket; a request that
took longer than the last bound is only known to have taken longer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

#: The upper bounds of the buckets, in milliseconds. A request is counted in the first bucket
#: whose bound it does not pass; the last bucket holds the rest.
BOUNDS_MS: tuple[int, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)


def _empty() -> list[int]:
    """Returns: A bucket per bound, and one for what passes the last."""
    return [0] * (len(BOUNDS_MS) + 1)


@dataclass
class Histogram:
    """
    How long some of the answers took.

    Attributes:
        counts: Requests per bucket, in the order of :data:`BOUNDS_MS`, then the rest.
    """

    counts: list[int] = field(default_factory=_empty)

    @property
    def total(self) -> int:
        """Returns: How many requests were measured."""
        return sum(self.counts)

    def add_ms(self, milliseconds: float) -> None:
        """
        Count a request.

        Args:
            milliseconds: How long it took.
        """
        for index, bound in enumerate(BOUNDS_MS):
            if milliseconds <= bound:
                self.counts[index] += 1
                return
        self.counts[-1] += 1

    def merge(self, other: Histogram) -> None:
        """
        Add another histogram's requests to this one.

        Args:
            other: The requests to add.
        """
        for index, count in enumerate(other.counts):
            self.counts[index] += count

    def percentile(self, percent: float) -> float | None:
        """
        Estimate how long the slowest ``100 - percent`` of requests took at least.

        Args:
            percent: 50 for the median, 95 for the p95.

        Returns:
            Milliseconds, interpolated inside the bucket that holds the wanted request; None
            when nothing was measured. A request past the last bound reads as that bound.
        """
        total = self.total
        if total == 0:
            return None
        wanted = max(0.0, min(100.0, percent)) / 100.0 * total
        passed = 0
        for index, count in enumerate(self.counts):
            if count and passed + count >= wanted:
                lower = BOUNDS_MS[index - 1] if index > 0 else 0
                if index >= len(BOUNDS_MS):
                    return float(BOUNDS_MS[-1])
                upper = BOUNDS_MS[index]
                return lower + (wanted - passed) / count * (upper - lower)
            passed += count
        return float(BOUNDS_MS[-1])

    def to_text(self) -> str:
        """Returns: The counts as the JSON array the store keeps."""
        return json.dumps(self.counts, separators=(",", ":"))

    @classmethod
    def from_text(cls, text: str | None) -> Histogram:
        """
        Args:
            text: What :meth:`to_text` wrote, or None for a row written before there was one.

        Returns:
            The histogram; an empty one for anything that is not a list of the right counts.
        """
        if not text:
            return cls()
        try:
            counts = json.loads(text)
        except ValueError:
            return cls()
        if (
            not isinstance(counts, list)
            or len(counts) != len(BOUNDS_MS) + 1
            or not all(isinstance(count, int) and count >= 0 for count in counts)
        ):
            return cls()
        return cls(list(counts))
