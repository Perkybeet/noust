# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which occurrences of a scheduled task ran, and which did not (checklist B-66).

A timer with ``Persistent=true`` runs a job that was due while the machine was off once, at boot:
systemd recovers it, but says nothing about the three that were due and are gone. The ledger sets
the occurrences a schedule had in a stretch against the runs the journal saw, and gives each one
of three verdicts:

- ``ran``: a run started within :data:`GRACE_SECONDS` of the moment it was due;
- ``late``: a run started later, before the next occurrence was due (the catch-up at boot is one);
- ``missed``: no run started before the next occurrence. When the machine was off at that moment
  the reason is ``server_off``, from the gaps between boots the journal keeps; otherwise it is
  left unexplained, because the ledger does not guess (the timer was stopped, the job was still
  running, a failed start).

The functions here are pure: the schedule's occurrences, the runs and the boots are given. Reading
them is :meth:`noust.managers.cron_manager.CronManager.ledger`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

#: How long after the moment a job was due a run still counts as on time.
GRACE_SECONDS = 120.0

RAN = "ran"
LATE = "late"
MISSED = "missed"

#: Why an occurrence did not run, when the journal can say.
REASON_SERVER_OFF = "server_off"


@dataclass(frozen=True)
class Occurrence:
    """
    One moment a job was due and what became of it.

    Attributes:
        at: When it was due, epoch seconds.
        status: ``ran``, ``late`` or ``missed``.
        ran_at: When the run that answers for it started, for ``ran`` and ``late``.
        reason: ``server_off`` when the machine was down at that moment; empty otherwise.
    """

    at: float
    status: str
    ran_at: float | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The occurrence as JSON-ready data.
        """
        return {"at": self.at, "status": self.status, "ran_at": self.ran_at, "reason": self.reason}


def boot_gaps(boots: Sequence[dict[str, Any]]) -> list[tuple[float, float]]:
    """
    Name the stretches the machine was down, from the boots the journal remembers.

    Args:
        boots: ``journalctl --list-boots -o json``: each with ``first_entry`` and ``last_entry``
            in microseconds, oldest first.

    Returns:
        ``(went_down, came_up)`` in epoch seconds for each stretch between two boots.
    """
    spans: list[tuple[float, float]] = []
    for entry in boots:
        try:
            spans.append((float(entry["first_entry"]) / 1e6, float(entry["last_entry"]) / 1e6))
        except (KeyError, TypeError, ValueError):
            continue
    spans.sort()
    return [(earlier[1], later[0]) for earlier, later in pairwise(spans)]


def coverage_start(boots: Sequence[dict[str, Any]]) -> float | None:
    """
    Say how far back the journal reaches, which is as far back as anything can be judged.

    Args:
        boots: As for :func:`boot_gaps`.

    Returns:
        The first moment of the oldest boot it keeps, epoch seconds; None when it keeps none.
    """
    firsts = []
    for entry in boots:
        try:
            firsts.append(float(entry["first_entry"]) / 1e6)
        except (KeyError, TypeError, ValueError):
            continue
    return min(firsts) if firsts else None


def judge(
    due: Sequence[float],
    runs: Sequence[float],
    gaps: Sequence[tuple[float, float]] = (),
    *,
    now: float,
    grace: float = GRACE_SECONDS,
) -> list[Occurrence]:
    """
    Set the moments a job was due against the runs it had.

    Args:
        due: When it was due, epoch seconds, oldest first.
        runs: When each run started, epoch seconds.
        gaps: The stretches the machine was down (see :func:`boot_gaps`).
        now: The present. An occurrence still inside its grace period is not judged: it may yet run.
        grace: How late a run may start and still be on time.

    Returns:
        One verdict per occurrence that can be judged, in the order given.
    """
    starts = sorted(runs)
    ordered = sorted(due)
    found: list[Occurrence] = []
    for index, moment in enumerate(ordered):
        if moment + grace > now:
            continue
        following = ordered[index + 1] if index + 1 < len(ordered) else float("inf")
        on_time = next((run for run in starts if moment <= run < moment + grace), None)
        if on_time is not None:
            found.append(Occurrence(moment, RAN, on_time))
            continue
        later = next((run for run in starts if moment + grace <= run < following), None)
        if later is not None:
            found.append(Occurrence(moment, LATE, later))
            continue
        down = any(went <= moment <= came for went, came in gaps)
        found.append(Occurrence(moment, MISSED, None, REASON_SERVER_OFF if down else ""))
    return found
