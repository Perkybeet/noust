# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup failures``: the whole output of a backup that failed (checklist H-10).

A notice carries the end of what a failed backup said, bounded to what a chat message holds, and
names the record that keeps all of it: ``noust backup failures show 42``. Reading is the same data
as ``GET /api/backups/failures``; ``--raw`` prints nothing but the output, so it can be piped to a
file or a pager.

``noust backup`` loads it through :func:`register`.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.managers.backup_failures import BackupFailureLog


@click.group("failures", cls=NoustGroup)
def failures() -> None:
    """Read the whole output of the backups that failed."""


@failures.command("list", read_only=True)
@click.option("--subject", help="Only the failures about this (a domain, Noust, engine/database).")
@click.option(
    "--limit",
    type=click.IntRange(1, 200),
    default=20,
    show_default=True,
    help="How many to list, newest first.",
)
@json_option("Print the list as JSON.")
@pass_context
def list_failures(ctx: Context, subject: str | None, limit: int) -> None:
    """
    List the backups that failed, newest first, without their output.
    """
    records = BackupFailureLog().recent(subject=subject, limit=limit)
    if ctx.json_output:
        click.echo(json.dumps({"failures": [record.summary() for record in records]}, indent=2))
        return
    if not records:
        ctx.logger.info("No backup has failed (or the record of it was pruned).")
        return
    for record in records:
        where = f" to {record.destination}" if record.destination else ""
        what = "could not be sent" if record.kind == "upload" else "failed"
        ctx.logger.info(
            f"  #{record.id}  {record.created_at}  {record.subject}{where}  {what}  "
            f"({record.size} bytes)"
        )
    ctx.logger.info("Read one with 'noust backup failures show ID'.")


@failures.command("show", read_only=True)
@click.argument("failure_id", type=int)
@click.option("--raw", is_flag=True, help="Print only the output, exactly as it was recorded.")
@json_option("Print the record, output included, as JSON.")
@pass_context
def show_failure(ctx: Context, failure_id: int, raw: bool) -> None:
    """
    Print all that a failed backup said, the record a notice names.
    """
    record = BackupFailureLog().get(failure_id)
    if record is None:
        ctx.logger.error(
            f"No backup failure {failure_id}",
            "List them with 'noust backup failures list'; only the newest are kept.",
        )
        click.get_current_context().exit(1)
        return
    if ctx.json_output:
        click.echo(json.dumps(record.to_dict(), indent=2))
        return
    if not raw:
        where = f" to {record.destination}" if record.destination else ""
        ctx.logger.info(f"#{record.id}  {record.created_at}  {record.subject}{where}")
        if record.truncated:
            ctx.logger.warning("The output was longer than is kept: its first part was cut.")
    click.echo(record.output)


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(failures)
