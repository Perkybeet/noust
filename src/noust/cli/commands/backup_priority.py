# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup priority``: how hard the backups lean on the server while they run.

The CLI of :mod:`noust.managers.backup_priority`. ``show`` prints the settings and whether
the units on this server follow them, ``set`` changes them and brings the units along, ``apply``
brings the units along after the configuration was edited by hand or Noust was upgraded.

``noust backup`` loads it through :func:`register`.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.config import Config
from noust.core.exceptions import NoustError
from noust.managers.backup_priority import BackupPriority, current_priority
from noust.managers.backup_scheduler import BackupScheduler


def _describe(priority: BackupPriority) -> dict[str, Any]:
    """
    Args:
        priority: The settings.

    Returns:
        Them as plain data.
    """
    return {
        "nice": priority.nice,
        "io_class": priority.io_class,
        "io_priority": priority.io_priority if priority.io_class == "best-effort" else None,
        "cpu_quota": priority.cpu_quota,
    }


def _fail(ctx: Context, exc: NoustError) -> None:
    """
    Report a refused operation, with its fix, and leave with status 1.

    Args:
        ctx: The invocation's context.
        exc: The error.
    """
    ctx.logger.error(str(exc))
    click.get_current_context().exit(1)


@click.group("priority", cls=NoustGroup)
def priority() -> None:
    """How hard the backups lean on the server while they run: nice, disk class, CPU ceiling."""


@priority.command("show", read_only=True)
@json_option("Print the settings as JSON.")
@pass_context
def show(ctx: Context) -> None:
    """Show the settings every backup service is written with."""
    try:
        current = current_priority()
    except NoustError as exc:
        _fail(ctx, exc)
        return
    data = _describe(current)
    if ctx.json_output:
        click.echo(json.dumps(data, indent=2))
        return
    ceiling = f"{current.cpu_quota}% of one CPU" if current.cpu_quota else "none"
    disk = (
        f"best-effort, priority {current.io_priority} (7 is the last)"
        if current.io_class == "best-effort"
        else "idle (only when nothing else wants the disk)"
    )
    click.echo(f"Nice:         {current.nice} (0 as an application, 19 the last)")
    click.echo(f"Disk:         {disk}")
    click.echo(f"CPU ceiling:  {ceiling}")
    click.echo("\nChange them with: noust backup priority set")


@priority.command("set")
@click.option("--nice", type=click.IntRange(0, 19), help="Nice value, 0 to 19.")
@click.option(
    "--io-class",
    type=click.Choice(["best-effort", "idle"]),
    help="Disk scheduling class: best-effort, or idle (only when nothing else wants the disk).",
)
@click.option(
    "--io-priority", type=click.IntRange(0, 7), help="Priority inside best-effort: 0 first, 7 last."
)
@click.option(
    "--cpu-quota",
    help="Ceiling in percent of one CPU (50 is half a core, 200 two), or 'none' for no ceiling.",
)
@json_option("Print the result as JSON.")
@pass_context
def set_priority(
    ctx: Context,
    nice: int | None,
    io_class: str | None,
    io_priority: int | None,
    cpu_quota: str | None,
) -> None:
    """
    Change the settings and bring the backup services already on this server along.

    Units Noust writes later are written with them too.
    """
    if nice is None and io_class is None and io_priority is None and cpu_quota is None:
        ctx.logger.error("Name what to change: --nice, --io-class, --io-priority or --cpu-quota")
        click.get_current_context().exit(1)
    config = Config()
    try:
        for key, value in (
            ("backup.nice", nice),
            ("backup.io_class", io_class),
            ("backup.io_priority", io_priority),
            ("backup.cpu_quota", cpu_quota),
        ):
            if value is not None:
                config.set(key, value)
        wanted = current_priority()
        config.save()
        changed = BackupScheduler(verbose=ctx.logger.verbose).apply_priority()
    except NoustError as exc:
        _fail(ctx, exc)
        return
    data = {**_describe(wanted), "units_changed": changed}
    if ctx.json_output:
        click.echo(json.dumps(data, indent=2))
        return
    ctx.logger.success("Backup priority saved")
    ctx.logger.info(f"  {len(changed)} backup service(s) on this server follow it now")


@priority.command("apply")
@json_option("Print the units that changed as JSON.")
@pass_context
def apply(ctx: Context) -> None:
    """
    Bring the backup services on this server to the configured settings.

    Run it after editing the configuration by hand, or after an upgrade that
    gave the backups a priority.
    """
    try:
        changed = BackupScheduler(verbose=ctx.logger.verbose).apply_priority()
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        click.echo(json.dumps({"units_changed": changed}, indent=2))
        return
    if changed:
        for name in changed:
            ctx.logger.info(f"  rewrote {name}")
        ctx.logger.success(f"{len(changed)} backup service(s) updated")
    else:
        ctx.logger.info("Every backup service already follows the settings")


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(priority)
