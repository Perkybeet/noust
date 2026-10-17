# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app disk``: what an application takes of the disk, and the limit its deploys are held to.

Without options it shows the last measurement and how it stands against the limit; ``--measure``
walks the application's trees now (it can take a while on a release tree with dependencies in it),
``--limit MB`` sets the limit and ``--no-limit`` removes it. The limit is Noust's: a deploy that
would build a new release is refused while the application is over it, and going back to an
earlier release never is. All of it is :mod:`noust.managers.app_disk`, which
``/api/apps/{d}/disk`` calls too; this module only parses, presents and asks.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.core.utils import format_bytes
from noust.managers import app_disk
from noust.validators.domain import validate_domain

_QUOTA_WORDS = {
    "enabled": "enabled: the filesystem is mounted with project quotas",
    "available": "available but not enabled: mount with prjquota to hold the writes themselves",
    "unsupported": "not offered by this filesystem",
    "unknown": "could not be asked",
}


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _show(ctx: Context, shown: dict[str, Any]) -> None:
    """
    Print an application's disk.

    Args:
        ctx: The command's context.
        shown: :meth:`noust.managers.app_disk.DiskStatus.to_dict`.
    """
    logger = ctx.logger
    logger.key_value("Application", shown["domain"])
    soft = shown["soft_mb"]
    logger.key_value("Limit", f"{soft} MB" if soft is not None else "none")
    usage = shown["usage"]
    if usage is None:
        logger.key_value("Measured", "never")
        logger.info(f"Measure it with: noust app disk {shown['domain']} --measure")
    else:
        logger.key_value("Measured", usage["measured_at"])
        logger.key_value("Takes", format_bytes(usage["total_bytes"]))
        for part in usage["parts"]:
            extra = (
                f", {format_bytes(part['reclaimable_bytes'])} a prune would give back"
                if part.get("reclaimable_bytes")
                else ""
            )
            logger.info(f"  {part['name']}: {format_bytes(part['bytes'])}{extra}")
        if usage["backups_bytes"]:
            logger.key_value("Backups", f"{format_bytes(usage['backups_bytes'])} (not counted)")
        for problem in usage["errors"]:
            logger.warning(problem)
    verdict = shown["verdict"]
    if verdict is not None:
        words = {"ok": "under the limit", "near": "near the limit", "over": "over the limit"}
        logger.key_value(
            "Standing", f"{verdict['percent']}% of the limit: {words[verdict['state']]}"
        )
    filesystem = shown["filesystem"]
    where = (
        f"{filesystem['fstype']} on {filesystem['mount']}" if filesystem["fstype"] else "unknown"
    )
    kernel = shown["kernel"]
    if kernel["applied"]:
        held = format_bytes(kernel["used_bytes"]) if kernel["used_bytes"] is not None else "unknown"
        logger.key_value(
            "Kernel quota",
            f"held: project {kernel['project']} on {kernel['mount']}, {kernel['hard_mb']} MB, "
            f"{held} counted",
        )
    else:
        logger.key_value(
            "Kernel quota",
            f"not applied; {where}: {_QUOTA_WORDS[filesystem['project_quota']]} (docs/runtime.md)",
        )


@click.command(
    "disk",
    cls=NoustCommand,
    # Showing it, and measuring it, change nothing on the machine; only the limit is a change.
    read_only=lambda params: (
        params["limit"] is None and not params["no_limit"] and params["kernel"] is None
    ),
)
@click.argument("domain")
@click.option("--measure", is_flag=True, help="Measure what it takes now, and keep the answer.")
@click.option(
    "--limit",
    "limit",
    type=click.IntRange(app_disk.MIN_LIMIT_MB, app_disk.MAX_LIMIT_MB),
    default=None,
    metavar="MB",
    help="Hold deploys to this many megabytes: a new release is refused while it is over.",
)
@click.option("--no-limit", is_flag=True, help="Remove the limit.")
@click.option(
    "--kernel/--no-kernel",
    "kernel",
    default=None,
    help="Give the limit to the kernel as a project quota (XFS or ext4 mounted with prjquota), "
    "or take it off. The writes themselves are then refused at the limit.",
)
@json_option("Print the disk as JSON.")
@pass_context
def disk_command(
    ctx: Context,
    domain: str,
    measure: bool,
    limit: int | None,
    no_limit: bool,
    kernel: bool | None,
) -> None:
    """
    Show what the application takes of the disk, and the limit its deploys are held to.

    The limit is Noust's, not the kernel's: it is checked before a deploy builds a new release,
    and going back to an earlier release is never held up by it. It does not stop the application
    from writing; --kernel gives the limit to the kernel as a project quota, which does, on a
    filesystem you mounted with prjquota (docs/runtime.md says how).
    """
    if limit is not None and no_limit:
        raise click.UsageError("Give --limit MB or --no-limit, not both.")
    validated = validate_domain(domain)
    app = get_store().get_app(validated)
    if app is None:
        raise NoustError(
            f"Application not found: {validated}",
            details="Check 'noust list' for the exact domain.",
        )
    if limit is not None or no_limit:
        app_disk.set_disk_limit(app, None if no_limit else limit, actor=_actor())
    if kernel is not None:
        ctx.logger.info(
            "Setting the project on every file of the tree; a large one takes a while."
            if kernel
            else "Taking the project off the tree."
        )
        app_disk.kernel_state_after(validated, kernel, actor=_actor())
    if measure:
        ctx.logger.info("Measuring; a large tree takes a while.")
        app_disk.measure_and_record(app)
    shown = app_disk.status(app).to_dict()
    if ctx.json_output:
        click.echo(json.dumps(shown))
        return
    _show(ctx, shown)
