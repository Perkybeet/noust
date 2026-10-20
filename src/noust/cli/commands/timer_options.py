# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The two options every backup schedule takes beyond its calendar: a time limit and a time zone.

One definition of the flags and of the lines that show them, shared by the four commands that
set a schedule (``noust db backup-schedule set``, ``noust backup schedule create``,
``noust backup volumes schedule set`` and ``noust self-backup schedule set``), so they spell and
word them alike. The rules are :mod:`noust.managers.backup_timer_options`'s.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, TypeVar

import click

__all__ = ["describe_timer_options", "timer_options"]

F = TypeVar("F", bound=Callable[..., Any])


def timer_options(function: F) -> F:
    """
    Add ``--max-runtime`` and ``--timezone`` to a command.

    Args:
        function: The command's function; it receives ``max_runtime`` and ``zone``.

    Returns:
        The function, decorated.
    """
    function = click.option(
        "--timezone",
        "zone",
        metavar="ZONE",
        help="Read the schedule in this time zone, such as Europe/Madrid "
        "(timedatectl list-timezones lists them). The server's clock by default.",
    )(function)
    function = click.option(
        "--max-runtime",
        "max_runtime",
        metavar="DURATION",
        help="Stop a run that takes longer than this: 90m, 2h, 1d or a number of seconds "
        "(1 minute to 7 days). No limit by default.",
    )(function)
    return function


def describe_timer_options(timer: Mapping[str, Any]) -> list[str]:
    """
    Say a schedule's time limit and time zone, for the lines of ``show``.

    Args:
        timer: The timer's state, as the managers return it (``max_runtime_seconds``,
            ``timezone``).

    Returns:
        The lines; empty when neither is set.
    """
    lines = []
    seconds = timer.get("max_runtime_seconds")
    if seconds:
        hours, rest = divmod(int(seconds), 3600)
        minutes, secs = divmod(rest, 60)
        parts = [
            f"{value}{unit}" for value, unit in ((hours, "h"), (minutes, "m"), (secs, "s")) if value
        ]
        lines.append(f"  Time limit:   {' '.join(parts)} (a run past it is stopped)")
    if timer.get("timezone"):
        lines.append(f"  Time zone:    {timer['timezone']}")
    return lines
