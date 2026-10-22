# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A health check that is a command: a program run as the application.

``test -s /app/run/ready``, ``php artisan health:check``, ``pg_isready`` in the
container of a stack. The application is up when the command exits 0. It runs
where and as whom a one-off command does (:func:`noust.terminal.targets.plan_app_exec`):
the application's own account in its sandbox, or ``docker exec`` into the
container of its stack. Unlike ``noust app exec`` it leaves no audit record per
attempt: a gate asks every two seconds, and the record that matters is the
deployment's, which carries the last output verbatim.
"""

from __future__ import annotations

import math
import shlex

from noust.core.runner import CommandRunner, get_runner
from noust.terminal.targets import plan_app_exec

#: The most of a failed command's output a probe keeps: enough to read why,
#: not enough to fill an evidence block with a stack trace fifteen times.
OUTPUT_KEPT = 400


def run_health_command(
    domain: str,
    command: str,
    *,
    service: str | None,
    timeout: float,
    runner: CommandRunner | None = None,
) -> tuple[bool, str]:
    """
    Run an application's health command once.

    Args:
        domain: The application.
        command: The command line; split like a POSIX shell would split it,
            and run by none.
        service: The Compose service, for a stack.
        timeout: Seconds it may take.
        runner: The runner; the process-wide one by default.

    Returns:
        Whether it exited 0, and, when it did not, what it said and how it
        ended, scrubbed of the secrets Noust knows.
    """
    runner = runner or get_runner()
    plan = plan_app_exec(domain, shlex.split(command), service=service, runner=runner)
    result = runner.run(
        list(plan.argv),
        cwd=plan.cwd,
        env=plan.env,
        timeout=max(1, math.ceil(timeout)),
        sandbox=plan.sandbox,
    )
    if result.success:
        return True, ""
    said = plan.scrubber.scrub((result.stderr or result.stdout).strip())
    if len(said) > OUTPUT_KEPT:
        said = said[:OUTPUT_KEPT] + "..."
    how = (
        f"did not finish in {math.ceil(timeout)} s"
        if result.timed_out
        else f"exited with status {result.exit_code}"
    )
    return False, f"{plan.target.label}: {command} {how}" + (f": {said}" if said else "")
