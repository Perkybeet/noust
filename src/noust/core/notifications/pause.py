# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The pause of notifications: ``notifications.muted_until``.

Turning every notification off is almost always "for a while" (a maintenance window, a night's
sleep), and a switch left off is how an operator misses the next real incident. The console asks
"until when" when it is turned off and records the answer here: a moment, with its time zone,
before which the notifier sends nothing, and after which it sends again with nothing to
remember. ``notifications.enabled`` stays the master switch, for "until I turn it on".

One reading of that moment, so the notifier that judges it and the configuration that accepts it
cannot disagree on what a value means.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import ConfigError

KEY = "notifications.muted_until"


def parse_pause(value: object) -> datetime | None:
    """
    Read a stored pause as a moment.

    Args:
        value: What ``notifications.muted_until`` holds: empty for no pause, or an ISO 8601
            moment with its time zone (``Z`` is accepted as UTC).

    Returns:
        The moment in UTC, or None for no pause and for anything that is not a moment with a
        zone: a value nobody can read pauses nothing, so a typo in the file never silences a
        server for good.
    """
    if not isinstance(value, str) or value.strip() == "":
        return None
    text = value.strip()
    # Python 3.10's fromisoformat does not know "Z", which is what a browser writes.
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(timezone.utc)


def is_paused(value: object, now: datetime) -> bool:
    """
    Whether a stored pause covers a moment.

    Args:
        value: What ``notifications.muted_until`` holds.
        now: The moment asked about, with its time zone.

    Returns:
        True while the pause has not ended.
    """
    until = parse_pause(value)
    return until is not None and now < until


def validate_pause(value: Any) -> str:
    """
    Accept a pause for ``Config.set``: no pause, or a moment with its time zone.

    Args:
        value: What the caller wants stored.

    Returns:
        An empty string, or the moment in UTC as ISO 8601.

    Raises:
        ConfigError: For text that is not a moment, or one with no time zone (it would mean a
            different instant on every machine that read it).
    """
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return ""
    moment = parse_pause(value)
    if moment is None:
        raise ConfigError(
            f"{KEY} must be a moment with its time zone",
            details=f"Got {value!r}. Write it like 2026-11-06T09:00:00+01:00, or leave it empty for no pause.",
        )
    return moment.isoformat()
