# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup drill``: prove an application's newest backup restores, without restoring it.

The CLI of :class:`~noust.managers.restore_drill.RestoreDrill`, which
``/api/apps/{domain}/restore-drills`` is the other client of. ``drill`` runs
one now and prints what it checked; ``drills`` lists the reports kept. A
scheduled backup runs one on its own when the last good drill is older than
``backup.drill_days``.

``noust backup`` loads it through :func:`register`.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.managers.restore_drill import DrillRecords, DrillReport, RestoreDrill, drill_days
from noust.validators.domain import validate_domain


def _print(ctx: Context, report: DrillReport) -> None:
    """
    Print a drill's report as an operator reads it.

    Args:
        ctx: The command's context.
        report: The report.
    """
    logger = ctx.logger
    logger.header(f"Restore drill: {report.domain}")
    logger.key_value("Backup", report.backup_id or "none")
    logger.key_value("When", report.started_at)
    for step in report.steps:
        line = f"{step.name}: {step.detail}"
        if not step.ok:
            logger.error(line)
        elif step.skipped:
            logger.info(f"  {line}")
        else:
            logger.success(line)
    if report.ok:
        logger.success("The newest backup comes back.")
    else:
        logger.error("The newest backup could not be shown to restore.")


@click.command("drill", cls=NoustCommand)
@click.argument("domain")
@click.option("--backup", "backup_id", help="Rehearse this backup instead of the newest.")
@json_option("Print the report as JSON.")
@pass_context
def drill_command(ctx: Context, domain: str, backup_id: str | None) -> None:
    """
    Rehearse restoring an application's newest backup, and keep the report.

    The backup is verified in depth, unpacked with the rules a restore uses,
    and each database dump in it is loaded into a temporary database and
    dropped. Nothing of the application, its databases or its units is
    touched, and it is not started: a drill proves the files unpack and the
    data loads. Exits 1 when something fails. A scheduled backup runs one on
    its own every ``backup.drill_days`` (30 by default; 0 turns it off).
    """
    report = RestoreDrill(logger=ctx.logger).run(
        validate_domain(domain), backup_id=backup_id, on_step=ctx.logger.substep
    )
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict()))
    else:
        _print(ctx, report)
    if not report.ok:
        raise SystemExit(1)


@click.command("drills", cls=NoustCommand, read_only=True)
@click.argument("domain", required=False)
@click.option("--limit", default=10, show_default=True, help="How many reports to list.")
@json_option("Print the reports as JSON.")
@pass_context
def drills_command(ctx: Context, domain: str | None, limit: int) -> None:
    """List the restore drills that were run, newest first."""
    reports = DrillRecords().list(validate_domain(domain) if domain else None, limit=limit)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"items": [report.to_dict() for report in reports], "drill_days": drill_days()}
            )
        )
        return
    if not reports:
        ctx.logger.info(
            "No drill has run yet. Run one with: noust backup drill DOMAIN"
            + ("" if drill_days() else " (scheduled drills are off: backup.drill_days is 0)")
        )
        return
    for report in reports:
        mark = "passed" if report.ok else "FAILED"
        ctx.logger.key_value(
            f"{report.domain} #{report.id}", f"{mark} {report.started_at} ({report.trigger})"
        )
        if not report.ok:
            ctx.logger.info(f"    {report.summary}")


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(drill_command)
    group.add_command(drills_command)
