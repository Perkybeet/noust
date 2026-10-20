# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A backup schedule's time limit and time zone.

A dump that hangs held the unit of its timer for as long as it liked, and every schedule ran on the
server's clock whatever the operator's day looked like. Two options per timer fix both:

* **a time limit**, from one minute to a week, which becomes ``TimeoutStartSec=`` in the one service
  the schedule runs in: systemd stops what runs past it, the unit fails, and the failure is the one
  the monitor already announces. It is not ``RuntimeMaxSec=``: every backup service is
  ``Type=oneshot``, which stays "activating" while it runs, and systemd ignores ``RuntimeMaxSec=``
  there ("has no effect in combination with Type=oneshot", which ``systemd-analyze verify`` says and
  the ``unit_files`` scenario of ``tests/integration/backups_run.py`` checks) while
  ``TimeoutStartSec=`` bounds the whole run;
* **a time zone**, which becomes a suffix of ``OnCalendar=`` (``*-*-* 02:00:00 Europe/Madrid``).

Both are validated here before they reach a root-owned unit. A zone is checked against the server's
own zone database rather than by loosening the characters a calendar may hold: ``Buenos_Aires`` has
an underscore and ``Etc/GMT+1`` a plus, and a pattern that lets those through lets a directive
through.

They are kept in a table of their own, keyed by the timer's unit name, so no full-row write of a
policy can put an old value back, and :meth:`~noust.managers.backup_scheduler.BackupScheduler`
applies them in the one place units are written, so they follow the unit through every rewrite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore, get_store

__all__ = [
    "MAX_RUNTIME_SECONDS",
    "MIN_RUNTIME_SECONDS",
    "TimerOptions",
    "TimerOptionsStore",
    "parse_duration",
    "validate_options",
    "with_options",
]

#: Where a server keeps its zone database.
ZONEINFO_DIRS: tuple[Path, ...] = (
    Path("/usr/share/zoneinfo"),
    Path("/usr/lib/zoneinfo"),
    Path("/usr/share/lib/zoneinfo"),
)

#: The shortest and longest time limit a schedule accepts.
MIN_RUNTIME_SECONDS = 60
MAX_RUNTIME_SECONDS = 7 * 86400

#: What a zone name looks like in the database: segments of letters, digits, ``_``, ``+`` and ``-``.
_ZONE = re.compile(r"[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)*")

#: A zone at the end of a calendar: ``UTC`` or anything with a slash.
_TRAILING_ZONE = re.compile(r"\s+(?:UTC|[A-Za-z][A-Za-z0-9_+\-]*(?:/[A-Za-z0-9_+\-]+)+)\s*$")

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

#: The markers priority's block is fenced by; the time limit lives inside it.
_BLOCK_END = "# /noust-priority"
_LIMIT_PREFIX = "TimeoutStartSec="


@dataclass(frozen=True)
class TimerOptions:
    """
    What a timer's service and calendar are given besides the policy.

    Attributes:
        max_runtime_seconds: The longest the service may run; None for no limit.
        timezone: The zone the calendar is read in; None for the server's clock.
    """

    max_runtime_seconds: int | None = None
    timezone: str | None = None

    @property
    def empty(self) -> bool:
        """Whether neither option is set."""
        return self.max_runtime_seconds is None and self.timezone is None

    def to_dict(self) -> dict[str, int | str | None]:
        """
        Returns:
            The options as plain data.
        """
        return {"max_runtime_seconds": self.max_runtime_seconds, "timezone": self.timezone}


def _in_range(seconds: int) -> int:
    """
    Args:
        seconds: A time limit.

    Returns:
        It, when it is within the limits.

    Raises:
        ValidationError: When it is shorter than a minute or longer than a week.
    """
    if not MIN_RUNTIME_SECONDS <= seconds <= MAX_RUNTIME_SECONDS:
        raise ValidationError(
            "A time limit is from 1 minute to 7 days",
            details=f"Got {seconds} seconds. Use 90m, 2h or 3600; leave it empty for no limit.",
            field="max_runtime",
        )
    return seconds


def parse_duration(text: str) -> int:
    """
    Read a time limit as an operator types it.

    Args:
        text: Seconds (``3600``) or a number with ``s``, ``m``, ``h`` or ``d`` (``90m``, ``2h``).

    Returns:
        Seconds.

    Raises:
        ValidationError: When it is not a whole number with at most one such unit, or is
            outside 1 minute to 7 days.
    """
    match = re.fullmatch(r"\s*(\d+)\s*([smhdSMHD]?)\s*", text)
    if match is None:
        raise ValidationError(
            f"{text!r} is not a time limit",
            details="Use a whole number of seconds, or a number with s, m, h or d: 90m, 2h, 1d.",
            field="max_runtime",
        )
    return _in_range(int(match.group(1)) * _UNITS[(match.group(2) or "s").lower()])


