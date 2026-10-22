# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust auth scim``: the identity providers that provision accounts here.

A thin front end over :class:`~noust.core.accounts.scim.ScimManager`, the manager the console's
``/api/auth/scim`` routes call (rule 3). A source is an identity provider that pushes the roster
of who may have an account (SCIM 2.0): it creates the account of a new person, switches it off
when the person leaves, and gives roles by group as it pushes them. The bearer token it
authenticates with is shown once, when it is issued, and kept as a hash; it is never taken from
the command line and never printed again.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.auth_common import (
    describe_mapping,
    either_flag,
    format_time,
    group_changes,
    group_options,
    record_audit,
)
from noust.core.accounts.oidc_model import PROVIDER_ROLES
from noust.core.accounts.scim import ScimManager
from noust.core.accounts.scim_store import ScimSource
from noust.core.config import Config
from noust.core.teams.model import TEAM_ROLES

BASE_PATH = "/api/scim/v2"


def _manager() -> ScimManager:
    """
    Returns:
        The SCIM manager over this server's store.
    """
    return ScimManager()


def _endpoint() -> str:
    """
    Returns:
        The address to give an identity provider as the SCIM endpoint: the console's public
        URL when it is set, a placeholder naming where it goes otherwise.
    """
    public = str(Config().get("web.public_url") or "").strip().rstrip("/")
    return (public or "https://CONSOLE-ADDRESS") + BASE_PATH


def _describe(ctx: Context, source: ScimSource, users: int) -> None:
    """
    Print one source in full.

    Args:
        ctx: The command context.
        source: The source.
        users: How many people it provisioned.
    """
    logger = ctx.logger
    rows = [
        ("name", source.name),
        ("label", source.label),
        ("state", "enabled" if source.enabled else "disabled"),
        ("token ends in", source.token_hint),
        ("token issued", format_time(source.token_created_at)),
        ("last used", f"{format_time(source.last_used_at)} from {source.last_used_ip or '-'}"),
        ("people provisioned", str(users)),
        ("new accounts start as", source.default_role),
        (
            "new accounts join team",
            f"{source.default_team} ({source.default_team_role})"
            if source.default_team
            else "the default one",
        ),
        ("attach by e-mail", "yes" if source.link_by_email else "no"),
        ("allowed e-mail domains", ", ".join(source.allowed_domains) or "any"),
        ("groups decide roles", "yes" if source.group_sync else "no"),
    ]
    for key, value in rows:
        logger.key_value(key, value)
    for mapping in source.group_mappings:
        logger.key_value(f"group {mapping.group}", describe_mapping(mapping))
    logger.key_value("SCIM endpoint", _endpoint())


@click.group("scim", cls=NoustGroup)
def scim() -> None:
    """Identity providers that provision accounts (SCIM 2.0): who may have one, and who left."""


@scim.command("set")
@click.argument("name")
@click.option("--label", default=None, help="What a person reads about it.")
@click.option("--enable", is_flag=True, help="Accept its token.")
@click.option("--disable", is_flag=True, help="Refuse its token (its accounts stay as they are).")
@click.option(
    "--rotate-token",
    is_flag=True,
    help="Issue a new token; the old one stops working at once.",
)
@click.option(
    "--default-role",
    type=click.Choice(PROVIDER_ROLES),
    default=None,
    help="The role of an account it creates (never security or auditor).",
)
@click.option(
    "--default-team", default=None, help="The team a created account joins ('' for the default)."
)
@click.option(
    "--default-team-role", type=click.Choice(TEAM_ROLES), default=None, help="Its role there."
)
@click.option(
    "--link-by-email",
    "link_on",
    is_flag=True,
    help="Attach a provisioned person to the account that already has their e-mail.",
)
@click.option("--no-link-by-email", "link_off", is_flag=True, help="Never attach by e-mail.")
@click.option(
    "--allowed-domain",
    "domains",
    multiple=True,
    help="Only provision e-mail addresses of this domain (repeat for several).",
)
@click.option("--clear-domains", is_flag=True, help="Provision every domain again.")
@group_options
@json_option("Print the source as JSON, with its token when one was issued.")
@pass_context
def set_command(
    ctx: Context,
    name: str,
    label: str | None,
    enable: bool,
    disable: bool,
    rotate_token: bool,
    default_role: str | None,
    default_team: str | None,
    default_team_role: str | None,
    link_on: bool,
    link_off: bool,
    domains: tuple[str, ...],
    clear_domains: bool,
    sync_on: bool,
    sync_off: bool,
    map_roles: tuple[str, ...],
    map_teams: tuple[str, ...],
    map_bindings: tuple[str, ...],
    clear_mappings: bool,
) -> None:
    """
    Add an identity provider that provisions accounts, or change one.

    A new source gets a bearer token, printed once: give it to the provider with the
    endpoint this prints. The accounts it creates wait for the person's first single
    sign-in (noust auth oidc, with --provisioning invite or auto), which activates them by
    the e-mail they share.
    """
    changes: dict[str, Any] = {
        "label": label,
        "enabled": either_flag((enable, disable), "--enable/--disable"),
        "default_role": default_role,
        "default_team": default_team,
        "default_team_role": default_team_role,
        "link_by_email": either_flag((link_on, link_off), "--link-by-email/--no-link-by-email"),
        **group_changes(sync_on, sync_off, map_roles, map_teams, map_bindings, clear_mappings),
    }
    if domains or clear_domains:
        changes["allowed_domains"] = list(domains)
    changes = {key: value for key, value in changes.items() if value is not None}

    manager = _manager()
    existed = manager.source(name) is not None
    source, token = manager.configure(name, changes, rotate_token=rotate_token, actor="cli")
    record_audit(
        "auth.scim.config",
        f"source:{source.name}",
        {
            "change": "changed" if existed else "added",
            "settings": sorted([*changes, *(["token"] if token else [])]),
        },
    )
    users = manager.data.count_users(source.name)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {**source.to_dict(), "users": users, "token": token, "endpoint": _endpoint()}
            )
        )
        return
    logger = ctx.logger
    logger.success(f"SCIM source {'changed' if existed else 'added'}: {source.name}")
    _describe(ctx, source, users)
    if token is not None:
        logger.blank()
        click.echo(f"Token: {token}")
        logger.blank()
        if ctx.dry_run:
            logger.warning("Rehearsal: this token was not saved and will not authenticate.")
        else:
            logger.warning("This is the only time the token is shown. Give it to the provider now.")
    if source.default_role == "admin":
        logger.warning(
            "Everyone this source provisions starts as admin. Use --default-role viewer and "
            "give roles by group."
        )


