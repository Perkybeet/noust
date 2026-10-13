# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app promote`` and ``noust app promotion``: putting a release on the same
application in another environment.

    noust app promotion staging.shop.example.com
    noust app promote staging.shop.example.com --to shop.example.com

The release that serves in staging is copied to production's ``releases/`` and
activated there behind its health gate, after its own ``pre_deploy`` hooks:
nothing is built again. See :mod:`noust.deployers.promote`.
"""

from __future__ import annotations

import json
import sys
from contextlib import nullcontext
from typing import TYPE_CHECKING, cast

import click

from noust.cli.app import Context, NoustCommand, global_flags, json_option, pass_context
from noust.core.audit import record as audit_record
from noust.core.logger import Logger
from noust.core.teams import Scope, TeamStore, ownership_target
from noust.core.terminal_context import TerminalContext
from noust.deployers.app_clone import (
    DATABASE_CHOICES,
    apply_clone,
    deploy_through_job,
    plan_clone,
)
from noust.deployers.app_export import report_summary
from noust.deployers.helpers.layout import app_root
from noust.deployers.promote import plan_promotion, promote, promotion_targets
from noust.deployers.releases import ReleaseManager
from noust.validators.domain import validate_domain

if TYPE_CHECKING:
    from noust.web.jobs import JobContext


@click.command("promotion", cls=NoustCommand, read_only=True)
@click.argument("domain")
@json_option("Print the targets as JSON.")
@pass_context
def promotion_command(ctx: Context, domain: str) -> None:
    """List the applications a release of DOMAIN can be promoted to."""
    domain = validate_domain(domain)
    targets = promotion_targets(domain)
    rows = []
    for app in targets:
        active = ReleaseManager(app_root(app)).current()
        rows.append(
            {
                "domain": app.domain,
                "release": active.id if active else None,
                "commit": active.commit if active else None,
            }
        )
    if ctx.json_output:
        click.echo(json.dumps({"domain": domain, "targets": rows}))
        return
    if not rows:
        ctx.logger.info(
            f"{domain} has no sibling to promote to: an application of the same project, in "
            "another environment, built from the same repository, on releases."
        )
        return
    ctx.logger.table(
        ["Target", "Runs", "Commit"],
        [[r["domain"], r["release"] or "-", r["commit"] or "-"] for r in rows],
    )
    ctx.logger.info(f"Promote: noust app promote {domain} --to TARGET")


@click.command("promote", cls=NoustCommand)
@click.argument("domain")
@click.option("--to", "target", required=True, help="The application that receives the release.")
@click.option(
    "--release", "release_id", default=None, help="The release (default: the one that serves)."
)
@click.option(
    "--schema-changed-ok",
    is_flag=True,
    help="Go back past a deployment of the target that changed its database's schema.",
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask before promoting.")
@global_flags
@pass_context
def promote_command(
    ctx: Context,
    domain: str,
    target: str,
    release_id: str | None,
    schema_changed_ok: bool,
    yes: bool,
) -> None:
    """
    Put a release of DOMAIN on another application and activate it there.

    Nothing is built: the release is copied to the target's releases, linked to
    the target's own .env and persistent paths, its pre_deploy hooks run, and it
    is activated behind the target's health gate. A release that does not answer
    is put back to what served before.
    """
    plan = plan_promotion(domain, target, release_id)
    for warning in plan.warnings:
        ctx.logger.warning(warning)
    if not yes and not ctx.json_output:
        question = f"Put release {plan.release.id} of {plan.source.domain} on {plan.target.domain}?"
        if not click.confirm(question, default=False):
            ctx.logger.info("Cancelled")
            return
    outcome = promote(
        domain,
        target,
        release_id=plan.release.id,
        schema_changed_ok=schema_changed_ok,
        logger=ctx.logger,
    )
    audit_record(
        "apps.promote",
        target=f"app:{outcome.target}",
        details={"from": outcome.source, "release": outcome.release},
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "source": outcome.source,
                    "target": outcome.target,
                    "release": outcome.release,
                    "commit": outcome.commit,
                    "deployment_id": outcome.deployment_id,
                    "warnings": list(outcome.warnings),
                }
            )
        )
        return
    ctx.logger.success(f"{outcome.target} runs release {outcome.release}")


@click.command("clone", cls=NoustCommand)
@click.argument("domain")
@click.argument("new_domain", metavar="NEW_DOMAIN")
@click.option(
    "--database",
    type=click.Choice(DATABASE_CHOICES),
    default=None,
    help="What the clone does with the application's database: 'empty' makes one of its "
    "own, 'share' uses the original's (and writes into its data). Required when the "
    "application has a database.",
)
@click.option("--source", default=None, help="Deploy the clone from this source instead.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before cloning.")
@click.option("--team", default=None, help="The team it goes to (default: the original's).")
@click.option("--project", default=None, help="The project it goes to (default: the original's).")
@click.option(
    "--environment", default=None, help="The environment it goes to (default: the original's)."
)
@global_flags
@json_option("Print what was applied as JSON.")
@pass_context
def clone_command(
    ctx: Context,
    domain: str,
    new_domain: str,
    database: str | None,
    source: str | None,
    yes: bool,
    team: str | None,
    project: str | None,
    environment: str | None,
) -> None:
    """
    Clone an application: the same application on NEW_DOMAIN.

    Everything its export carries is copied, secrets included, except the names
    the original answers on, plus its tags, build block, deploy hooks and how its
    previews are made. The clone is built and gated like any application. It goes
    to the original's project and environment unless --project, --environment or
    --team say otherwise. To put a copy on another server, export the application
    there ('noust app export --with-secrets') and import it on the other.
    """
    plan = plan_clone(domain, new_domain, database=database, source=source)
    ids: tuple[int | None, int | None]
    if team or project or environment:
        target = TeamStore().resolve_target(
            Scope.everything(), team=team, project=project, environment=environment
        )
        ids = (target.project_id, target.environment_id)
    else:
        ids = (plan.source.project_id, plan.source.environment_id)
    if not ctx.json_output:
        ctx.logger.key_value("Clone", f"{plan.source.domain} -> {plan.plan.domain}")
        for line in (*plan.extras,):
            ctx.logger.list_item(line)
        for line in plan.omitted:
            ctx.logger.list_item(f"not copied: {line}")
    if ctx.dry_run:
        ctx.logger.info("Rehearsal: nothing was created")
        return
    if not yes and not ctx.json_output and not click.confirm(f"Clone {domain} as {new_domain}?"):
        ctx.logger.info("Cancelled")
        return
    # In JSON mode what is printed on stdout is the result alone; progress goes to stderr.
    log = Logger(verbose=ctx.verbose, stream=sys.stderr) if ctx.json_output else ctx.logger
    context = cast("JobContext", TerminalContext(log))
    with ownership_target(ids[0] or 0, ids[1] or 0) if all(ids) else nullcontext():
        report = apply_clone(plan, deploy=deploy_through_job(context), logger=log)
    audit_record(
        "apps.clone",
        target=f"app:{new_domain}",
        details={"from": domain, "database": plan.database},
    )
    if ctx.json_output:
        click.echo(json.dumps(report_summary(report)))
        return
    ctx.logger.success(f"{new_domain} is a clone of {domain}")
    for step in report.not_applied:
        ctx.logger.warning(f"not applied: {step.part}: {step.detail}")
