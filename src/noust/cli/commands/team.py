# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust team`` command group: who works together, and on what.

A team groups people and the projects they run. Every application, database,
site and backup schedule belongs to one project of one team, and a person sees
the teams they belong to (``noust user`` makes the people, this makes the
teams). A team is a boundary of permissions and of organisation, not of
execution: the applications of two teams still share this server's kernel.

``noust env`` is taken (it is the variables of an application's ``.env``), so
the stages of a project are ``noust environment``.
"""

from __future__ import annotations

import json
from datetime import datetime

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record, say, teams
from noust.core.teams.model import TEAM_ROLE_VIEWER, TEAM_ROLES


def _when(stamp: float) -> str:
    """
    Args:
        stamp: A UNIX timestamp.

    Returns:
        The date, for a table.
    """
    return datetime.fromtimestamp(stamp).isoformat(sep=" ", timespec="minutes")


@click.group("team", cls=NoustGroup)
def cli() -> None:
    """Manage teams, and the people in them."""


@cli.command("list", read_only=True)
@json_option("Print the teams as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the teams, with how many people and projects each has."""
    store = teams()
    rows = [
        {
            **team.to_dict(),
            "members": len(store.list_members(team.slug)),
            "projects": len(store.list_projects(team=team.slug)),
            "resources": store.resource_counts(team_id=team.id),
        }
        for team in store.list_teams()
    ]
    if ctx.json_output:
        click.echo(json.dumps({"teams": rows}))
        return
    ctx.logger.table(
        ["Team", "Name", "Members", "Projects", "Applications", "Created"],
        [
            [
                row["slug"],
                row["name"],
                str(row["members"]),
                str(row["projects"]),
                str(row["resources"]["apps"]),
                _when(row["created_at"]),
            ]
            for row in rows
        ],
    )


@cli.command("create")
@click.argument("name")
@click.option("--slug", default=None, help="Its short name; made from NAME when omitted.")
@pass_context
def create_command(ctx: Context, name: str, slug: str | None) -> None:
    """
    Create a team, with a default project and its production environment.

    NAME is what people read; the slug is what is typed in commands and URLs.
    """
    team = teams().create_team(name, slug=slug, created_by="cli")
    record("team.create", f"team:{team.slug}", {"name": team.name})
    say(ctx, {"team": team.to_dict()}, f"Team created: {team.slug} ({team.name})")


@cli.command("rename")
@click.argument("team")
@click.argument("name")
@pass_context
def rename_command(ctx: Context, team: str, name: str) -> None:
    """Change the name a team is shown as; its slug stays."""
    renamed = teams().rename_team(team, name)
    record("team.update", f"team:{renamed.slug}", {"name": renamed.name})
    say(ctx, {"team": renamed.to_dict()}, f"Team renamed: {renamed.slug} is now {renamed.name}")


@cli.command("delete")
@click.argument("team")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def delete_command(ctx: Context, team: str, yes: bool) -> None:
    """
    Delete a team that holds nothing.

    Its projects, environments and memberships go with it. The default team,
    and a team that still has applications, databases, sites or backup
    schedules, are refused: move them first ('noust app move').
    """
    found = teams().require_team(team)
    if not yes and not ctx.json_output:
        click.confirm(f"Delete the team {found.slug}?", abort=True)
    removed = teams().delete_team(found.slug)
    record("team.delete", f"team:{removed.slug}")
    say(ctx, {"team": removed.to_dict()}, f"Team deleted: {removed.slug}")


@cli.command("members", read_only=True)
@click.argument("team")
@json_option("Print the members as JSON.")
@pass_context
def members_command(ctx: Context, team: str) -> None:
    """List a team's members and their role in it."""
    members = teams().list_members(team)
    if ctx.json_output:
        click.echo(json.dumps({"team": team, "members": [m.to_dict() for m in members]}))
        return
    if not members:
        ctx.logger.info(f"Nobody belongs to {team} yet. Add someone with 'noust team add-member'.")
        return
    ctx.logger.table(
        ["Account", "Role in the team", "Role on the server", "Since"],
        [[m.username, m.role, m.instance_role, _when(m.created_at)] for m in members],
    )


@cli.command("add-member")
@click.argument("team")
@click.argument("username")
@click.option(
    "--role",
    type=click.Choice(TEAM_ROLES),
    default=TEAM_ROLE_VIEWER,
    show_default=True,
    help="The role in the team: viewer reads, operator also deploys, admin also changes, "
    "owner also governs the team.",
)
@pass_context
def add_member_command(ctx: Context, team: str, username: str, role: str) -> None:
    """
    Make an account a member of a team, or change its role there.

    Security officers and auditors work across every team and never join one.
    A person who has no account yet is invited into the team with
    'noust user invite NAME --team TEAM'.
    """
    member = teams().add_member(team, username, role, created_by="cli")
    record(
        "team.member.add", f"team:{member.team}", {"account": member.username, "role": member.role}
    )
    say(
        ctx,
        {"member": member.to_dict()},
        f"{member.username} is {member.role} of the team {member.team}",
    )


@cli.command("remove-member")
@click.argument("team")
@click.argument("username")
@pass_context
def remove_member_command(ctx: Context, team: str, username: str) -> None:
    """
    Take an account out of a team.

    Their sessions and tokens stop seeing the team's resources at their next
    request. The last owner of a team cannot leave it.
    """
    member = teams().remove_member(team, username)
    record("team.member.remove", f"team:{member.team}", {"account": member.username})
    say(
        ctx,
        {"member": member.to_dict()},
        f"{member.username} is no longer a member of the team {member.team}",
    )
