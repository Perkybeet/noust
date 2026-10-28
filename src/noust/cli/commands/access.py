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
from noust.core.access.role_manager import RoleManager, RoleView


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


def _state(view: RoleView) -> str:
    """
    Args:
        view: A role with its holders.

    Returns:
        How the role stands, in a word or two, for a table.
    """
    role = view.role
    if role.protected:
        return "protected"
    if role.kind == "custom":
        return "yours"
    return "edited" if role.edited else "follows Noust"


@role_group.command("list", read_only=True)
@json_option("Print the roles as JSON.")
@pass_context
def role_list(ctx: Context) -> None:
    """List the server's roles, and a team's built-in ones."""
    access = access_manager()
    catalog = access.catalog()
    views = RoleManager().views()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "roles": [view.to_dict() for view in views],
                    "built_in": catalog["built_in"],
                    "custom": catalog["custom"],
                }
            )
        )
        return
    ctx.logger.table(
        ["Role", "State", "Permissions", "Held by", "Offered", "Joins duties"],
        [
            [
                view.role.name,
                _state(view),
                str(len(view.role.permissions)),
                str(len(view.holders)),
                str(len(view.role.offers)) if view.role.offers else "-",
                "yes" if view.joins_duties else "-",
            ]
            for view in views
        ],
    )
    built_in: list[dict[str, Any]] = catalog["built_in"]  # type: ignore[assignment]
    ctx.logger.info(
        "A team's own roles: "
        + ", ".join(f"{item['name']} ({', '.join(item['scopes'])})" for item in built_in)
    )


@role_group.command("show", read_only=True)
@click.argument("name")
@json_option("Print the role as JSON.")
@pass_context
def role_show(ctx: Context, name: str) -> None:
    """Show what a role holds, permission by permission, and who holds it."""
    manager = RoleManager()
    if manager.roles.get(name) is None:
        catalog = access_manager().catalog()
        built_in = {item["name"]: item for item in catalog["built_in"]}  # type: ignore[attr-defined]
        if name in built_in:
            item = built_in[name]
            if ctx.json_output:
                click.echo(json.dumps(item))
                return
            ctx.logger.info(f"{item['name']}: a built-in role of a team")
            descriptions = permission_descriptions()
            for permission in item["permissions"]:
                ctx.logger.info(f"  {permission}  {descriptions.get(permission, '')}")
            return
    view = manager.view(name)
    if ctx.json_output:
        click.echo(json.dumps(view.to_dict()))
        return
    role = view.role
    ctx.logger.info(f"{role.name} ({_state(view)}): {role.description or role.label}")
    descriptions = permission_descriptions()
    for permission in role.permissions:
        ctx.logger.info(f"  {permission}  {descriptions.get(permission, '')}")
    if view.holders:
        ctx.logger.info("Held by: " + ", ".join(h.username for h in view.holders))
    if view.bindings:
        ctx.logger.info(f"Named by {view.bindings} binding(s) at a team, project or environment.")
    if role.offers:
        ctx.logger.info(
            "A newer release offers it: "
            + ", ".join(role.offers)
            + f". Take or leave them: noust access role offers {role.name} --accept PERMISSION"
        )
    if view.joins_duties:
        ctx.logger.info(
            "It holds "
            + ", ".join(view.governing)
            + " (governing) together with running the server: where a framework keeps them "
            "apart, split it."
        )


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

    Where a framework keeps governing and running apart, a role cannot hold
    security.manage, accounts.manage or audit.manage together with deploying,
    operating or managing: split it in two, as the built-in roles are.
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
    """
    Change a role, one Noust ships included; everyone who holds it holds the change.

    A role Noust ships stops following the release the first time its permissions change
    ('noust access role reset' puts it back). The owner cannot be changed.
    """
    role = access_manager().update_role(
        name,
        permissions=permissions or None,
        label=label,
        description=description,
        actor="cli",
    )
    record("access.role.update", f"role:{role.name}", {"permissions": list(role.permissions)})
    ctx.logger.success(f"Role updated: {role.name}")


@role_group.command("duplicate")
@click.argument("name")
@click.argument("new_name")
@click.option("--label", default=None, help="Its label; the original's by default.")
@pass_context
def role_duplicate(ctx: Context, name: str, new_name: str, label: str | None) -> None:
    """Copy a role into a role of your own, which you then change as you like."""
    copy = RoleManager().duplicate(name, new_name, label=label, actor="cli")
    record("access.role.duplicate", f"role:{copy.name}", {"copied_from": name})
    ctx.logger.success(f"Role created: {copy.name} (a copy of {name})")
    if ctx.json_output:
        click.echo(json.dumps({"role": copy.to_dict()}))


@role_group.command("reset")
@click.argument("name")
@pass_context
def role_reset(ctx: Context, name: str) -> None:
    """Put a role Noust ships back to how it comes: it follows the release again."""
    back = RoleManager().reset(name, actor="cli")
    record("access.role.reset", f"role:{back.name}")
    ctx.logger.success(f"Role put back: {back.name}")


@role_group.command("offers")
@click.argument("name")
@click.option("--accept", "accept", multiple=True, help="An offered permission to add (repeat).")
@click.option(
    "--dismiss", "dismiss", multiple=True, help="An offered permission to leave out (repeat)."
)
@json_option("Print the role as JSON.")
@pass_context
def role_offers(ctx: Context, name: str, accept: tuple[str, ...], dismiss: tuple[str, ...]) -> None:
    """
    Take or leave what a newer release ships for a role you edited.

    Without options it lists what is on offer.
    """
    manager = RoleManager()
    if accept or dismiss:
        role = manager.settle_offers(name, accept=accept, dismiss=dismiss, actor="cli")
        record(
            "access.role.offer",
            f"role:{role.name}",
            {"accepted": sorted(accept), "dismissed": sorted(dismiss)},
        )
        ctx.logger.success(f"Offers settled for {role.name}")
    else:
        role = manager.roles.require(name)
    if ctx.json_output:
        click.echo(json.dumps({"offers": list(role.offers)}))
        return
    if not role.offers:
        ctx.logger.info(f"Nothing is on offer for {role.name}.")
        return
    ctx.logger.table(["Offered"], [[permission] for permission in role.offers])


@role_group.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def role_remove(ctx: Context, name: str, yes: bool) -> None:
    """Remove a role of your own that nobody holds."""
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
        ctx.logger.info(
            "No role has been given at a team, a project or an environment. Give one with "
            "'noust access grant'. The roles of an account on the whole server are 'noust user list'."
        )
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
    "whole server: one more role of the account, as 'noust user roles' sets them.",
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
    # Root on the server has no account to hold anything with, which is how the owner role is
    # handed on when no owner is left to do it.
    binding = access_manager().bind(
        username,
        role,
        scope=scope,
        team=team,
        project=project,
        environment=environment,
        actor="cli",
        by=None,
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
