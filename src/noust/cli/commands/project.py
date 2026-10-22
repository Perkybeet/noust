# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust project`` command group: what a team's applications are grouped into.

A project holds applications, databases and sites, in one or more environments
(``noust environment``). It belongs to a team; its slug is unique inside it.
Moving an application between projects is ``noust app move``.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record, say, teams
from noust.core.teams import DEFAULT_SLUG


def _team_or_default(team: str | None) -> str:
    """
    Args:
        team: The ``--team`` option.

    Returns:
        The team named, or the default team.
    """
    return team or DEFAULT_SLUG


@click.group("project", cls=NoustGroup)
def cli() -> None:
    """Manage the projects of the teams."""


@cli.command("list", read_only=True)
@click.option("--team", default=None, help="Only this team's projects.")
@json_option("Print the projects as JSON.")
@pass_context
def list_command(ctx: Context, team: str | None) -> None:
    """List projects, with their environments and what they hold."""
    store = teams()
    if team is not None:
        store.require_team(team)
    environments: dict[int, list[str]] = {}
    for environment in store.list_environments():
        environments.setdefault(environment.project_id, []).append(environment.slug)
    rows = [
        {
            **project.to_dict(),
            "environments": environments.get(project.id, []),
            "resources": store.resource_counts(project_id=project.id),
        }
        for project in store.list_projects(team=team)
    ]
    if ctx.json_output:
        click.echo(json.dumps({"projects": rows}))
        return
    ctx.logger.table(
        ["Team", "Project", "Name", "Environments", "Applications"],
        [
            [
                row["team"],
                row["slug"],
                row["name"],
                ", ".join(row["environments"]) or "-",
                str(row["resources"]["apps"]),
            ]
            for row in rows
        ],
    )


@cli.command("create")
@click.argument("name")
@click.option("--team", default=None, help="The team it belongs to (default: the default team).")
@click.option("--slug", default=None, help="Its short name; made from NAME when omitted.")
@pass_context
def create_command(ctx: Context, name: str, team: str | None, slug: str | None) -> None:
    """Create a project in a team, with its production environment."""
    project = teams().create_project(_team_or_default(team), name, slug=slug, created_by="cli")
    record("project.create", f"project:{project.team}/{project.slug}")
    say(
        ctx,
        {"project": project.to_dict()},
        f"Project created: {project.team}/{project.slug} (environment: production)",
    )


@cli.command("rename")
@click.argument("project")
@click.argument("name")
@click.option("--team", default=None, help="The team it belongs to (default: the default team).")
@pass_context
def rename_command(ctx: Context, project: str, name: str, team: str | None) -> None:
    """Change a project's display name; the short name commands use stays."""
    renamed = teams().rename_project(_team_or_default(team), project, name)
    record("project.update", f"project:{renamed.team}/{renamed.slug}", {"name": renamed.name})
    say(
        ctx,
        {"project": renamed.to_dict()},
        f"Project renamed: {renamed.team}/{renamed.slug} is now {renamed.name}",
    )


@cli.command("delete")
@click.argument("project")
@click.option("--team", default=None, help="The team it belongs to (default: the default team).")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def delete_command(ctx: Context, project: str, team: str | None, yes: bool) -> None:
    """
    Delete a project that holds nothing, with its environments.

    A project that still has applications, databases, sites or backup
    schedules is refused: move them first ('noust app move').
    """
    chosen = _team_or_default(team)
    found = teams().require_project(chosen, project)
    if not yes and not ctx.json_output:
        click.confirm(f"Delete the project {found.team}/{found.slug}?", abort=True)
    removed = teams().delete_project(found.team, found.slug)
    record("project.delete", f"project:{removed.team}/{removed.slug}")
    say(ctx, {"project": removed.to_dict()}, f"Project deleted: {removed.team}/{removed.slug}")
