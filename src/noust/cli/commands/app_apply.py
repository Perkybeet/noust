# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app apply``: set an application from a declaration file.

The file is the one ``noust app export --as-yaml`` writes: the health check, limits, release
retention, source options, build block, hooks, non-secret variables, domains and tasks of one
application, in the keys ``noust.yaml`` already uses. Only the sections the file has are touched;
what runs commands (build, hooks, tasks, Compose arguments) is shown and asked for first. The
work is :mod:`noust.deployers.project_apply`, which ``POST /api/apps/{d}/apply`` calls too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustCommand, global_flags, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.deployers.project_apply import (
    ApplyPlan,
    ApplyReport,
    apply_declaration,
    parse_declaration,
    plan_declaration,
)
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _print_plan(ctx: Context, plan: ApplyPlan) -> None:
    """
    Show what applying would change.

    Args:
        ctx: The command's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Application", plan.domain)
    for change in plan.changes:
        logger.list_item(
            change.description + ("  [runs commands]" if change.root_equivalent else "")
        )
    if not plan.changes:
        logger.info("Nothing to change: the application already is as declared.")
    if plan.unchanged:
        logger.info(f"Already as declared: {', '.join(plan.unchanged)}")
    for note in plan.notes:
        logger.warning(note)


def _print_report(ctx: Context, report: ApplyReport) -> None:
    """
    Show what applying did, what it did not last.

    Args:
        ctx: The command's context.
        report: The report.
    """
    missing = report.not_applied
    if not missing:
        ctx.logger.success(f"Applied the declaration to {report.domain}")
        return
    ctx.logger.warning(f"{len(missing)} part(s) were not applied:")
    for step in missing:
        ctx.logger.list_item(f"{step.part}: {step.detail}")


# Only --check reads; applying changes the application, so it is recorded like any other change.
@click.command("apply", cls=NoustCommand, read_only=lambda params: bool(params["check"]))
@click.argument("domain")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--prune",
    is_flag=True,
    help="Also remove the aliases, redirects and tasks the file does not name.",
)
@click.option("--check", is_flag=True, help="Show what would change and change nothing.")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Do not ask before applying what runs commands (build, hooks, tasks, Compose arguments).",
)
@global_flags
@json_option("Print the plan and what was applied as JSON.")
@pass_context
def apply_command(
    ctx: Context, domain: str, file: Path, prune: bool, check: bool, yes: bool
) -> None:
    """
    Set an application from a declaration file, as 'noust app export --as-yaml' writes one.

    Only the sections the file has are changed. Variables are set, never removed, and secrets
    are refused: the file is meant to be committed. A build block, hooks, tasks or Compose
    arguments run commands as the application or on the machine, so they are listed and
    confirmed first (--yes skips the question; without a terminal it is required).
    """
    validated = validate_domain(domain)
    plan = plan_declaration(
        validated, parse_declaration(file.read_text(encoding="utf-8")), prune=prune
    )
    if check or ctx.dry_run:
        if ctx.json_output:
            click.echo(json.dumps({"plan": plan.to_dict(), "rehearsed": True}))
            return
        _print_plan(ctx, plan)
        ctx.logger.info("Rehearsal: nothing was changed")
        return
    if not ctx.json_output:
        _print_plan(ctx, plan)
    if plan.confirm and not yes:
        reasons = "; ".join(plan.confirm)
        if ctx.json_output or not (sys.stdin.isatty() and sys.stdout.isatty()):
            raise ValidationError(
                f"The declaration needs confirming: it sets {reasons}",
                details="Review the plan with --check, then apply again with --yes.",
                field="confirm",
            )
        click.confirm(f"Apply it to {validated}? It sets {reasons}", abort=True)
    report = apply_declaration(plan, actor=_actor(), logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps({"plan": plan.to_dict(), "result": report.to_dict()}))
        return
    _print_report(ctx, report)
