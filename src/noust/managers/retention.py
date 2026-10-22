# Copyright (c) 2024-2026 Yago López Prado
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
from typing import Any, TypeVar

from noust.core.exceptions import ValidationError

Key = TypeVar("Key", bound=Hashable)

#: The largest retention by size a policy accepts: a petabyte.
MAX_RETENTION_GB = 1_000_000


def check_gigabytes(value: Any, field_name: str) -> float | None:
    """
    Check a retention by size, as an operator gives it.

    Args:
        value: What the caller gave, in gigabytes.
        field_name: The field, for the error.

    Returns:
        The value as a number, or None when the caller gave none.

    Raises:
        ValidationError: When it is not a number above zero and at most
            :data:`MAX_RETENTION_GB`.
    """
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0 < value <= MAX_RETENTION_GB
    ):
        raise ValidationError(
            f"{field_name} must be a number of gigabytes above 0 and at most {MAX_RETENTION_GB}",
            details="Leave it empty for no limit by size. The newest backup always stays, "
            "whatever its size.",
            field=field_name,
        )
    return float(value)


def gigabytes_to_bytes(gigabytes: float | None) -> int | None:
    """
    Turn a retention in gigabytes into the bytes a row stores.

    Args:
        gigabytes: The value :func:`check_gigabytes` returned.

    Returns:
        Bytes (a gigabyte is 2**30), or None for no limit.
    """
    return None if gigabytes is None else max(1, int(gigabytes * 1024**3))


def with_gigabytes(stored: dict[str, Any]) -> dict[str, Any]:
    """
    Show a stored retention by size in gigabytes, as the API and the CLI speak it.

    Args:
        stored: A policy as its row holds it (``retention_bytes``).

    Returns:
        The same with ``retention_gb`` in place of ``retention_bytes``.
    """
    kept = stored.pop("retention_bytes", None)
    stored["retention_gb"] = None if kept is None else round(kept / 1024**3, 3)
    return stored


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


def without_newest_good(
    expired: Sequence[Key], entries: Sequence[tuple[Key, datetime, bool]]
) -> list[Key]:
    """
    Spare, from what retention would delete, the newest copy that is not known to be bad.

    Retention keeps the newest entry. When that one failed its check (a dump taken but corrupt,
    kept as evidence) "the newest" is not a copy anybody can restore, and the rule that keeps
    it would let the good copies behind it be pushed out one by one. The newest good copy is
    spared as well.

    Args:
        expired: What :func:`select_expired` chose, newest first.
        entries: ``(key, moment, good)`` for every entry retention considered.

    Returns:
        ``expired`` without the newest good entry; unchanged when there is none.
    """

    def aware(moment: datetime) -> datetime:
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

    good = [(key, moment) for key, moment, ok in entries if ok]
    if not good:
        return list(expired)
    newest_good = max(good, key=lambda entry: aware(entry[1]))[0]
    return [key for key in expired if key != newest_good]
