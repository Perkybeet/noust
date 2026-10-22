# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust auth ldap``: the directories people sign in against (LDAP, Active Directory).

A thin front end over :class:`~noust.core.accounts.directory.DirectoryManager`, the manager the
console's ``/api/auth/ldap`` routes call (rule 3). The service account's password is never taken
from the command line, which every local user can read in ``ps``: it is typed at a hidden prompt,
or piped with ``--password-stdin``, and kept in a 0600 file of the secret store.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.auth_common import (
    describe_mapping,
    either_flag,
    group_changes,
    group_options,
    read_secret,
    record_audit,
)
from noust.core.accounts.directory import DEFAULT_USER_FILTER, DirectoryManager
from noust.core.accounts.directory_store import LdapDirectory
from noust.core.accounts.ldap_client import TLS_MODES
from noust.core.accounts.oidc_model import PROVIDER_ROLES, PROVISIONING
from noust.core.teams.model import TEAM_ROLES


def _manager() -> DirectoryManager:
    """
    Returns:
        The directory manager over this server's store.
    """
    return DirectoryManager()


def _describe(ctx: Context, directory: LdapDirectory, has_password: bool) -> None:
    """
    Print one directory in full.

    Args:
        ctx: The command context.
        directory: The directory.
        has_password: Whether its service account's password is stored.
    """
    logger = ctx.logger
    rows = [
        ("name", directory.name),
        ("label", directory.label),
        ("state", "enabled" if directory.enabled else "disabled"),
        ("server", f"{directory.host}:{directory.port} ({directory.tls})"),
        ("trusts", directory.ca_file or "the system's authorities"),
        ("search as", directory.bind_dn or "nobody (anonymously)"),
        ("password", "stored" if has_password else "-"),
        ("people from", directory.user_base),
        ("people found by", directory.user_filter),
        ("stable identifier", directory.subject_attribute),
        ("e-mail", directory.email_attribute),
        ("display name", directory.name_attribute),
        ("login name", directory.username_attribute),
        ("groups listed in", directory.groups_attribute),
        (
            "groups searched in",
            f"{directory.group_base} with {directory.group_filter}"
            if directory.group_base
            else "-",
        ),
        ("a first sign-in", directory.provisioning),
        ("new accounts start as", directory.default_role),
        (
            "new accounts join team",
            f"{directory.default_team} ({directory.default_team_role})"
            if directory.default_team
            else "the default one",
        ),
        ("link by e-mail", "yes" if directory.link_by_email else "no"),
        ("allowed e-mail domains", ", ".join(directory.allowed_domains) or "any"),
        ("groups decide roles", "yes" if directory.group_sync else "no"),
    ]
    for key, value in rows:
        logger.key_value(key, value)
    for mapping in directory.group_mappings:
        logger.key_value(f"group {mapping.group}", describe_mapping(mapping))


@click.group("ldap", cls=NoustGroup)
def ldap() -> None:
    """Directories people sign in against, with the name and password they have there."""


