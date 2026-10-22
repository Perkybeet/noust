# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust access`` command group: who may do what, and where.

A role is held at a scope: the whole server, a team, a project or an
environment (I-03). The same person is admin of one project and only reads
another. ``noust access role`` defines roles of your own as sets of the
permissions that exist (I-07); ``noust access grant`` gives a built-in or a
custom role at a scope; ``noust token create --project`` binds a token to one.

A thin front end over :class:`~noust.web.permissions.access.AccessManager`, the
manager the console's ``/api/access`` routes call, so every rule (separation of
duties, what a project can own, the ENS profile's one role per account) is the
same one. It runs as root and what it does is on the audit trail.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record
from noust.cli.web_state import access_manager, permission_descriptions
from noust.core.access import SCOPE_KINDS, Binding


def _when(stamp: float) -> str:
    """
    Args:
        stamp: A UNIX timestamp.

    Returns:
        The date, for a table.
    """
    return datetime.fromtimestamp(stamp).isoformat(sep=" ", timespec="minutes")


def _where(binding: Binding) -> str:
    """
    Args:
        binding: A binding.

    Returns:
        Its scope as people write it: ``server``, ``acme``, ``acme/web``...
    """
    return binding.scope or "server"


@click.group("access", cls=NoustGroup)
def cli() -> None:
    """Roles, and where each person holds them."""


@cli.group("role", cls=NoustGroup)
def role_group() -> None:
    """Define roles of your own, as sets of permissions."""


@role_group.command("list", read_only=True)
@json_option("Print the roles as JSON.")
@pass_context
def role_list(ctx: Context) -> None:
    """List the roles that can be given: a team's built-in ones and yours."""
    catalog = access_manager().catalog()
    built_in: list[dict[str, Any]] = catalog["built_in"]  # type: ignore[assignment]
    custom: list[dict[str, Any]] = catalog["custom"]  # type: ignore[assignment]
    if ctx.json_output:
        click.echo(json.dumps({"built_in": built_in, "custom": custom}))
        return
    ctx.logger.table(
        ["Role", "Kind", "Held on", "Permissions"],
        [
            [
                item["name"],
                "built in",
                ", ".join(item["scopes"]),
                str(len(item["permissions"])),
            ]
            for item in built_in
        ]
        + [[item["name"], "custom", "any scope", str(len(item["permissions"]))] for item in custom],
    )


@role_group.command("show", read_only=True)
@click.argument("name")
@json_option("Print the role as JSON.")
@pass_context
def role_show(ctx: Context, name: str) -> None:
    """Show what a role holds, permission by permission."""
    manager = access_manager()
    catalog = manager.catalog()
    built_in = {item["name"]: item for item in catalog["built_in"]}  # type: ignore[attr-defined]
    if name in built_in:
        item = built_in[name]
    else:
        item = manager.data.require_role(name).to_dict()
    if ctx.json_output:
        click.echo(json.dumps(item))
        return
    ctx.logger.info(f"{item['name']}: {item.get('description') or 'a built-in role of a team'}")
    descriptions = permission_descriptions()
    for permission in item["permissions"]:
        ctx.logger.info(f"  {permission}  {descriptions.get(permission, '')}")


@role_group.command("create")
@click.argument("name")
@click.option(
    "--permission",
    "permissions",
    multiple=True,
    required=True,
    help="A permission the role holds (repeat). 'noust access permissions' lists them.",
)
@click.option("--label", default=None, help="What the console shows; the name by default.")
@click.option("--description", default=None, help="What the role is for.")
@pass_context
def role_create(
    ctx: Context,
    name: str,
    permissions: tuple[str, ...],
    label: str | None,
    description: str | None,
) -> None:
    """
    Define a role.

    A role cannot govern access and run the server at once (security.manage,
    accounts.manage or audit.manage together with deploying, operating or
    managing): split it in two, as the built-in roles are.
    """
    role = access_manager().create_role(
        name, permissions, label=label, description=description, actor="cli"
    )
    record("access.role.create", f"role:{role.name}", {"permissions": list(role.permissions)})
    ctx.logger.success(f"Role created: {role.name} ({len(role.permissions)} permissions)")
    if ctx.json_output:
        click.echo(json.dumps({"role": role.to_dict()}))


