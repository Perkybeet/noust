# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Saying a quantity the way a person reads it: a duration, a size, a date.

Short, and the same in every language where a unit is: ``1 min 12 s``,
``18.3 GB``. Only month names are translated (:data:`noust.core.messages.MONTHS`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from noust.core.messages import MONTHS, Locale


def format_duration(seconds: float) -> str:
    """
    Say how long something took, short and the same in every language.

    Args:
        seconds: The duration.

    Returns:
        ``42 s``, ``1 min 12 s``, ``2 min`` or ``2 h 5 min``.
    """
    total = max(round(seconds), 0)
    if total < 60:
        return f"{total} s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs} s" if secs else f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def format_bytes(size: int) -> str:
    """
    Say a size in bytes the way ``df -h`` does, one decimal.

    Args:
        size: A number of bytes.

    Returns:
        ``900 B``, ``1.5 KB``, ``5.0 MB``, ``18.3 GB``; anything above stays
        in GB.
    """
    if size < 1024:
        return f"{size} B"
    value = float(size)
    for unit in ("KB", "MB", "GB"):
        value /= 1024
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
    raise AssertionError("unreachable")  # pragma: no cover


def format_date(value: str, locale: Locale) -> str:
    """
    Spell out a calendar date.

    Args:
        value: An ISO date, ``2026-10-06``.
        locale: The reader's language.

    Returns:
        ``6 Oct 2026`` or ``6 oct 2026``; a value that is not a date is
        returned as it came.
    """
    try:
        parsed = date.fromisoformat(value[:10])
    except ValueError:
        return value
    return f"{parsed.day} {MONTHS[locale][parsed.month - 1]} {parsed.year}"


def format_utc(moment: datetime) -> str:
    """
    Spell out a moment in UTC, to the minute.

    Args:
        moment: A timezone-aware (or naive UTC) datetime.

    Returns:
        ``2026-09-29 10:45 UTC``.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return f"{moment:%Y-%m-%d %H:%M} UTC"
