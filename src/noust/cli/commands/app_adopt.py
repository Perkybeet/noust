# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app adopt``: register a Docker Compose stack that already runs, as it is.

The work is :mod:`noust.deployers.compose_adopt`; this module shows what the
adoption found - the project the containers run under, the site that serves
the domain, what a rehearsed ``up`` says - and asks before recording it.
"""

from __future__ import annotations

import json
from pathlib import Path

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.logger import Logger
from noust.deployers import compose_adopt
from noust.deployers.compose_adopt import AdoptionPlan

_PROJECT_FROM = {
    "containers": "the label of its running containers",
    "compose": "the name Compose gives it; nothing from it has run",
    "stack": "named by the stack itself",
}


def _show(logger: Logger, plan: AdoptionPlan) -> None:
    """
    Print what an adoption would record.

    Args:
        logger: Where it is printed.
        plan: The plan.
    """
    logger.key_value("Application", f"{plan.domain} (unit {plan.app_name})")
    logger.key_value("Directory", f"{plan.app_path} (kept as it is, nothing fetched)")
    logger.key_value("Compose file", plan.compose_file)
    logger.key_value(
        "Project", f"{plan.project or '-'} ({_PROJECT_FROM.get(plan.project_from, '')})"
    )
    logger.key_value("Containers", ", ".join(plan.containers) or "none")
    logger.key_value("Updates from", f"{plan.source} ({plan.branch or 'current branch'})")
    logger.key_value("Commit", plan.commit or "unknown")
    if plan.headless:
        logger.key_value("Site", "none: the stack publishes no port")
    elif plan.site is None:
        logger.key_value("Site", "none found")
    else:
        recorded = f", recorded for {plan.domain}" if plan.site_name else ""
        logger.key_value("Site", f"{plan.site} (the operator's, never rewritten{recorded})")
    logger.key_value("Port", str(plan.port) if plan.port else "none")
    logger.key_value("Rehearsed up", "changes nothing" if not plan.changes else "changes:")
    for change in plan.changes:
        logger.substep(change)
    logger.key_value("Unit", f"{plan.app_name}.service, created and enabled, not started")
    for warning in plan.warnings:
        logger.warning(warning)


@click.command("adopt", cls=NoustCommand)
@click.argument("domain")
@click.option(
    "--path",
    "path",
    required=True,
    type=click.Path(path_type=Path),
    help="The directory the stack runs from, such as /opt/proggest.",
)
@click.option(
    "--compose-file",
    default=None,
    help="Its compose file, relative to --path (found among the usual names otherwise).",
)
@click.option("--source", default=None, help="Where updates fetch from (default: origin).")
@click.option("--branch", default=None, help="The branch updates follow (default: the current).")
@click.option(
    "--site", default=None, help="The site file serving the domain (found by its names otherwise)."
)
@click.option(
    "--port",
    type=click.IntRange(1, 65535),
    default=None,
    help="The port to register (the compose file's web port otherwise).",
)
@click.option(
    "--accept-recreate",
    is_flag=True,
    help="Adopt even if starting the stack as Noust would recreate something.",
)
@click.option("--yes", "-y", is_flag=True, help="Adopt without asking.")
@json_option("Print what was adopted as JSON.")
@pass_context
def adopt_command(
    ctx: Context,
    domain: str,
    path: Path,
    compose_file: str | None,
    source: str | None,
    branch: str | None,
    site: str | None,
    port: int | None,
    accept_recreate: bool,
    yes: bool,
) -> None:
    """
    Register a Docker Compose stack that already runs, without touching it.

    Nothing is cloned, cleaned, rebuilt or restarted. The project is read from
    the running containers and kept; 'docker compose up --dry-run' must show
    nothing would be recreated; the site serving DOMAIN is found and left as
    it is; a unit is created and enabled, not started.
    """
    plan = compose_adopt.plan_adoption(
        domain,
        path,
        compose_file=compose_file,
        source=source,
        branch=branch,
        site=site,
        port=port,
        accept_recreate=accept_recreate,
        web=compose_adopt._default_web(),
    )
    if not ctx.json_output:
        _show(ctx.logger, plan)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(f"Adopt {plan.domain} as shown?", default=False, abort=True)
    result = compose_adopt.adopt(plan)
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    if not result.adopted:
        ctx.logger.info("Rehearsal: nothing was adopted.")
        return
    ctx.logger.success(
        f"{plan.domain} adopted. Update it with: noust update {plan.domain}; its unit "
        f"{plan.app_name} starts it at boot."
    )
