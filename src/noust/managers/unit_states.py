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

from noust.core import paths

__all__ = [
    "announces_its_own_failure",
    "has_timer",
    "state_group",
    "state_is_running",
    "timer_phase",
]

#: The units of a backup timer that run without saying how they ended: the retention sweep, the pass that
#: sends again what was not sent (when it fails, it is that it could not be made at all), and the
#: archiving of a write-ahead log (``noust-backup-pitr-<engine>``). Every other unit of a backup timer
#: (an application's, its data's, a database's, Noust's own) runs a command that announces its own
#: failure as ``backup_failed``; a scheduled task's is announced by ``cron_failed``.
_SILENT_BACKUP_UNITS = ("sweep", "resend", "pitr-")

#: The systemd results of a run that ended by its own exit: the command ran, saw it had failed and
#: said so before it went. Every other result (its time limit, the memory limit, a signal, a
#: watchdog) is systemd's doing, and a run killed that way announced nothing.
_ENDED_BY_ITSELF = ("exit-code",)


def announces_its_own_failure(unit: str, result: str | None = None) -> bool:
    """
    Say whether a unit's failure is already said by the command it runs (item 201).

    The one definition: the monitor watches every unit Noust manages and said a failure again as
    ``unit_failed`` when the run had said it as ``backup_failed`` or ``cron_failed``, so nine failed
    sends were eighteen messages. The closed list is the runs of the backup timers that announce
    (every one but :data:`_SILENT_BACKUP_UNITS`) and of the scheduled tasks. A unit that is not one
    of them is never listed here, so a new kind of unit is announced by the monitor until somebody
    makes it say its own.

    Args:
        unit: The unit's name, with or without ``.service``.
        result: systemd's ``Result`` for its last run, when the question is about that run. A run
            that ended by something other than its own exit (:data:`_ENDED_BY_ITSELF`) said nothing,
            so it is not said to have. None asks only about the unit.

    Returns:
        True when the failure of the unit's run is the command's to announce.
    """
    name = unit.removesuffix(".service")
    announces = False
    for prefix in (paths.BACKUP_UNIT_PREFIX, paths.LEGACY_BACKUP_UNIT_PREFIX):
        if name.startswith(prefix):
            rest = name.removeprefix(prefix)
            announces = not rest.startswith(_SILENT_BACKUP_UNITS)
    if name.startswith((paths.CRON_UNIT_PREFIX, paths.LEGACY_CRON_UNIT_PREFIX)):
        announces = True
    if not announces:
        return False
    return result is None or result in _ENDED_BY_ITSELF


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
