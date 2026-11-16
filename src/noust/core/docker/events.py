# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Docker did lately: how many times it restarted each container in the last minutes.

A container's ``RestartCount`` is its whole life: one that restarted forty times last month is not
falling today. What says a container keeps falling is how often Docker restarted it in the last ten
minutes, and Docker keeps that: ``docker events`` over a window. The window is given as two timestamps, which
is the form every Docker that Noust supports reads.

**A restart is a death followed by a start.** Docker has an event called ``restart``, and asking for it
finds nothing of what matters: it is only emitted by ``docker restart``, which a person types. A container whose
restart policy brings it back leaves ``die`` and ``start`` and no ``restart`` (seen on Docker 20.10, 24.0.2 and 29),
so a loop that asked for restarts counted none. What is counted is the starts that follow a death.
"""

from __future__ import annotations

import json
import time

from noust.core.runner import CommandRunner

__all__ = ["LOOP_MINUTES", "recent_restarts"]

#: The window in which three restarts are a loop: what the monitor calls one too.
LOOP_MINUTES = 10

#: Seconds the question gets.
EVENTS_TIMEOUT = 30


def recent_restarts(
    runner: CommandRunner, *, minutes: int = LOOP_MINUTES, now: float | None = None
) -> dict[str, int]:
    """
    Count the restarts Docker made in a window that ends now.

    A restart is a ``start`` that comes after a ``die`` of the same container, whoever brought it back (its
    restart policy, or a person), so one is never counted twice; a container that died and is waiting out
    Docker's back-off has not restarted yet.

    Args:
        runner: Runs the question.
        minutes: How far back to look.
        now: The end of the window, epoch seconds; the clock's when None.

    Returns:
        For each container id with a restart in the window, how many. Empty when there were none or
        when Docker did not say: not knowing is not an error, and a panel that failed for it would
        fail on the machine that most needs it.
    """
    until = int(time.time() if now is None else now)
    since = until - minutes * 60
    result = runner.run(
        [
            "docker",
            "events",
            "--since",
            str(since),
            "--until",
            str(until),
            "--filter",
            "type=container",
            "--filter",
            "event=die",
            "--filter",
            "event=start",
            "--format",
            "{{json .}}",
        ],
        timeout=EVENTS_TIMEOUT,
    )
    if not result.success:
        return {}
    counts: dict[str, int] = {}
    fallen: set[str] = set()
    # Docker prints the events of a window in the order they happened.
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        actor = event.get("Actor")
        ident = actor.get("ID") if isinstance(actor, dict) else None
        action = event.get("Action") or event.get("status")
        if not isinstance(ident, str) or not ident:
            continue
        if action == "die":
            fallen.add(ident)
        elif action == "start" and ident in fallen:
            fallen.discard(ident)
            counts[ident] = counts.get(ident, 0) + 1
    return counts
