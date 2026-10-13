# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one retention rule for database dumps, local and remote.

"Keep the last N, nothing older than D days, and no more than G gigabytes" is
decided here and only here, as a pure function over what exists, so the local
directory and every destination apply exactly the same rule and a test can pin
it without a disk or a remote. What may be considered is the caller's decision: a schedule only
ever deletes dumps a schedule made (a dump taken by hand, or the safety copy
before a restore, is never in the list it passes).
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import TypeVar

Key = TypeVar("Key", bound=Hashable)


def select_expired(
    entries: Sequence[tuple[Key, datetime]],
    *,
    count: int | None,
    days: int | None,
    now: datetime | None = None,
    max_bytes: int | None = None,
    sizes: Mapping[Key, int] | None = None,
) -> list[Key]:
    """
    Choose what retention deletes.

    An entry goes when it is past the newest ``count``, or older than
    ``days`` days, or when the entries newer than it already add up to
    ``max_bytes``: any limit alone is enough. The newest entry always stays: a
    limit by age or size must never leave a database with no dump because its
    timer stopped or one dump is larger than the budget, and one entry is what
    "keep the last 1" means anyway.

    The size limit counts from the newest back, and an entry that does not fit
    ends the list: an older, smaller entry is not kept in its place, because
    "the last N gigabytes" is a window of time, not a pile of whatever is small.

    Args:
        entries: ``(key, moment)`` for every dump retention may delete, in any
            order. Naive moments are read as UTC.
        count: Dumps to keep, newest first; None for no limit by count.
        days: Days a dump is kept; None for no limit by age.
        now: The clock, injectable for tests.
        max_bytes: The most the kept entries may weigh together; None for no
            limit by size.
        sizes: Each key's size in bytes, for ``max_bytes``. An entry without
            one counts as zero.

    Returns:
        The keys to delete, newest first.
    """
    if not count and not days and not max_bytes:
        return []

    def aware(moment: datetime) -> datetime:
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    ordered = sorted(entries, key=lambda entry: aware(entry[1]), reverse=True)
    reference = aware(now) if now else datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=days) if days else None
    expired: list[Key] = []
    weight = 0
    over_budget = False
    for index, (key, moment) in enumerate(ordered):
        weight += (sizes or {}).get(key, 0)
        if index == 0:
            continue
        beyond_count = bool(count) and index >= (count or 0)
        beyond_age = cutoff is not None and aware(moment) < cutoff
        over_budget = over_budget or (bool(max_bytes) and weight > (max_bytes or 0))
        if beyond_count or beyond_age or over_budget:
            expired.append(key)
    return expired
