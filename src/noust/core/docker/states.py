# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The state of a container as one word of a closed list, decided in one place.

Docker says a container is ``running`` or ``exited``, and the sentence beside it (``Exited (137) 3 days
ago``) carries the rest. What an operator needs is what it means: a container that keeps falling, one
that ran out of memory, one that somebody stopped, one that did its job and ended. A stack, the summary
that says what asks for attention and the page of one container must say the same of the same
container, so the words are decided here (rule 3) and the screens translate them.

====================  ==========================================  =========
kind                  what it is                                  problem
====================  ==========================================  =========
``running``           it runs (a health check that has not        no
                      failed yet is not a problem)
``unhealthy``         it runs and its health check fails          yes
``loop``              Docker keeps restarting it                  yes
``oom``               it was killed for lack of memory            yes
``killed``            it ended with 137: killed, not by memory    yes
``error``             it ended with a failure of its own          yes
``dead``              Docker could not remove it                  yes
``terminated``        it ended with 143: somebody stopped it      no
``ended``             it ended with 0                             no
``stopped``           it is not running and Docker did not say    no
                      how it ended
``paused``            somebody paused it                          no
``created``           it was made and never started               no
====================  ==========================================  =========
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from noust.core.docker.models import Container

__all__ = [
    "KINDS",
    "StateInfo",
    "classify",
    "classify_container",
    "exit_code_of",
    "is_job",
    "stack_state",
]

#: Every word a state can be.
KINDS = (
    "running",
    "unhealthy",
    "loop",
    "oom",
    "killed",
    "error",
    "dead",
    "terminated",
    "ended",
    "stopped",
    "paused",
    "created",
)

#: The sentence Docker prints for a container that ended: ``Exited (137) 3 days ago``.
_EXITED = re.compile(r"(?:Exited|Restarting) \((-?\d+)\)")

#: What ``docker stop`` leaves: the process was asked to end (SIGTERM, 128 + 15) and did.
EXIT_TERMINATED = 143

#: What a kill leaves (SIGKILL, 128 + 9): Docker's, the kernel's when memory ran out, or a person's.
EXIT_KILLED = 137


@dataclass(frozen=True)
class StateInfo:
    """
    A container's state, as one word and whether it asks for the operator.

    Attributes:
        kind: One of :data:`KINDS`.
        problem: Whether it is something to look at.
        exit_code: How its last run ended, when Docker said.
    """

    kind: str
    problem: bool
    exit_code: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            ``kind``, ``problem`` and ``exit_code``.
        """
        return {"kind": self.kind, "problem": self.problem, "exit_code": self.exit_code}


def exit_code_of(status: str) -> int | None:
    """
    Read how a container ended out of the sentence Docker prints for it.

    Args:
        status: ``Exited (137) 3 days ago`` or ``Restarting (1) 5 seconds ago``.

    Returns:
        The exit code, or None when the sentence has none (a container that runs, or was created).
    """
    found = _EXITED.search(status or "")
    return int(found.group(1)) if found else None


def classify(
    *, state: str, health: str = "", exit_code: int | None = None, oom: bool = False
) -> StateInfo:
    """
    Decide a container's state.

    Args:
        state: Docker's own word (``running``, ``exited``, ``restarting``, ``paused``, ``created``,
            ``dead``).
        health: ``healthy``, ``unhealthy``, ``starting`` or empty.
        exit_code: How its last run ended, when known.
        oom: Whether memory ran out in its last run (only ``inspect`` says).

    Returns:
        The state.
    """
    if state == "restarting":
        return StateInfo("loop", True, exit_code)
    if state == "running":
        if health == "unhealthy":
            return StateInfo("unhealthy", True)
        return StateInfo("running", False)
    if state == "paused":
        return StateInfo("paused", False)
    if state == "created":
        return StateInfo("created", False)
    if state == "dead":
        return StateInfo("dead", True, exit_code)
    if oom:
        return StateInfo("oom", True, exit_code)
    if exit_code is None:
        return StateInfo("stopped", False)
    if exit_code == 0:
        return StateInfo("ended", False, 0)
    if exit_code == EXIT_TERMINATED:
        return StateInfo("terminated", False, exit_code)
    if exit_code == EXIT_KILLED:
        return StateInfo("killed", True, exit_code)
    return StateInfo("error", True, exit_code)


def classify_container(
    container: Container, *, exit_code: int | None = None, oom: bool = False
) -> StateInfo:
    """
    Decide a listed container's state.

    Args:
        container: A container, as the listing has it.
        exit_code: How it ended, when ``inspect`` said; the sentence of the listing otherwise.
        oom: Whether memory ran out, when ``inspect`` said.

    Returns:
        The state.
    """
    code = exit_code if exit_code is not None else exit_code_of(container.status)
    return classify(state=container.state, health=container.health, exit_code=code, oom=oom)


def is_job(*, kind: str, policy: str) -> bool:
    """
    Tell a job that finished from a service that stopped.

    Args:
        kind: The container's state kind.
        policy: Its restart policy (``no``, ``always``, ``unless-stopped``, ``on-failure``).

    Returns:
        True for one that ended well and was never meant to be started again: its work is done and a
        stack that holds it is not failing.
    """
    return kind == "ended" and policy == "no"


def stack_state(members: Sequence[tuple[StateInfo, str]]) -> tuple[str, int, int]:
    """
    Decide the state of a stack out of its containers.

    A stack is its containers, and a worker is whatever is not a job that did its work: a setup step
    that ended well is not a failure, and a stack that holds one is not "exited".

    Args:
        members: Each container's state and restart policy.

    Returns:
        ``(state, running, workers)``: ``failed`` when a worker keeps falling or ended badly,
        ``stopped`` when none of them runs, ``warning`` when some are down or one is unhealthy,
        else ``running``; how many workers run; and how many there are.
    """
    workers = [state for state, policy in members if not is_job(kind=state.kind, policy=policy)]
    running = sum(1 for state in workers if state.kind in ("running", "unhealthy"))
    if any(state.problem and state.kind != "unhealthy" for state in workers):
        return "failed", running, len(workers)
    if not workers or running == 0:
        return "stopped", running, len(workers)
    if running < len(workers) or any(state.kind == "unhealthy" for state in workers):
        return "warning", running, len(workers)
    return "running", running, len(workers)
