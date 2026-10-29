# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What state a systemd unit is in, in the words every view of Noust uses.

``systemctl`` and ``list-units`` give two columns, ``ACTIVE`` and ``SUB``. Noust's views need
fewer words than systemd has: the Services page and ``noust service list`` filter by
:func:`state_group`, the top bar's tally and the Overview's attention list read the same two
columns through :func:`timer_phase`, and a view that decided any of it for itself would
disagree with the others (the top bar once said "13 stopped" of a server whose eight backups
were only waiting for their timers). This module is the one place that decides, below
:mod:`noust.managers.service_manager`, which puts it on its manager as static methods.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["has_timer", "state_group", "state_is_running", "timer_phase"]


def state_is_running(raw_state: str) -> bool:
    """
    Decide whether a systemd active-state string means "running".

    ``systemctl is-active`` and the ACTIVE column ``systemctl list-units`` prints share one
    vocabulary (``active``, ``inactive``, ``failed``, ``activating``, ``deactivating``,
    ``reloading``), and exactly one value of it means the unit is up. This is the one place that
    decides that, so ``get_status`` (which the web API reads) and a caller of ``list_services``
    (``noust service list``) cannot end up answering "is it running" two different ways.

    Args:
        raw_state: The raw string systemd reported.

    Returns:
        True when the state means the unit is running.
    """
    return raw_state.strip() == "active"


def has_timer(directory: Path, name: str) -> bool:
    """
    Say whether a timer starts a unit.

    The one definition of a scheduled service (backups, cron jobs, scheduled updates and the
    cleaning of the disk are all written this way): a ``<name>.timer`` stands beside
    ``<name>.service`` in the directory Noust writes units to.

    Args:
        directory: Where Noust writes units.
        name: Unit name without the ``.service`` suffix.

    Returns:
        True when the unit has a timer file.
    """
    return (directory / f"{name}.timer").is_file()


def timer_phase(active: str, sub: str, scheduled: bool) -> str | None:
    """
    Say where a service a timer starts is in its cycle.

    A one-shot unit is ``inactive`` between runs, ``activating`` while one runs and ``failed``
    after one that did not end well. Only the first is a service waiting for its timer; the
    others are what any unit in that state is.

    Args:
        active: systemd's ACTIVE column (``ActiveState``).
        sub: systemd's SUB column (``SubState``).
        scheduled: Whether a timer starts the unit (:func:`has_timer`).

    Returns:
        ``"waiting"`` between two runs, ``"running"`` while a run is in progress, and None for a
        unit no timer starts or one that failed or is looping, which the usual rules judge.
    """
    if not scheduled:
        return None
    active, sub = active.strip().lower(), sub.strip().lower()
    if active == "failed" or sub in ("failed", "auto-restart"):
        return None
    if active in ("active", "activating", "reloading"):
        return "running"
    return "waiting"


def state_group(active: str, sub: str, *, scheduled: bool = False) -> str:
    """
    Sort a unit into the group a services listing filters by.

    ``failed`` is every unit that is not cleanly up or cleanly down: systemd's own ``failed`` and
    a unit it is still restarting (``activating``, or ``auto-restart`` between two attempts),
    which is the crash loop that has not yet settled into ``failed``. The console's Services page
    decides the same way from the same two columns, so a filter here and a filter there keep the
    same units. A service a timer starts is ``scheduled`` between two runs, not ``stopped``: it is
    waiting, and nothing is wrong.

    Args:
        active: systemd's ACTIVE column (``ActiveState``).
        sub: systemd's SUB column (``SubState``).
        scheduled: Whether a timer starts the unit.

    Returns:
        ``failed``, ``running``, ``stopped`` or ``scheduled``.
    """
    phase = timer_phase(active, sub, scheduled)
    if phase is not None:
        return "scheduled" if phase == "waiting" else phase
    active = active.strip().lower()
    if sub.strip().lower() == "auto-restart" or active in ("activating", "failed"):
        return "failed"
    return "running" if state_is_running(active) else "stopped"
