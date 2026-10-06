# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
One command, run once, in an application's own context.

``rails runner``, ``php artisan migrate``, ``node scripts/fix.js``, a shell of
the database in the container: the commands an application has that are not its
start command. This is Heroku's ``run`` and the safer half of the terminal
(checklist B-71, M-42): the same account and the same secrets as an interactive
session (:mod:`noust.terminal.targets`), without a person at a keyboard, so
there is no input to record and no shell to escape into.

- The command is an **argument vector**, given to the program as it is. It is
  never a line, and nothing here puts it behind ``sh -c``: ``["echo", "a; b"]``
  prints ``a; b``.
- It has a **deadline** (``terminal.exec.max_seconds`` is the ceiling) and its
  **output is bounded**: a command that prints without end is cut and the log
  says so, and stops costing memory.
- Its output reaches the caller **line by line**, scrubbed of the application's
  known secrets, so a job's log, the console and the terminal show what the
  command did and not what it knew.
- It is **on record** twice (``terminal.exec``: started, finished) with the
  command (scrubbed), the exit status and how long it took.

A stack's command runs through ``docker exec``. The Docker client is stopped at
the deadline; Docker leaves the process it started inside the container, which
this cannot reach: it is written in ``docs/terminal.md``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

from noust.core import audit
from noust.core.exceptions import ValidationError
from noust.core.runner import CommandRunner, get_runner
from noust.terminal.settings import load_settings
from noust.terminal.targets import ExecPlan, plan_app_exec

logger = logging.getLogger(__name__)

#: The longest a one-off command is given when it names no time.
DEFAULT_TIMEOUT_SECONDS = 300

#: The most output a command may hand back. What follows is dropped, once said.
MAX_OUTPUT_BYTES = 2 * 1024 * 1024

#: The most of the command the audit trail keeps.
MAX_COMMAND_CHARS = 1024

OUTPUT_CUT = "[output cut: the command printed more than {limit} KiB, the rest is not kept]"


@dataclass(frozen=True)
class ExecResult:
    """
    How a one-off command went.

    Attributes:
        exit_code: Its exit status; -1 when it did not finish in time.
        timed_out: It was stopped at its deadline.
        duration: Seconds it ran.
        output_bytes: What it printed, as far as it was kept.
        output_cut: More was printed than was kept.
        target: What it ran in, as a header would say.
        out_of_memory: The kernel killed it for going past the memory limit.
    """

    exit_code: int
    timed_out: bool
    duration: float
    output_bytes: int
    output_cut: bool
    target: str
    out_of_memory: bool = False

    @property
    def success(self) -> bool:
        """Whether it finished, with status 0."""
        return self.exit_code == 0 and not self.timed_out


def clamp_timeout(timeout: int | None) -> int:
    """
    Decide how long a command may run.

    Args:
        timeout: What the caller asked for, or None for the default.

    Returns:
        The seconds: the default when none was asked, never more than the
        configured ceiling.

    Raises:
        ValidationError: A time that is not a positive number of seconds.
    """
    ceiling = load_settings().exec_max_seconds
    if timeout is None:
        return min(DEFAULT_TIMEOUT_SECONDS, ceiling)
    if isinstance(timeout, bool) or timeout < 1:
        raise ValidationError(
            f"A command needs at least one second, not {timeout!r}", field="timeout"
        )
    if timeout > ceiling:
        raise ValidationError(
            f"A command may run at most {ceiling} seconds here, not {timeout}",
            details="An administrator raises the ceiling with: "
            "noust config set terminal.exec.max_seconds N",
            field="timeout",
        )
    return timeout


def _command_text(plan: ExecPlan) -> str:
    """
    Say what is being run, for the audit trail, without the secrets Noust knows.

    Args:
        plan: The plan.

    Returns:
        The command line, scrubbed and cut.
    """
    argv = plan.argv
    if plan.target.container is not None:
        # "docker exec <container>" is how it is carried, not what was asked.
        argv = argv[3:]
    text = plan.scrubber.scrub(" ".join(argv))
    return text if len(text) <= MAX_COMMAND_CHARS else text[:MAX_COMMAND_CHARS] + "..."


def run_exec(
    domain: str,
    argv: list[str],
    *,
    service: str | None = None,
    timeout: int | None = None,
    on_line: Callable[[str], None],
    runner: CommandRunner | None = None,
) -> ExecResult:
    """
    Run one command in an application and hand back what it printed as it prints it.

    Args:
        domain: The application. Judged by the caller's teams before this.
        argv: The program and its arguments.
        service: The Compose service, for a stack.
        timeout: Seconds it may run; the default when None.
        on_line: Called with each line of output, scrubbed, without its newline.
        runner: The runner; the process-wide one by default.

    Returns:
        How it went. A command that exits non-zero is a result, not an error:
        what to make of it is the caller's.

    Raises:
        ValidationError: Unknown application, service, or an invalid command or time.
        DeploymentError: Nothing to run it in (a static site, a stack that is
            not running).
    """
    seconds = clamp_timeout(timeout)
    runner = runner or get_runner()
    plan = plan_app_exec(domain, argv, service=service, runner=runner)
    command = _command_text(plan)
    target = f"app:{plan.target.domain}"
    audit.record(
        "terminal.exec",
        target=target,
        details={
            "stage": "started",
            "command": command,
            "account": plan.target.account,
            "service": plan.target.service,
            "timeout": seconds,
        },
    )

    kept = 0
    cut = False

    def forward(line: str) -> None:
        nonlocal kept, cut
        clean = plan.scrubber.scrub(line)
        size = len(clean.encode("utf-8", errors="replace")) + 1
        if cut:
            return
        if kept + size > MAX_OUTPUT_BYTES:
            cut = True
            on_line(OUTPUT_CUT.format(limit=MAX_OUTPUT_BYTES // 1024))
            return
        kept += size
        on_line(clean)

    started = time.monotonic()
    result = runner.stream(
        list(plan.argv),
        on_line=forward,
        cwd=plan.cwd,
        env=plan.env,
        timeout=seconds,
        sandbox=plan.sandbox,
    )
    duration = time.monotonic() - started
    outcome = ExecResult(
        exit_code=result.exit_code,
        timed_out=result.timed_out,
        duration=round(duration, 3),
        output_bytes=kept,
        output_cut=cut,
        target=plan.target.label,
        out_of_memory=result.out_of_memory,
    )
    audit.record(
        "terminal.exec",
        target=target,
        outcome="ok" if outcome.success else "failure",
        details={
            "stage": "finished",
            "command": command,
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "out_of_memory": outcome.out_of_memory,
            "duration": outcome.duration,
            "output_bytes": outcome.output_bytes,
            "output_cut": outcome.output_cut,
        },
    )
    return outcome
