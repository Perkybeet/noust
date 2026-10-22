# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app replicas``: how many copies of its web process an application runs.

Without COPIES it says how the application is scaled and how each copy is; with it,
the copies are started one by one, each asked the application's own health check on
its own port, and only then does nginx share the traffic with them. Both are
:mod:`noust.managers.app_replicas`, which ``/api/apps/{d}/replicas`` calls too.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.app_replica_store import MAX_COPIES
from noust.managers.app_replicas import ReplicaManager
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


@click.command("replicas", cls=NoustCommand)
@click.argument("domain")
@click.argument("copies", required=False, type=click.IntRange(1, MAX_COPIES))
@json_option("Print the copies as JSON.")
@pass_context
def replicas_command(ctx: Context, domain: str, copies: int | None) -> None:
    """
    Show how an application is scaled, or run it as COPIES copies of its web process.

    COPIES counts the application's own unit: 3 is the unit and two more, each on a
    port of its own, behind the same nginx site. 1 runs it alone again. A new copy that
    does not answer its health check leaves everything as it was.
    """
    validated = validate_domain(domain)
    manager = ReplicaManager(log=ctx.logger)
    if copies is not None:
        change = manager.set_copies(validated, copies, actor=_actor())
        if ctx.json_output:
            click.echo(
                json.dumps(
                    {
                        "domain": change.domain,
                        "copies": change.copies,
                        "before": change.before,
                        "ports": list(change.ports),
                        "started": list(change.started),
                        "stopped": list(change.stopped),
                    }
                )
            )
            return
        if change.copies == change.before:
            ctx.logger.info(f"{validated} already runs {change.copies} cop{_plural(change.copies)}")
            return
        ctx.logger.success(
            f"{validated} runs {change.copies} cop{_plural(change.copies)} (it ran {change.before})"
        )
        return
    status = manager.status(validated)
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    ctx.logger.key_value("Copies", f"{status.copies} of at most {status.maximum}")
    if not status.eligible:
        ctx.logger.warning(f"It cannot be scaled out: {status.reason}")
    ctx.logger.table(
        ["Unit", "Port", "State", "Traffic"],
        [
            [
                copy.unit + (" (own)" if copy.own else ""),
                str(copy.port),
                copy.state,
                "yes" if copy.serving else "no",
            ]
            for copy in status.units
        ],
    )


def _plural(count: int) -> str:
    """
    Pick the ending of "copy".

    Args:
        count: How many.

    Returns:
        ``y`` for one, ``ies`` otherwise.
    """
    return "y" if count == 1 else "ies"
