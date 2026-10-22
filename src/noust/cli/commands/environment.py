# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust environment`` command group: the stages of a project.

Production, staging, preview... An environment belongs to a project, and what
lives in it (applications, databases, sites) is told apart from the same
project's other stages. Every project starts with ``production``. ``noust env``
is taken (an application's variables), which is why this is spelled out.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record, say, teams
from noust.core.teams import DEFAULT_SLUG


@click.group("environment", cls=NoustGroup)
def cli() -> None:
    """Manage the environments of the projects."""


@cli.command("list", read_only=True)
@click.option("--team", default=None, help="Only this team's environments.")
@click.option("--project", default=None, help="Only this project's environments.")
@json_option("Print the environments as JSON.")
@pass_context
def list_command(ctx: Context, team: str | None, project: str | None) -> None:
    """List environments, with what each holds."""
    store = teams()
    if team is not None:
        store.require_team(team)
    rows = [
        {**environment.to_dict(), "resources": store.resource_counts(environment_id=environment.id)}
        for environment in store.list_environments(team=team, project=project)
    ]
    if ctx.json_output:
        click.echo(json.dumps({"environments": rows}))
        return
    ctx.logger.table(
        ["Team", "Project", "Environment", "Name", "Applications"],
        [
            [row["team"], row["project"], row["slug"], row["name"], str(row["resources"]["apps"])]
            for row in rows
        ],
    )


@cli.command("create")
@click.argument("name")
@click.option("--team", default=None, help="The team (default: the default team).")
@click.option("--project", default=DEFAULT_SLUG, show_default=True, help="The project.")
@click.option("--slug", default=None, help="Its short name; made from NAME when omitted.")
@pass_context
def create_command(
    ctx: Context, name: str, team: str | None, project: str, slug: str | None
) -> None:
    """Create an environment in a project."""
    environment = teams().create_environment(
        team or DEFAULT_SLUG, project, name, slug=slug, created_by="cli"
    )
    record(
        "environment.create",
        f"environment:{environment.team}/{environment.project}/{environment.slug}",
    )
    say(
        ctx,
        {"environment": environment.to_dict()},
        f"Environment created: {environment.team}/{environment.project}/{environment.slug}",
    )


@cli.command("rename")
@click.argument("environment")
@click.argument("name")
@click.option("--team", default=None, help="The team (default: the default team).")
@click.option("--project", default=DEFAULT_SLUG, show_default=True, help="The project.")
@pass_context
def rename_command(
    ctx: Context, environment: str, name: str, team: str | None, project: str
) -> None:
    """Change an environment's display name; the short name commands use stays."""
    renamed = teams().rename_environment(team or DEFAULT_SLUG, project, environment, name)
    record(
        "environment.update",
        f"environment:{renamed.team}/{renamed.project}/{renamed.slug}",
        {"name": renamed.name},
    )
    say(
        ctx,
        {"environment": renamed.to_dict()},
        f"Environment renamed: {renamed.team}/{renamed.project}/{renamed.slug} is now {renamed.name}",
    )


@cli.command("delete")
@click.argument("environment")
@click.option("--team", default=None, help="The team (default: the default team).")
@click.option("--project", default=DEFAULT_SLUG, show_default=True, help="The project.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def delete_command(
    ctx: Context, environment: str, team: str | None, project: str, yes: bool
) -> None:
    """
    Delete an environment that holds nothing.

    The last environment of a project, and one that still has applications,
    databases, sites or backup schedules, are refused.
    """
    owner = team or DEFAULT_SLUG
    found = teams().require_environment(owner, project, environment)
    if not yes and not ctx.json_output:
        click.confirm(
            f"Delete the environment {found.team}/{found.project}/{found.slug}?", abort=True
        )
    removed = teams().delete_environment(owner, project, environment)
    record("environment.delete", f"environment:{removed.team}/{removed.project}/{removed.slug}")
    say(
        ctx,
        {"environment": removed.to_dict()},
        f"Environment deleted: {removed.team}/{removed.project}/{removed.slug}",
    )