@ldap.command("set")
@click.argument("name")
@click.option("--host", default=None, help="The directory's name or address.")
@click.option("--port", type=int, default=None, help="Its port (636 for ldaps, 389 otherwise).")
@click.option(
    "--tls",
    type=click.Choice(TLS_MODES),
    default=None,
    help="ldaps, starttls, or none (a directory on this machine only).",
)
@click.option(
    "--ca-file", default=None, help="A PEM bundle of the authority to trust ('' for the system's)."
)
@click.option("--label", default=None, help="What the sign-in page says.")
@click.option("--enable", is_flag=True, help="Offer it on the sign-in page.")
@click.option("--disable", is_flag=True, help="Stop offering it.")
@click.option(
    "--bind-dn", default=None, help="The service account that searches ('' to search anonymously)."
)
@click.option("--password-stdin", is_flag=True, help="Read its password from standard input.")
@click.option("--prompt-password", is_flag=True, help="Ask for its password at a hidden prompt.")
@click.option("--clear-bind-password", is_flag=True, help="Forget its stored password.")
@click.option(
    "--user-base",
    default=None,
    help="Where people are searched from, such as ou=people,dc=acme,dc=test.",
)
@click.option(
    "--user-filter",
    default=None,
    help=f"The search, with {{username}} where the typed name goes. Default: {DEFAULT_USER_FILTER}",
)
@click.option(
    "--subject-attribute",
    default=None,
    help="The directory's stable id for a person: entryUUID (OpenLDAP, 389, FreeIPA) or objectGUID (Active Directory).",
)
@click.option("--email-attribute", default=None, help="Where the address is (mail).")
@click.option("--name-attribute", default=None, help="Where the display name is (displayName).")
@click.option(
    "--username-attribute", default=None, help="Where the login name is (uid, sAMAccountName)."
)
@click.option(
    "--groups-attribute", default=None, help="Where the entry lists its groups (memberOf)."
)
@click.option(
    "--group-base",
    default=None,
    help="Where groups are searched, when the entry does not list them.",
)
@click.option(
    "--group-filter",
    default=None,
    help="That search, with {dn} and {username}: (&(objectClass=groupOfNames)(member={dn})).",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=int,
    default=None,
    help="Seconds one sign-in may take (1-30).",
)
@click.option(
    "--provisioning",
    type=click.Choice(PROVISIONING),
    default=None,
    help="A first sign-in: off (an administrator links people), invite, or auto (create the account).",
)
@click.option(
    "--default-role",
    type=click.Choice(PROVIDER_ROLES),
    default=None,
    help="The role of a created account.",
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
    help="Link a first sign-in to the account of its address.",
)
@click.option("--no-link-by-email", "link_off", is_flag=True, help="Never link by e-mail.")
@click.option(
    "--allowed-domain", "domains", multiple=True, help="Only addresses of this domain (repeat)."
)
@click.option("--clear-domains", is_flag=True, help="Allow every domain again.")
@click.option("--require-email", "email_on", is_flag=True, help="Refuse a person with no address.")
@click.option(
    "--no-require-email", "email_off", is_flag=True, help="Let in people with no address."
)
@group_options
@json_option("Print the directory as JSON.")
@pass_context
def set_command(
    ctx: Context,
    name: str,
    host: str | None,
    port: int | None,
    tls: str | None,
    ca_file: str | None,
    label: str | None,
    enable: bool,
    disable: bool,
    bind_dn: str | None,
    password_stdin: bool,
    prompt_password: bool,
    clear_bind_password: bool,
    user_base: str | None,
    user_filter: str | None,
    subject_attribute: str | None,
    email_attribute: str | None,
    name_attribute: str | None,
    username_attribute: str | None,
    groups_attribute: str | None,
    group_base: str | None,
    group_filter: str | None,
    timeout_seconds: int | None,
    provisioning: str | None,
    default_role: str | None,
    default_team: str | None,
    default_team_role: str | None,
    link_on: bool,
    link_off: bool,
    domains: tuple[str, ...],
    clear_domains: bool,
    email_on: bool,
    email_off: bool,
    sync_on: bool,
    sync_off: bool,
    map_roles: tuple[str, ...],
    map_teams: tuple[str, ...],
    map_bindings: tuple[str, ...],
    clear_mappings: bool,
) -> None:
    """
    Add a directory, or change one: only the settings given change.

    A new one needs --host and --user-base. The connection is encrypted (ldaps or starttls) and
    the certificate verified; a directory on a private network is allowed by naming it in
    security.allow_private_hosts. Check it with 'noust auth ldap test NAME'.
    """
    changes: dict[str, Any] = {
        "host": host,
        "port": port,
        "tls": tls,
        "ca_file": ca_file,
        "label": label,
        "enabled": either_flag((enable, disable), "--enable/--disable"),
        "bind_dn": bind_dn,
        "user_base": user_base,
        "user_filter": user_filter,
        "subject_attribute": subject_attribute,
        "email_attribute": email_attribute,
        "name_attribute": name_attribute,
        "username_attribute": username_attribute,
        "groups_attribute": groups_attribute,
        "group_base": group_base,
        "group_filter": group_filter,
        "timeout_seconds": timeout_seconds,
        "provisioning": provisioning,
        "default_role": default_role,
        "default_team": default_team,
        "default_team_role": default_team_role,
        "link_by_email": either_flag((link_on, link_off), "--link-by-email/--no-link-by-email"),
        "require_email_verified": either_flag(
            (email_on, email_off), "--require-email/--no-require-email"
        ),
        **group_changes(sync_on, sync_off, map_roles, map_teams, map_bindings, clear_mappings),
    }
    if domains or clear_domains:
        changes["allowed_domains"] = list(domains)
    changes = {key: value for key, value in changes.items() if value is not None}

    password = read_secret(
        "bind password", name, password_stdin, prompt_password, "noust auth ldap set ..."
    )
    manager = _manager()
    existed = manager.directory(name) is not None
    directory = manager.configure(
        name,
        changes,
        bind_password=password,
        clear_bind_password=clear_bind_password,
        actor="cli",
    )
    record_audit(
        "auth.ldap.config",
        f"directory:{directory.name}",
        {
            "change": "changed" if existed else "added",
            "settings": sorted([*changes, *(["bind_password"] if password else [])]),
        },
    )
    has_password = manager.data.has_secret(directory.name)
    if ctx.json_output:
        click.echo(json.dumps({**directory.to_dict(), "bind_password_set": has_password}))
        return
    ctx.logger.success(f"Directory {'changed' if existed else 'added'}: {directory.name}")
    _describe(ctx, directory, has_password)
    if directory.provisioning == "auto":
        ctx.logger.warning(
            f"Everyone in this directory can sign in and gets an account as {directory.default_role}. "
            "Restrict who with --allowed-domain or the user filter, or use --provisioning invite."
        )
    ctx.logger.info(f"Check it with: noust auth ldap test {directory.name}")


