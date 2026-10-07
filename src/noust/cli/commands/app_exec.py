# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app exec``: run one command in an application, as the application.

``noust app exec shop.example.com -- rails runner 'puts User.count'``: the
command after ``--`` is an argument vector, given to the program as it is (never
a shell line), run as the application's own account in its sandbox, or in a
container of its stack, with the application's environment. What it prints is
printed here as it appears, and the command's own exit status is this
command's. It is :func:`noust.terminal.execution.run_exec`, which
``POST /api/apps/{domain}/exec`` runs as a job too; this module only parses,
presents and asks.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.terminal.execution import clamp_timeout, run_exec
from noust.terminal.targets import validate_command
from noust.validators.domain import validate_domain


@click.command("exec", cls=NoustCommand, context_settings={"ignore_unknown_options": True})
@click.argument("domain")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@click.option("--service", help="The Compose service to run it in, for a stack that runs several.")
@click.option(
    "--timeout",
    type=int,
    default=None,
    help="Seconds it may run; 300 by default, at most terminal.exec.max_seconds.",
)
@json_option("Print how it went as JSON, after its output.")
@pass_context
def exec_command(
    ctx: Context,
    domain: str,
    command: tuple[str, ...],
    service: str | None,
    timeout: int | None,
) -> None:
    """
    Run one command in an application, as the application: noust app exec DOMAIN -- PROGRAM ARGS.

    The program and its arguments follow ``--``. They are never given to a
    shell: ``-- echo 'a; b'`` prints ``a; b``. The command has the
    application's own account, its sandbox and its environment (a stack's
    command runs in the container of the service). It is on record, with the
    command and how it ended. The exit status is the command's.
    """
    validated = validate_domain(domain)
    argv = list(command)
    if not argv:
        raise ValidationError(
            "Say what to run, after --",
            details=f"For example: noust app exec {validated} -- rails runner 'puts 1'",
            field="command",
        )
    validate_command(argv)
    seconds = clamp_timeout(timeout)
    if ctx.dry_run:
        ctx.logger.info(f"Would run in {validated} (at most {seconds} s): {' '.join(argv)}")
        return
    result = run_exec(
        validated, argv, service=service, timeout=seconds, on_line=lambda line: click.echo(line)
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration": result.duration,
                    "output_bytes": result.output_bytes,
                    "output_cut": result.output_cut,
                    "target": result.target,
                }
            )
        )
    if result.timed_out:
        ctx.logger.error(
            f"The command did not finish within {seconds} seconds, and was stopped.",
        )
        raise click.exceptions.Exit(124)
    if result.out_of_memory:
        ctx.logger.error("The command was killed for using more memory than the application may.")
        raise click.exceptions.Exit(137)
    if result.exit_code != 0:
        raise click.exceptions.Exit(result.exit_code if 0 < result.exit_code < 256 else 1)
