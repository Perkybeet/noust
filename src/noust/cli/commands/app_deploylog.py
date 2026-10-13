# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app deploy-log``: the captured log of a deployment, filtered and ready to share.

A client of :func:`noust.deployers.logs.read_deployment_log`, like
``GET /api/deployments/{id}/log``: the same level filter, search, timestamps switch and
sanitised copy, so what the console copies and what a script reads are one thing.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.deployers.logs import LEVELS, read_deployment_log
from noust.validators.domain import validate_domain


@click.command("deploy-log", cls=NoustCommand, read_only=True)
@click.argument("domain")
@click.argument("deployment_id", required=False, type=int)
@click.option(
    "--level",
    type=click.Choice(LEVELS),
    default="all",
    show_default=True,
    help="warn keeps warnings and errors; error keeps only errors.",
)
@click.option("--search", help="Keep only the lines that hold this text, ignoring case.")
@click.option(
    "--timestamps/--no-timestamps", default=True, show_default=True, help="Keep each line's stamp."
)
@click.option(
    "--sanitize",
    is_flag=True,
    default=False,
    help="Mask what looks like a credential and strip control sequences, to share the log.",
)
@click.option("--tail", type=click.IntRange(min=1), help="Only this many bytes, from the end.")
@json_option("Print the log and whether it was cut as JSON.")
@pass_context
def deploy_log_command(
    ctx: Context,
    domain: str,
    deployment_id: int | None,
    level: str,
    search: str | None,
    timestamps: bool,
    sanitize: bool,
    tail: int | None,
) -> None:
    """
    Print the captured log of a deployment of an application.

    Without DEPLOYMENT_ID, the application's latest deployment. The secrets the
    build knew were taken out when the log was written; --sanitize also masks what
    only looks like one (a connection string's password, a token, a key block).
    """
    validated = validate_domain(domain)
    store = get_store()
    if deployment_id is None:
        latest = store.list_deployments(validated, limit=1)
        if not latest:
            raise NoustError(
                f"{validated} has no deployments yet",
                details=f"Deploy it first, or see what exists: noust list {validated}",
            )
        record = latest[0]
    else:
        found = store.get_deployment(deployment_id)
        if found is None or found.domain != validated:
            raise NoustError(
                f"Deployment {deployment_id} of {validated} not found",
                details="List the application's deployments in the console, or omit the id.",
            )
        record = found
    result = read_deployment_log(
        record, tail=tail, level=level, search=search, timestamps=timestamps, sanitize=sanitize
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "deployment": record.id,
                    "content": result.content,
                    "truncated": result.truncated,
                    "missing_reason": result.missing_reason,
                }
            )
        )
        return
    if result.missing_reason:
        raise NoustError(result.missing_reason)
    if result.truncated:
        ctx.logger.warning("The log is longer than what is shown: the end of it is printed")
    click.echo(result.content, nl=False)
