# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Run the code that applies an operation, rehearsed, and keep what it would have done.

This is what makes a plan the operation's own and not a list written beside it (rule 3): the
function that updates an application is called with the same arguments it would be called with, and
what it asks of the runner and of the filesystem is noted instead of done. The rehearsal belongs to
the caller (:mod:`noust.core.rehearsal_scope`), so a console serving other requests can make a plan
while they act for real.

What the code under rehearsal reads is the machine as it is now, and its own writes are not there
to be read back. That is why a plan always says what it does not know, and why this module takes
no opinion on it: the operation's builder does, because only it knows what its code reads back.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Generic, TypeVar

from noust.core import rehearsal_scope
from noust.core.exceptions import NoustError, QueueFullError
from noust.core.plans.recorder import PlanFileSystem, PlanRunner
from noust.core.plans.shape import PlanCommand, PlanFile, PlanStep
from noust.core.runner import get_runner
from noust.core.secret_detection import mask_secret_shapes

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

#: How many plans are made at once. A plan looks at the machine for up to a minute, and a console has a
#: handful of threads for everything else: one more than this is told to ask again in a moment.
MAX_CONCURRENT_PLANS = 4

_slots = threading.BoundedSemaphore(MAX_CONCURRENT_PLANS)


class Phases:
    """
    The phases an operation announces while it runs, to attach to each the commands it issues.

    The code that applies an operation already says what it is doing (``on_phase``, ``on_step``:
    the progress its job shows). Handing :meth:`announce` to those callbacks gives a plan its steps
    from the operation itself and not from a list written beside it.
    """

    def __init__(self) -> None:
        self._marks: list[tuple[str, int]] = []

    def announce(self, title: str) -> None:
        """
        Say that a phase begins; the commands issued from now on belong to it.

        Args:
            title: What the operation calls it.
        """
        runner = get_runner()
        position = len(runner.commands) if isinstance(runner, PlanRunner) else 0
        self._marks.append((title, position))

    def phase(self, _index: int, _total: int, title: str) -> None:
        """
        An ``on_phase`` callback: ``(position, total, message)``.

        Args:
            _index: Its position.
            _total: How many phases there are.
            title: What the operation calls it.
        """
        self.announce(title)

    def apply(
        self, commands: tuple[PlanCommand, ...]
    ) -> tuple[tuple[PlanCommand, ...], tuple[PlanStep, ...]]:
        """
        Attach the commands to the phases they were issued in.

        Args:
            commands: What the rehearsal kept, in order.

        Returns:
            The commands with their step, and the steps that issued at least one.
        """
        steps: list[PlanStep] = []
        annotated = list(commands)
        for number, (title, start) in enumerate(self._marks):
            end = self._marks[number + 1][1] if number + 1 < len(self._marks) else len(commands)
            members = tuple(range(min(start, len(commands)), min(end, len(commands))))
            if not members:
                continue
            key = f"step-{len(steps) + 1}"
            steps.append(PlanStep(key, title, members))
            for position in members:
                annotated[position] = replace(annotated[position], step=key)
        return tuple(annotated), tuple(steps)


@dataclass(frozen=True)
class Rehearsal(Generic[T_co]):
    """
    What running an operation rehearsed left behind.

    Attributes:
        result: What the operation returned; None when it raised.
        error: The refusal the operation raised, when it did. A defect (anything that is not a
            :class:`~noust.core.exceptions.NoustError`) is not kept: it propagates.
        commands: The commands, in order, redacted, probes among them.
        files: The paths it would have changed.
        steps: The phases the operation announced, with the commands of each.
        asked_remote: A probe asked a repository or a registry.
        skipped_probes: Probes not asked because the plan had spent its time for looking.
        omitted: Commands and files left out for the plan's size.
    """

    result: T_co | None
    error: NoustError | None
    commands: tuple[PlanCommand, ...]
    files: tuple[PlanFile, ...]
    steps: tuple[PlanStep, ...]
    asked_remote: bool
    skipped_probes: int
    omitted: int


def rehearse(
    operation: Callable[[], T],
    *,
    scrub: Callable[[str], str] = mask_secret_shapes,
    now: Callable[[], float] = time.monotonic,
    phases: Phases | None = None,
) -> Rehearsal[T]:
    """
    Call an operation inside a rehearsal that is this context's alone.

    Args:
        operation: The code that applies the operation, as it would be called to apply it.
        scrub: What takes a secret out of a text: every command and every line of a file goes
            through it. Pass the one of the application the operation is about
            (:func:`noust.core.redact.scrubber_for`) when there is one.
        now: The clock that measures the plan's time for looking.
        phases: Where the operation announces its phases, when it has any to announce.

    Returns:
        What it returned or the refusal it raised, and what it would have run and changed.

    Raises:
        QueueFullError: :data:`MAX_CONCURRENT_PLANS` plans are being made already; nothing was run.
    """
    if not _slots.acquire(blocking=False):
        raise QueueFullError(
            "Too many plans are being made at once",
            details="Ask again in a moment.",
            limit=MAX_CONCURRENT_PLANS,
        )
    try:
        runner = PlanRunner(get_runner(), scrub=scrub, now=now)
        filesystem = PlanFileSystem(scrub=scrub)
        result: T | None = None
        error: NoustError | None = None
        with rehearsal_scope.scoped(runner=runner, fs=filesystem, on_check=runner.note_check):
            try:
                result = operation()
            except NoustError as exc:
                error = exc
    finally:
        _slots.release()
    files, omitted_files = filesystem.files()
    commands, steps = (
        phases.apply(tuple(runner.commands)) if phases is not None else (tuple(runner.commands), ())
    )
    return Rehearsal(
        result=result,
        error=error,
        commands=commands,
        files=files,
        steps=steps,
        asked_remote=runner.asked_remote,
        skipped_probes=runner.skipped_probes,
        omitted=runner.omitted + omitted_files,
    )