@ldap.command("show", read_only=True)
@click.argument("name", required=False)
@json_option("Print the directories as JSON.")
@pass_context
def show_command(ctx: Context, name: str | None) -> None:
    """Show the configured directories, or one in full. Passwords are never shown."""
    manager = _manager()
    found = [manager.require_directory(name)] if name else manager.directories()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "directories": [
                        {**item.to_dict(), "bind_password_set": manager.data.has_secret(item.name)}
                        for item in found
                    ]
                }
            )
        )
        return
    if not found:
        ctx.logger.info(
            "No directory is configured. Add one with 'noust auth ldap set NAME --host HOST "
            "--user-base DN'."
        )
        return
    if name:
        _describe(ctx, found[0], manager.data.has_secret(found[0].name))
        return
    ctx.logger.table(
        ["Name", "Sign-in page", "Server", "State", "A first sign-in"],
        [
            [
                item.name,
                item.label,
                f"{item.host}:{item.port} ({item.tls})",
                "enabled" if item.enabled else "disabled",
                item.provisioning,
            ]
            for item in found
        ],
    )


@ldap.command("test", read_only=True)
@click.argument("name")
@json_option("Print the checks as JSON.")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """
    Look at a directory the way a sign-in would: the connection, the service account, the base.

    Signs nobody in and changes nothing. Exits 1 when a check fails.
    """
    checks = _manager().test(name)
    ok = all(check.ok for check in checks)
    if ctx.json_output:
        click.echo(json.dumps({"ok": ok, "checks": [check.to_dict() for check in checks]}))
    else:
        for check in checks:
            (ctx.logger.success if check.ok else ctx.logger.warning)(
                f"{check.name}: {check.detail}"
            )
    if not ok:
        raise SystemExit(1)


@ldap.command("remove")
@click.argument("name")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, name: str, force: bool) -> None:
    """Remove a directory and its password. People linked to it can no longer sign in through it."""
    manager = _manager()
    directory = manager.require_directory(name)
    if not force and not click.confirm(
        f"Remove the directory {directory.name}? People linked to it can no longer sign in with it",
        default=False,
    ):
        ctx.logger.info("Cancelled.")
        return
    manager.remove(directory.name)
    record_audit("auth.ldap.config", f"directory:{directory.name}", {"change": "removed"})
    ctx.logger.success(f"Directory removed: {directory.name}")


@ldap.command("link")
@click.argument("name")
@click.argument("username")
@click.option(
    "--subject",
    required=True,
    help="The directory's stable identifier for the person (entryUUID, objectGUID).",
)
@pass_context
def link_command(ctx: Context, name: str, username: str, subject: str) -> None:
    """
    Link an account to a person in a directory by hand.

    The way in for a directory whose first sign-in is 'off', and for an account whose
    e-mail is not the person's address there.
    """
    identity = _manager().link(name, username, subject, actor="cli")
    record_audit(
        "auth.ldap.link",
        f"account:{username}",
        {"detail": f"linked by hand to {name}", "subject": subject[:64]},
    )
    ctx.logger.success(f"Linked {username} to {name} (identity {identity.id})")
