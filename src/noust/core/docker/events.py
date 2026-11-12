# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What Docker did lately: how many times it restarted each container in the last minutes.

A container's ``RestartCount`` is its whole life: one that restarted forty times last month is not
falling today. What says a container keeps falling is how often Docker restarted it in the last ten
minutes, and Docker keeps that: ``docker events`` over a window, asked for restarts only. The window is
given as two timestamps, which is the form every Docker that Noust supports reads.
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
            "event=restart",
            "--format",
            "{{json .}}",
        ],
        timeout=EVENTS_TIMEOUT,
    )
    if not result.success:
        return {}
    counts: dict[str, int] = {}
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        actor = event.get("Actor") if isinstance(event, dict) else None
        ident = actor.get("ID") if isinstance(actor, dict) else None
        if isinstance(ident, str) and ident:
            counts[ident] = counts.get(ident, 0) + 1
    return counts
