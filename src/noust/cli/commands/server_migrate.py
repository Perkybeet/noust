# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust server migrate``: moving to another server, in three commands.

``plan`` and ``prepare`` run on the old server, ``check`` on the new one after
``noust restore-server``. The work is
:class:`noust.managers.server_migration.ServerMigration`, which the console's API calls
too; this module parses arguments and prints. Nothing here stops, changes or removes
anything on the old server, and none of it edits DNS.

``noust server`` (:mod:`noust.cli.commands.server`) loads this module and calls
:func:`register`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.self_backup import PASSPHRASE_FILE, read_passphrase
from noust.managers.self_backup import SelfBackupManager
from noust.managers.server_migration import (
    MigrationCheck,
    MigrationPlan,
    PreparedMigration,
    ServerMigration,
)

#: What each state is called in the printed report.
_STEP_LABELS = {"done": "copied", "failed": "FAILED", "skipped": "left out"}
_APP_LABELS = {"ok": "ok", "attention": "needs attention", "down": "DOWN"}


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


@click.group("migrate", cls=NoustGroup)
def migrate() -> None:
    """Move this server's applications, data and state to another server."""


def _show_plan(ctx: Context, plan: MigrationPlan) -> None:
    """
    Print what moving would take.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Server", plan.host)
    logger.key_value("Destination", plan.destination)
    logger.key_value(
        "Bundle passphrase", "stored" if plan.passphrase_stored else "asked for when it is sealed"
    )
    if plan.applications:
        logger.table(
            ["Application", "Names", "Databases", "Data", "Newest backup"],
            [
                [
                    item["domain"],
                    ", ".join(item["names"]),
                    ", ".join(item["databases"]) or "-",
                    str(item["data"]),
                    item["last_backup"] or "none",
                ]
                for item in plan.applications
            ],
        )
    if plan.databases:
        logger.key_value("Standalone databases", ", ".join(plan.databases))
    if plan.engines:
        logger.key_value("The new server needs", ", ".join(plan.engines))
    for warning in plan.warnings:
        logger.warning(warning)
    for blocker in plan.blockers:
        logger.error(blocker)


@migrate.command("plan", read_only=True)
@click.option("--to", "destination", required=True, help="A backup destination both servers reach.")
@json_option("Print the plan as JSON.")
@pass_context
def plan_command(ctx: Context, destination: str) -> None:
    """
    Say what moving this server would take and whether it can start. Changes nothing.

    Lists each application with the names it answers on, its databases and how many pieces
    of data it has, the databases no application owns, and the engines the new server needs.
    """
    plan = ServerMigration(logger=ctx.logger).plan(destination)
    if ctx.json_output:
        _echo_json(plan.to_dict())
    else:
        _show_plan(ctx, plan)
    if plan.blockers:
        raise click.exceptions.Exit(1)


def _show_prepared(ctx: Context, report: PreparedMigration) -> None:
    """
    Print what the preparation did, and what to run next.

    Args:
        ctx: The invocation's context.
        report: What it did.
    """
    logger = ctx.logger
    logger.table(
        ["What", "Name", "Result", "Copy"],
        [
            [
                step.kind,
                step.name,
                _STEP_LABELS.get(step.state, step.state),
                step.backup or step.detail,
            ]
            for step in report.steps
        ],
    )
    for step in report.steps:
        if step.state == "failed":
            logger.error(f"{step.name}: {step.detail}")
    if report.ok:
        logger.success("Everything was copied and sent. On the new server:")
    else:
        logger.warning(
            "Not everything was copied (see above). Fix what failed and run it again: it is safe "
            "to repeat. On the new server:"
        )
    for number, text in enumerate(report.next_steps, 1):
        click.echo(f"  {number}. {text}")


@migrate.command("prepare")
@click.option("--to", "destination", required=True, help="A backup destination both servers reach.")
@click.option(
    "--app", "only", multiple=True, help="Move only this application's copies. Repeatable."
)
@click.option("--no-data", is_flag=True, help="Do not copy the applications' data.")
@click.option(
    "--stored-passphrase",
    is_flag=True,
    help="Seal the bundle under the passphrase 'noust self-backup schedule' keeps.",
)
@PASSPHRASE_FILE
@click.option("--yes", "-y", is_flag=True, help="Do not ask first.")
@json_option("Print the report as JSON.")
@pass_context
def prepare_command(
    ctx: Context,
    destination: str,
    only: tuple[str, ...],
    no_data: bool,
    stored_passphrase: bool,
    passphrase_file: Path | None,
    yes: bool,
) -> None:
    """
    Copy everything now and send it to a destination, ready for 'noust restore-server'.

    Each application is backed up (its release, .env, the dumps of its databases and its
    Docker volumes) and so is its data; the databases no application owns are dumped; and
    Noust's own state is sealed last, under a passphrase that is never sent. A piece that
    fails is reported and the rest go on. Nothing on this server is stopped or changed: it
    keeps answering until DNS points at the new one. Safe to repeat for a final copy.
    """
    migration = ServerMigration(logger=ctx.logger)
    plan = migration.plan(destination)
    if plan.blockers:
        if ctx.json_output:
            _echo_json(plan.to_dict())
        else:
            _show_plan(ctx, plan)
        raise click.ClickException("It cannot start: see what is listed above.")
    if not yes and not ctx.json_output:
        _show_plan(ctx, plan)
        click.confirm("Copy all of it to the destination now?", default=False, abort=True)
    if stored_passphrase:
        kept = SelfBackupManager(logger=ctx.logger).stored_passphrase()
        if kept is None:
            raise click.ClickException(
                "No passphrase is stored. Give one with --passphrase-file, or set it with "
                "'noust self-backup schedule set'."
            )
        passphrase = kept
    else:
        passphrase = read_passphrase(confirm=False, file=passphrase_file)
    report = migration.prepare(destination, passphrase, only=only, data=not no_data)
    if ctx.json_output:
        _echo_json(report.to_dict())
    else:
        _show_prepared(ctx, report)
    if not report.ok:
        raise click.exceptions.Exit(1)


def _show_check(ctx: Context, report: MigrationCheck) -> None:
    """
    Print the check.

    Args:
        ctx: The invocation's context.
        report: What it found.
    """
    logger = ctx.logger
    logger.key_value("Server", report.host)
    logger.key_value("Checked", report.generated_at)
    if report.addresses:
        logger.key_value("Addresses", ", ".join(report.addresses))
    logger.table(
        ["Application", "Result", "State", "Answers", "Names"],
        [
            [
                item.domain,
                _APP_LABELS.get(item.status, item.status),
                item.state or "-",
                "-" if item.answers is None else ("yes" if item.answers else "NO"),
                ", ".join(
                    f"{name['name']} ({'here' if name['resolves_here'] else 'elsewhere'})"
                    for name in item.names
                ),
            ]
            for item in report.applications
        ],
    )
    for item in report.applications:
        for problem in item.problems:
            logger.warning(f"{item.domain}: {problem}")
        if item.probe:
            click.echo(item.probe)
    if report.pending:
        logger.info("Names that still resolve to another server (point them here to switch):")
        for entry in report.pending:
            where = ", ".join(entry["resolves_to"]) or "nowhere"
            click.echo(f"  {entry['name']} -> {where}")
    counts = report.counts()
    summary = f"{counts['ok']} ok, {counts['attention']} need attention, {counts['down']} down"
    if report.ok:
        logger.success(summary)
    else:
        logger.error(summary)


@migrate.command("check", read_only=True)
@json_option("Print the report as JSON.")
@pass_context
def check_command(ctx: Context) -> None:
    """
    Ask every application the questions to answer before pointing DNS at this server.

    Run it on the new server after 'noust restore-server'. For each application: whether it
    runs, whether it answers the way a deploy's health gate asks, whether its certificate and
    its databases are there, and where each of its names resolves now. A name that still
    resolves to the old server is listed as pending, not as wrong. Exits 1 when an application
    is down.
    """
    report = ServerMigration(logger=ctx.logger).check()
    if ctx.json_output:
        _echo_json(report.to_dict())
    else:
        _show_check(ctx, report)
    if not report.ok:
        raise click.exceptions.Exit(1)


def register(group: click.Group) -> None:
    """
    Join ``noust server``: what :mod:`noust.cli.commands.server` calls.

    Args:
        group: The ``server`` group.
    """
    group.add_command(migrate)
