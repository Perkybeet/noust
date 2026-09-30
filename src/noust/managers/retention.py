# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The one retention rule for database dumps, local and remote.

"Keep the last N, and nothing older than D days" is decided here and only
here, as a pure function over what exists, so the local directory and every
destination apply exactly the same rule and a test can pin it without a disk
or a remote. What may be considered is the caller's decision: a schedule only
ever deletes dumps a schedule made (a dump taken by hand, or the safety copy
before a restore, is never in the list it passes).
"""

from __future__ import annotations

from collections.abc import Hashable, Sequence
from datetime import datetime, timedelta, timezone
from typing import TypeVar

Key = TypeVar("Key", bound=Hashable)


def select_expired(
    entries: Sequence[tuple[Key, datetime]],
    *,
    count: int | None,
    days: int | None,
    now: datetime | None = None,
) -> list[Key]:
    """
    Choose what retention deletes.

    An entry goes when it is past the newest ``count``, or older than
    ``days`` days: either limit alone is enough. The newest entry always
    stays: a limit by age must never leave a database with no dump because
    its timer stopped, and one entry is what "keep the last 1" means anyway.

    Args:
        entries: ``(key, moment)`` for every dump retention may delete, in any
            order. Naive moments are read as UTC.
        count: Dumps to keep, newest first; None for no limit by count.
        days: Days a dump is kept; None for no limit by age.
        now: The clock, injectable for tests.

    Returns:
        The keys to delete, newest first.
    """
    if not count and not days:
        return []

    def aware(moment: datetime) -> datetime:
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    ordered = sorted(entries, key=lambda entry: aware(entry[1]), reverse=True)
    reference = aware(now) if now else datetime.now(timezone.utc)
    cutoff = reference - timedelta(days=days) if days else None
    expired: list[Key] = []
    for index, (key, moment) in enumerate(ordered):
        if index == 0:
            continue
        beyond_count = bool(count) and index >= (count or 0)
        beyond_age = cutoff is not None and aware(moment) < cutoff
        if beyond_count or beyond_age:
            expired.append(key)
    return expired
