# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust workspace``: a monorepo as one application per workspace.

``detect`` reads a repository's workspaces (npm, Yarn, pnpm, Lerna, Nx, Turborepo) and says which
are applications and which are libraries, with what each would watch and build. ``create`` makes
one application of each workspace you name, on the releases layout, through the same path
``noust app import`` takes: each is built, health-gated and recorded like any deployment, with
its own releases, rollback and previews, and a push deploys only the applications whose files it
touched. Both are :mod:`noust.deployers.workspaces`, which ``/api/workspaces`` calls too.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.cli.commands.app import run_import
from noust.core.exceptions import ValidationError
from noust.deployers.app_export import plan_import
from noust.deployers.workspaces import (
    Workspace,
    WorkspaceReport,
    inspect_workspaces,
    workspace_document,
)
from noust.validators.domain import validate_domain


@click.group("workspace", cls=NoustGroup)
def cli() -> None:
    """A monorepo as one application per workspace."""


def _print_report(ctx: Context, report: WorkspaceReport) -> None:
    """
    Show the workspaces of a repository.

    Args:
        ctx: The command's context.
        report: What was found.
    """
    logger = ctx.logger
    logger.key_value("Workspaces", f"{report.tool} ({report.package_manager})")
    for item in report.workspaces:
        if item.deployable:
            logger.list_item(f"{item.directory}  {item.name}  [{item.app_type}]")
            if item.build:
                logger.info(f"    builds with: {' '.join(item.build)}")
            logger.info(f"    watches: {', '.join(item.watch_paths)}")
        else:
            logger.list_item(f"{item.directory}  {item.name}  (not an application: {item.reason})")
    for problem in report.problems:
        logger.warning(problem)


@cli.command("detect", read_only=True)
@click.argument("source")
@click.option("--branch", help="The branch to read; the repository's default otherwise.")
@json_option("Print the workspaces as JSON.")
@pass_context
def detect_command(ctx: Context, source: str, branch: str | None) -> None:
    """Find the workspaces of a repository and which of them are applications."""
    report = inspect_workspaces(source, branch=branch)
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict()))
        return
    if report.tool == "none":
        ctx.logger.info("This repository declares no workspaces.")
        for problem in report.problems:
            ctx.logger.warning(problem)
        return
    _print_report(ctx, report)
    if report.deployable:
        ctx.logger.info(
            "Create an application from each with: noust workspace create SOURCE "
            "--app WORKSPACE=DOMAIN"
        )


@cli.command("create")
@click.argument("source")
@click.option(
    "--app",
    "pairs",
    multiple=True,
    required=True,
    metavar="WORKSPACE=DOMAIN",
    help="A workspace (its name or its directory) and the domain to deploy it on. Repeat.",
)
@click.option("--branch", help="The branch to deploy; the repository's default otherwise.")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Do not ask before applying what a plan runs as root (cron jobs, previews).",
)
@global_flags
@json_option("Print each application's plan and result as JSON.")
@pass_context
def create_command(
    ctx: Context, source: str, pairs: tuple[str, ...], branch: str | None, yes: bool
) -> None:
    """
    Deploy workspaces of a monorepo, one application each.

    Each application is the workspace as its base directory, on releases, with the files it
    depends on as its watch paths and, in a Turborepo, the build of the workspace and what it
    needs. With --dry-run, only the plans are shown.
    """
    report = inspect_workspaces(source, branch=branch)
    if report.tool == "none" or not report.workspaces:
        raise ValidationError(
            "This repository declares no workspaces",
            details="Workspaces are listed in package.json, pnpm-workspace.yaml or lerna.json. "
            "For a project in one directory, use: noust create --build-file with base_directory.",
        )
    chosen: list[tuple[Workspace, str]] = []
    for pair in pairs:
        key, separator, domain = pair.partition("=")
        if not separator or not key.strip() or not domain.strip():
            raise click.UsageError(f"--app wants WORKSPACE=DOMAIN, not {pair!r}")
        found = report.named(key.strip())
        if found is None:
            available = ", ".join(item.directory for item in report.deployable) or "none"
            raise ValidationError(
                f"There is no workspace {key.strip()!r} in this repository",
                details=f"Applications here: {available}. Name one by its directory or its name.",
                field="app",
            )
        target = validate_domain(domain.strip())
        if any(target == other for _item, other in chosen):
            raise click.UsageError(f"{target} is given for two workspaces")
        chosen.append((found, target))

    plans = []
    for item, domain in chosen:
        document = workspace_document(report, item, domain=domain, source=source, branch=branch)
        plans.append(plan_import(document, domain=domain))
    # One application after the other, each through the path an import takes; a failure stops
    # the rest, and what was created before it is in the log and `noust list`. With --json each
    # application prints its own object.
    for plan in plans:
        if not ctx.json_output:
            ctx.logger.info(f"--- {plan.domain}")
        run_import(ctx, plan, yes=yes)