@scim.command("show", read_only=True)
@click.argument("name", required=False)
@json_option("Print the sources as JSON.")
@pass_context
def show_command(ctx: Context, name: str | None) -> None:
    """Show the sources, or one in full. Tokens are never shown."""
    manager = _manager()
    sources = [manager.require_source(name)] if name else manager.sources()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "endpoint": _endpoint(),
                    "sources": [
                        {**found.to_dict(), "users": manager.data.count_users(found.name)}
                        for found in sources
                    ],
                }
            )
        )
        return
    if not sources:
        ctx.logger.info(
            "No SCIM source is configured. Add one with 'noust auth scim set NAME --label TEXT'."
        )
        return
    if name:
        _describe(ctx, sources[0], manager.data.count_users(sources[0].name))
        return
    ctx.logger.table(
        ["Name", "Label", "State", "People", "New accounts", "Last used"],
        [
            [
                found.name,
                found.label,
                "enabled" if found.enabled else "disabled",
                str(manager.data.count_users(found.name)),
                found.default_role,
                format_time(found.last_used_at),
            ]
            for found in sources
        ],
    )


@scim.command("users", read_only=True)
@click.argument("name")
@json_option("Print the roster as JSON.")
@pass_context
def users_command(ctx: Context, name: str) -> None:
    """List the people a source provisioned, with the state of their account."""
    manager = _manager()
    source = manager.require_source(name)
    total, users = manager.data.list_users(source.name, limit=500)
    rows: list[dict[str, Any]] = []
    for user in users:
        account = manager.accounts.get(user.account_id)
        if account is None:
            continue
        rows.append(
            {
                "id": user.id,
                "user_name": user.user_name,
                "account": account.username,
                "status": account.status,
                "role": account.role,
                "disabled_by_scim": user.disabled_by_scim,
                "groups": [group.display_name for group in manager.data.groups_of(user.id)],
            }
        )
    if ctx.json_output:
        click.echo(json.dumps({"total": total, "users": rows}))
        return
    if not rows:
        ctx.logger.info("This source has not provisioned anybody yet.")
        return
    ctx.logger.table(
        ["User", "Account", "Status", "Role", "Groups"],
        [
            [
                row["user_name"],
                row["account"],
                row["status"] + (" (by SCIM)" if row["disabled_by_scim"] else ""),
                row["role"],
                ", ".join(row["groups"]) or "-",
            ]
            for row in rows
        ],
    )


@scim.command("remove")
@click.argument("name")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, name: str, force: bool) -> None:
    """
    Remove a source and its roster. Its token stops working; its accounts stay as they are.
    """
    manager = _manager()
    source = manager.require_source(name)
    if not force and not click.confirm(
        f"Remove the SCIM source {source.name}? Its provider can no longer provision",
        default=False,
    ):
        ctx.logger.info("Cancelled.")
        return
    manager.remove(source.name)
    record_audit("auth.scim.config", f"source:{source.name}", {"change": "removed"})
    ctx.logger.success(f"SCIM source removed: {source.name}")
