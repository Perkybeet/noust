# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app reclaim``: hand a Compose stack running outside its unit back to it (item 73).

Someone ran ``docker compose up -d`` by hand: the containers serve, the unit
is stopped, and neither a reboot nor Noust brings the stack back. The work is
:mod:`noust.deployers.compose_reclaim`; this module shows what was found -
the containers that run, what a rehearsed ``up`` says - and asks before
enabling and starting the unit.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.logger import Logger
from noust.deployers.compose_reclaim import ReclaimPlan, plan_reclaim, reclaim


def _show(logger: Logger, plan: ReclaimPlan) -> None:
    """
    Print what handing the stack back would do.

    Args:
        logger: Where it is printed.
        plan: The plan.
    """
    logger.key_value("Application", plan.domain)
    logger.key_value("Running outside its unit", ", ".join(plan.containers))
    logger.key_value("Project", plan.project or "(named by the stack)")
    logger.key_value("Rehearsed up", "changes nothing" if not plan.changes else "changes:")
    for change in plan.changes:
        logger.substep(change)
    boot = "already enabled" if plan.enabled else "enabled to start at boot"
    logger.key_value("Unit", f"{plan.unit}.service, {boot}, then started")


@click.command("reclaim", cls=NoustCommand)
@click.argument("domain")
@click.option(
    "--accept-recreate",
    is_flag=True,
    help="Hand it back even if starting the unit would recreate a container.",
)
@click.option("--yes", "-y", is_flag=True, help="Hand it back without asking.")
@json_option("Print what was done as JSON.")
@pass_context
def reclaim_command(ctx: Context, domain: str, accept_recreate: bool, yes: bool) -> None:
    """
    Hand a Docker Compose stack that runs outside its unit back to Noust.

    For a stack whose containers were brought up by hand while its unit is
    stopped: the unit is enabled, so a reboot brings the stack back, and
    started, so Noust supervises it again. 'docker compose up --dry-run' must
    show the start would recreate nothing, unless --accept-recreate.
    """
    plan = plan_reclaim(domain, accept_recreate=accept_recreate)
    if not ctx.json_output:
        _show(ctx.logger, plan)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(f"Hand {plan.domain} back to its unit as shown?", default=False, abort=True)
    result = reclaim(plan)
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    if not result.reclaimed:
        ctx.logger.info("Rehearsal: nothing was changed.")
        return
    ctx.logger.success(
        f"{plan.domain} runs under {plan.unit}.service again; it starts at boot and Noust "
        "supervises it."
    )