@role_group.command("set")
@click.argument("name")
@click.option(
    "--permission", "permissions", multiple=True, help="Replace its permissions (repeat)."
)
@click.option("--label", default=None, help="Its new label.")
@click.option("--description", default=None, help="Its new description.")
@pass_context
def role_set(
    ctx: Context,
    name: str,
    permissions: tuple[str, ...],
    label: str | None,
    description: str | None,
) -> None:
    """Change a role; everyone who holds it holds the change at their next request."""
    role = access_manager().update_role(
        name,
        permissions=permissions or None,
        label=label,
        description=description,
        actor="cli",
    )
    record("access.role.update", f"role:{role.name}", {"permissions": list(role.permissions)})
    ctx.logger.success(f"Role updated: {role.name}")


@role_group.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def role_remove(ctx: Context, name: str, yes: bool) -> None:
    """Remove a role nobody holds."""
    if not yes and not ctx.json_output:
        click.confirm(f"Remove the role {name}?", abort=True)
    role = access_manager().delete_role(name)
    record("access.role.delete", f"role:{role.name}")
    ctx.logger.success(f"Role removed: {role.name}")


@cli.command("permissions", read_only=True)
@json_option("Print the permissions as JSON.")
@pass_context
def permissions_command(ctx: Context) -> None:
    """List every permission a role can hold, and what it allows."""
    permissions: dict[str, str] = access_manager().catalog()["permissions"]  # type: ignore[assignment]
    if ctx.json_output:
        click.echo(json.dumps({"permissions": permissions}))
        return
    ctx.logger.table(["Permission", "Allows"], [[name, text] for name, text in permissions.items()])


@cli.command("list", read_only=True)
@click.option("--user", "username", default=None, help="Only this account's.")
@click.option("--team", default=None, help="Only what is held in this team.")
@json_option("Print the bindings as JSON.")
@pass_context
def list_command(ctx: Context, username: str | None, team: str | None) -> None:
    """List who holds a role where, besides each account's own role and team memberships."""
    manager = access_manager()
    found = manager.bindings_in_team(team) if team else manager.list_bindings(username)
    if team and username:
        found = [b for b in found if b.username == username.strip().lower()]
    if ctx.json_output:
        click.echo(json.dumps({"bindings": [b.to_dict() for b in found]}))
        return
    if not found:
        ctx.logger.info("No role has been given at a scope. Give one with 'noust access grant'.")
        return
    ctx.logger.table(
        ["ID", "Account", "Role", "Held on", "Where", "Since"],
        [
            [str(b.id), b.username, b.role, b.scope_kind, _where(b), _when(b.created_at)]
            for b in found
        ],
    )


@cli.command("grant")
@click.argument("username")
@click.option("--role", required=True, help="A built-in team role or one you defined.")
@click.option("--team", default=None, help="The team.")
@click.option("--project", default=None, help="The project of that team.")
@click.option("--env", "environment", default=None, help="The environment of that project.")
@click.option(
    "--scope",
    type=click.Choice(SCOPE_KINDS),
    default=None,
    help="Where; the narrowest of --team, --project and --env by default. 'instance' is the "
    "whole server (a role you defined, added to the account's own).",
)
@pass_context
def grant_command(
    ctx: Context,
    username: str,
    role: str,
    team: str | None,
    project: str | None,
    environment: str | None,
    scope: str | None,
) -> None:
    """
    Give an account a role at a scope.

    \b
      noust access grant maria --role admin --team acme --project web
      noust access grant ci-bot --role release-manager --team acme --project web --env staging
      noust access grant paul --role db-reader --scope instance

    Giving a role where the account already has one replaces it. A built-in
    role on a whole team is the team's membership ('noust team add-member').
    """
    binding = access_manager().bind(
        username,
        role,
        scope=scope,
        team=team,
        project=project,
        environment=environment,
        actor="cli",
    )
    record(
        "access.bind",
        f"account:{binding.username}",
        {"role": binding.role, "scope": binding.scope_kind, "where": binding.scope},
    )
    ctx.logger.success(f"{binding.username} is now {binding.role} on {_where(binding)}")
    if ctx.json_output:
        click.echo(json.dumps({"binding": binding.to_dict()}))


@cli.command("revoke")
@click.argument("binding_id", type=int)
@pass_context
def revoke_command(ctx: Context, binding_id: int) -> None:
    """Take a role away, by the id 'noust access list' shows."""
    binding = access_manager().unbind(binding_id)
    record(
        "access.unbind",
        f"account:{binding.username}",
        {"role": binding.role, "scope": binding.scope_kind, "where": binding.scope},
    )
    ctx.logger.success(f"{binding.username} no longer holds {binding.role} on {_where(binding)}")
