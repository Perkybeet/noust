# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup sweep``: apply every backup policy's retention now (checklist H-04).

The CLI of :class:`~noust.managers.backup_sweep.BackupSweep`, which a daily timer
(``noust-backup-sweep``) also runs: retention used to wait for a run that went well, so a policy
whose runs failed (a full disk) or whose timer stopped pruned nothing.

``noust backup`` loads it through :func:`register`.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.managers.backup_sweep import BackupSweep


@click.command("sweep", cls=NoustCommand)
@json_option("Print the report as JSON.")
@pass_context
def sweep(ctx: Context) -> None:
    """
    Delete the backups beyond every policy's retention, without waiting for a run.

    Applies the limits of every database policy, every application's data policy and every
    application schedule to the copies on this server. Only copies a schedule made are
    considered; a manual backup and a restore's safety copy are never touched, the newest copy
    stays, and when the newest failed its check the newest good one stays too.
    """
    report = BackupSweep().run()
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        for name, files in sorted(report.databases.items()):
            ctx.logger.info(f"  {name}: deleted {len(files)} dump(s)")
        for name, ids in sorted(report.volumes.items()):
            ctx.logger.info(f"  {name}: deleted {len(ids)} data backup(s)")
        for name, count in sorted(report.applications.items()):
            ctx.logger.info(f"  {name}: deleted {count} backup(s)")
        for kind, why in sorted(report.errors.items()):
            ctx.logger.error(f"  The {kind} could not be swept: {why}")
        if report.deleted:
            ctx.logger.success(f"{report.deleted} copy(ies) beyond retention deleted")
        elif report.ok:
            ctx.logger.info("Nothing is beyond its retention")
    if not report.ok:
        click.get_current_context().exit(1)


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(sweep)
