# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust mcp`` command group: an endpoint for clients (J-19, J-20).

``POST /api/mcp`` speaks the Model Context Protocol, so an MCP client (an
editor, a command-line tool, a script) is given tools to inspect, deploy and
control applications with the address of the console and an API token. It is
off until an administrator turns it on, and what it may do is the token's:
``noust mcp token`` makes one with exactly the permissions a kind of client
needs, bound to a team if it should see nothing else. See ``docs/mcp.md``.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record
from noust.cli.web_state import access_manager, mcp_catalog, mcp_settings, token_manager
from noust.core.config import MCP_GROUPS, Config

#: What each kind of client needs, as the groups it uses: a deploying client also reads.
#: The groups are the names in ``noust.web.mcp.tools``, spelled here because that module is
#: the console's and is only imported when a command runs.
PRESETS: dict[str, tuple[str, ...]] = {
    "inspect": ("inspect",),
    "deploy": ("inspect", "deploy"),
    "control": ("inspect", "deploy", "control"),
}

#: The permission every session holds for itself, which every token keeps.
SELF_PERMISSION = "self"


def permissions_for(preset: str) -> list[str]:
    """
    Args:
        preset: ``inspect``, ``deploy`` or ``control``.

    Returns:
        The permissions the routes behind that kind of client's tools need, and
        nothing more.
    """
    groups = PRESETS[preset]
    tools, permission_for_route = mcp_catalog()
    needed = {
        permission_for_route(tool.method, tool.path) or "" for tool in tools if tool.group in groups
    }
    needed.discard("")
    needed.add(SELF_PERMISSION)
    return sorted(needed)


@click.group("mcp", cls=NoustGroup)
def cli() -> None:
    """Let clients inspect, deploy and control applications through MCP."""


def _status(ctx: Context) -> None:
    """
    Args:
        ctx: The command context.
    """
    settings, versions = mcp_settings()
    tools, _permission = mcp_catalog()
    groups = [g for g in MCP_GROUPS if g in settings.groups]
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "enabled": settings.enabled,
                    "groups": groups,
                    "protocol_versions": list(versions),
                    "endpoint": "/api/mcp",
                    "tools": len(tools),
                }
            )
        )
        return
    ctx.logger.info("MCP is " + ("on" if settings.enabled else "off") + ".")
    ctx.logger.info("Tool groups offered: " + (", ".join(groups) or "none"))
    ctx.logger.info("Endpoint: POST <console address>/api/mcp, with an API token")
    ctx.logger.info("Protocol revisions: " + ", ".join(versions))
    if not settings.enabled:
        ctx.logger.info("Turn it on with 'noust mcp enable'.")


@cli.command("status", read_only=True)
@json_option("Print the setting as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say whether MCP is on and which groups of tools it offers."""
    _status(ctx)


@cli.command("enable")
@pass_context
def enable_command(ctx: Context) -> None:
    """Turn the MCP endpoint on."""
    Config().set("mcp.enabled", True)
    record("mcp.settings", "setting:mcp", {"enabled": True})
    ctx.logger.success("MCP is on. Make a client a token: noust mcp token NAME --preset inspect")


@cli.command("disable")
@pass_context
def disable_command(ctx: Context) -> None:
    """Turn the MCP endpoint off: it answers nothing until it is turned on again."""
    Config().set("mcp.enabled", False)
    record("mcp.settings", "setting:mcp", {"enabled": False})
    ctx.logger.success("MCP is off.")


@cli.command("groups")
@click.argument("groups", nargs=-1, required=True, type=click.Choice(MCP_GROUPS))
@pass_context
def groups_command(ctx: Context, groups: tuple[str, ...]) -> None:
    """
    Choose which groups of tools are offered: inspect, deploy, control.

    \b
      noust mcp groups inspect            read-only: a client can look, never change
      noust mcp groups inspect deploy     also update, roll back and back up
    """
    Config().set("mcp.groups", list(groups))
    record("mcp.settings", "setting:mcp", {"groups": list(groups)})
    ctx.logger.success("Offered: " + ", ".join(g for g in MCP_GROUPS if g in groups))


@cli.command("tools", read_only=True)
@json_option("Print the tools as JSON.")
@pass_context
def tools_command(ctx: Context) -> None:
    """List every tool, the permission its route needs and what it asks to be confirmed."""
    tools, permission_for_route = mcp_catalog()
    rows: list[dict[str, Any]] = [
        {
            "name": tool.name,
            "group": tool.group,
            "permission": permission_for_route(tool.method, tool.path),
            "read_only": tool.read_only,
            "confirm": tool.destructive,
            "route": f"{tool.method} {tool.path}",
        }
        for tool in tools
    ]
    if ctx.json_output:
        click.echo(json.dumps({"tools": rows}))
        return
    ctx.logger.table(
        ["Tool", "Group", "Needs", "Changes", "Confirm"],
        [
            [
                str(row["name"]),
                str(row["group"]),
                str(row["permission"]),
                "no" if row["read_only"] else "yes",
                "yes" if row["confirm"] else "",
            ]
            for row in rows
        ],
    )


@cli.command("token")
@click.argument("name")
@click.option(
    "--preset",
    type=click.Choice(sorted(PRESETS)),
    default="inspect",
    show_default=True,
    help="What the client needs: inspect (read), deploy (also update, roll back, back up) or "
    "control (also start, stop, restart).",
)
@click.option("--owner", required=True, help="The account the token acts for.")
@click.option("--team", default=None, help="Bind it to this team: it sees nothing else.")
@click.option("--project", default=None, help="Bind it to this project of the team.")
@click.option("--env", "environment", default=None, help="Bind it to this environment.")
@click.option(
    "--expires-hours",
    type=int,
    default=24 * 30,
    show_default=True,
    help="Lifetime in hours. A client's credential should not live forever.",
)
@pass_context
def token_command(
    ctx: Context,
    name: str,
    preset: str,
    owner: str,
    team: str | None,
    project: str | None,
    environment: str | None,
    expires_hours: int,
) -> None:
    """
    Issue an API token with the permissions a client needs and no more.

    The token holds only what the tools of the preset need (never a secret, the
    machine, accounts or settings), at most what its owner holds, and no sudo
    mode. Printed once; give it to the client as 'Authorization: Bearer ...'.
    """
    manager = token_manager()
    account = manager.accounts.require(owner)
    bound = access_manager().bound_scope(team=team, project=project, environment=environment)
    issued = manager.create_api_token(
        name,
        "deploy" if preset != "inspect" else "read",
        expires_hours,
        owner=account,
        permissions=permissions_for(preset),
        created_by="cli",
        bound=bound,
    )
    ctx.logger.success(f"MCP token issued: {issued['name']} ({preset})")
    ctx.logger.blank()
    click.echo(f"Token: {issued['token']}")
    ctx.logger.blank()
    if ctx.dry_run:
        ctx.logger.warning("Rehearsal: this token was not saved and will not authenticate.")
    else:
        ctx.logger.warning("This is the only time the token is shown. Store it now.")
    if not mcp_settings()[0].enabled:
        ctx.logger.info("MCP is off: turn it on with 'noust mcp enable'.")
