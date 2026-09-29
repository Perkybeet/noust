# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust migrate-from-wasm``: move a WASM 2.x server onto Noust's names.

The same migration runs on its own the first time an operator runs a
privileged command (:func:`noust.core.migrate_from_wasm.run_automatically`);
this command shows what it did or would do, and finishes whatever it left.
The work is in :mod:`noust.core.migrate_from_wasm`; this module only presents
it.
"""

from __future__ import annotations

import json
import os

import click

from noust.cli.app import Context, NoustCommand, global_flags, json_option, pass_context
from noust.core import paths
from noust.core.logger import Logger
from noust.core.migrate_from_wasm import MigrationReport, Migrator, needs_migration

#: How each step's status is shown.
_STATUS_LABELS = {
    "pending": "would do",
    "done": "done",
    "refused": "not done",
    "failed": "undone",
    "skipped": "skipped",
}


def _print_report(logger: Logger, report: MigrationReport) -> None:
    """
    Print a report, one line per step and the reason under any not done.

    Args:
        logger: The command's logger.
        report: What happened, or would.
    """
    for step in report.steps:
        label = _STATUS_LABELS.get(step.status, step.status)
        status = {"done": "ok", "pending": "info", "skipped": "info"}.get(step.status, "error")
        logger.check(label, step.description, status)
        if step.detail:
            for line in step.detail.splitlines():
                logger.info(f"    {line}")


def _print_record(logger: Logger) -> bool:
    """
    Print what earlier runs did, from the record they left.

    Args:
        logger: The command's logger.

    Returns:
        True when a record was found.
    """
    record = paths.MIGRATION_RECORD
    try:
        history = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(history, list):
        return False
    for run in history:
        done = [s for s in run.get("steps", []) if s.get("status") == "done"]
        logger.info(f"{run.get('at', '?')} (Noust {run.get('version', '?')}):")
        for step in done:
            logger.check("done", step.get("description", ""), "ok")
    return True


@click.command("migrate-from-wasm", cls=NoustCommand)
@json_option("Print what was done, or would be, as JSON.")
@global_flags
@pass_context
def cli(ctx: Context) -> int:
    """
    Move this server from WASM's names to Noust's.

    Renames /etc/wasm, /var/lib/wasm (and the store inside it) and
    /var/backups/wasm to their Noust names, leaving links with the old ones;
    points nginx and PHP-FPM at the renamed upstreams and pools; and replaces
    WASM's own units (wasm-web, wasm-monitor, wasm-cron-*, wasm-backup-*,
    wasm-previews) with the same units named noust-*, in the same state.
    Every step is undone when it fails, and running it again finishes what is
    left. It also runs on its own the first time a privileged command does.

    With --dry-run it only says what it would do.
    """
    logger = Logger(verbose=ctx.verbose)

    if ctx.dry_run or not needs_migration():
        report = Migrator().plan()
        if ctx.json_output:
            click.echo(json.dumps(report.as_dict(), indent=2))
            return 0
        if report.steps:
            logger.info("The migration from WASM would:")
            _print_report(logger, report)
        elif not _print_record(logger):
            logger.info("Nothing of WASM's is left to migrate on this server.")
        else:
            logger.info("Nothing is left to migrate.")
        return 0

    if os.geteuid() != 0:
        logger.error("The migration from WASM needs root")
        logger.info("Run it as root: sudo noust migrate-from-wasm")
        return 1

    report = Migrator(announce=logger.substep).run()
    if ctx.json_output:
        click.echo(json.dumps(report.as_dict(), indent=2))
    else:
        _print_report(logger, report)
        if report.complete:
            logger.success("This server now runs under Noust's names.")
        else:
            logger.warning("Some steps were not done; what each needs is shown under it.")
    return 0 if report.complete else 1