def _known_zone(name: str) -> bool:
    """
    Args:
        name: A zone name that already matches the pattern.

    Returns:
        Whether this server's zone database has it (``UTC`` always).
    """
    if name == "UTC":
        return True
    for root in ZONEINFO_DIRS:
        candidate = root / name
        try:
            if candidate.is_file() and candidate.resolve().is_relative_to(root.resolve()):
                return True
        except OSError:
            continue
    return False


def validate_options(max_runtime: int | str | None, zone: str | None) -> TimerOptions:
    """
    Check what an operator asked for.

    Args:
        max_runtime: A time limit (seconds, or ``90m``/``2h``/``1d``); None or empty for none.
        zone: A zone of the server's database (``Europe/Madrid``); None or empty for the
            server's clock.

    Returns:
        The options.

    Raises:
        ValidationError: A limit is out of range or unreadable, or the zone is not one this
            server knows.
    """
    seconds: int | None
    if max_runtime is None or (isinstance(max_runtime, str) and not max_runtime.strip()):
        seconds = None
    elif isinstance(max_runtime, bool):
        raise ValidationError("A time limit is a number", field="max_runtime")
    elif isinstance(max_runtime, int):
        seconds = _in_range(max_runtime)
    else:
        seconds = parse_duration(max_runtime)
    name = (zone or "").strip()
    if not name:
        return TimerOptions(seconds, None)
    if not _ZONE.fullmatch(name) or len(name) > 64 or not _known_zone(name):
        raise ValidationError(
            f"{name!r} is not a time zone this server knows",
            details="Use a name of the zone database, such as Europe/Madrid, "
            "America/Argentina/Buenos_Aires or UTC (timedatectl list-timezones lists them).",
            field="timezone",
        )
    return TimerOptions(seconds, name)


def with_options(text: str, options: TimerOptions, *, kind: Literal["service", "timer"]) -> str:
    """
    Put a timer's options into the text of its unit.

    A service gets ``TimeoutStartSec=`` inside the block priority fences (and loses it when there is
    no limit); a timer gets the zone after its calendar, replacing one already there. A timer with
    no zone asked is left alone: a calendar that names its own zone keeps it.

    Args:
        text: A unit file.
        options: What to put in.
        kind: ``service`` or ``timer``.

    Returns:
        The text.
    """
    if kind == "timer":
        if options.timezone is None:
            return text
        lines = text.split("\n")
        for index, line in enumerate(lines):
            if line.startswith("OnCalendar="):
                calendar = _TRAILING_ZONE.sub("", line)
                lines[index] = f"{calendar} {options.timezone}"
        return "\n".join(lines)
    lines = [line for line in text.split("\n") if not line.startswith(_LIMIT_PREFIX)]
    if options.max_runtime_seconds is not None and _BLOCK_END in lines:
        at = lines.index(_BLOCK_END)
        lines.insert(at, f"{_LIMIT_PREFIX}{options.max_runtime_seconds}")
    return "\n".join(lines)


class TimerOptionsStore:
    """
    The options of every timer, one row each, in the store's own connection.

    Args:
        store: The store; the process-wide one when None.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            self._store = get_store()
        return self._store

    def get(self, unit: str) -> TimerOptions:
        """
        Read a timer's options.

        Args:
            unit: The unit name without its suffix.

        Returns:
            The options; empty when none were set.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT max_runtime_seconds, timezone FROM backup_timer_options WHERE unit = ?",
                (unit,),
            )
            row = cursor.fetchone()
        return TimerOptions(row["max_runtime_seconds"], row["timezone"]) if row else TimerOptions()

    def all(self) -> dict[str, TimerOptions]:
        """
        Returns:
            Every timer that has options, by unit name.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT unit, max_runtime_seconds, timezone FROM backup_timer_options ORDER BY unit"
            )
            return {
                row["unit"]: TimerOptions(row["max_runtime_seconds"], row["timezone"])
                for row in cursor.fetchall()
            }

    def set(self, unit: str, options: TimerOptions) -> None:
        """
        Replace a timer's options as a whole; empty options forget the row.

        Args:
            unit: The unit name without its suffix.
            options: What the timer is to have.
        """
        if options.empty:
            self.delete(unit)
            return
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO backup_timer_options (unit, max_runtime_seconds, timezone, "
                "updated_at) VALUES (?, ?, ?, ?) ON CONFLICT(unit) DO UPDATE SET "
                "max_runtime_seconds = excluded.max_runtime_seconds, "
                "timezone = excluded.timezone, updated_at = excluded.updated_at",
                (unit, options.max_runtime_seconds, options.timezone, stamp),
            )

    def delete(self, unit: str) -> None:
        """
        Forget a timer's options.

        Args:
            unit: The unit name without its suffix.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM backup_timer_options WHERE unit = ?", (unit,))
