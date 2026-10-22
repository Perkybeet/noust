# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust auth`` command group: how people sign in besides a password.

``noust auth oidc`` configures single sign-in through an OpenID Connect
provider (Keycloak, Authentik, Okta, Auth0, Microsoft Entra, Google, ...): which
provider, who may come in through it, and what a first sign-in becomes. A thin
front end over :class:`~noust.core.accounts.oidc.OidcManager`, the manager the
console's ``/api/auth/oidc`` routes call, so what is set here is what the
sign-in page offers and every rule is the same one.

It runs as root and is not held to roles, like ``noust user``: it is the channel
that sets single sign-in up in the first place. What it does is on the audit
trail with the identity of whoever ran it. The client secret is never taken
from the command line, which every local user can read in ``ps`` and which
lands in shell history: it is typed at a hidden prompt, or piped with
``--client-secret-stdin``, and kept in a 0600 file of the secret store.
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
    mappings_from_options,
    read_secret,
    record_audit,
)
from noust.cli.commands.auth_ldap import ldap
from noust.cli.commands.auth_saml import saml
from noust.cli.commands.auth_scim import scim
from noust.core.accounts.oidc import OidcManager
from noust.core.accounts.oidc_model import (
    KINDS,
    PROVIDER_ROLES,
    PROVISIONING,
    OidcProvider,
)
from noust.core.config import Config
from noust.core.teams.model import TEAM_ROLES

CALLBACK = "/api/auth/oidc/callback"


def _manager() -> OidcManager:
    """
    Returns:
        The single sign-in manager over this server's store.
    """
    return OidcManager()


def _callback_for(provider: OidcProvider) -> str | None:
    """
    Args:
        provider: A provider.

    Returns:
        The callback address to register with it: its own, else the console's
        public one; None when neither is known.
    """
    if provider.redirect_uri:
        return provider.redirect_uri
    public = str(Config().get("web.public_url") or "").strip().rstrip("/")
    return public + CALLBACK if public else None


def _describe(ctx: Context, provider: OidcProvider, has_secret: bool) -> None:
    """
    Print one provider in full.

    Args:
        ctx: The CLI context.
        provider: The provider.
        has_secret: Whether a client secret is stored.
    """
    logger = ctx.logger
    logger.info(
        f"{provider.name}: {provider.label} ({'enabled' if provider.enabled else 'disabled'})"
    )
    rows = [
        ("kind", provider.kind),
        ("issuer", provider.issuer),
        ("client id", provider.client_id),
        (
            "client secret",
            "set"
            if has_secret
            else (
                "none (GitHub needs it to give a token)"
                if provider.kind == "github"
                else "none (a public client: PKCE alone)"
            ),
        ),
        ("scopes", provider.scopes),
        ("e-mail must be verified", "yes" if provider.require_email_verified else "no"),
        (
            "e-mail assumed verified when unsaid",
            "yes (a directory the organisation controls)"
            if provider.assume_email_verified
            else "no",
        ),
        ("clock skew", f"{provider.clock_skew_seconds} s"),
        ("provisioning", provider.provisioning),
        ("default role", provider.default_role),
        (
            "default team",
            f"{provider.default_team} ({provider.default_team_role})"
            if provider.default_team
            else "the default team",
        ),
        ("link by verified e-mail", "yes" if provider.link_by_email else "no"),
        ("allowed e-mail domains", ", ".join(provider.allowed_domains) or "any"),
        ("required methods (amr)", ", ".join(provider.require_amr) or "none"),
        ("groups claim", provider.groups_claim),
        ("groups decide roles and teams", "yes" if provider.group_sync else "no"),
    ]
    for key, value in rows:
        logger.key_value(key, value)
    for mapping in provider.group_mappings:
        logger.key_value(f"group {mapping.group}", describe_mapping(mapping))
    callback = _callback_for(provider)
    logger.key_value(
        "redirect URI to register",
        callback or "set web.public_url first (https://your-console-name)",
    )


@click.group("auth", cls=NoustGroup)
def cli() -> None:
    """How people sign in to the console besides a password."""


cli.add_command(scim)
cli.add_command(ldap)
cli.add_command(saml)


@cli.group("oidc", cls=NoustGroup)
def oidc() -> None:
    """Single sign-in through an OpenID Connect provider."""


@oidc.command("set")
@click.argument("name")
@click.option(
    "--kind",
    type=click.Choice(KINDS),
    default=None,
    help="oidc (the default) or github: OAuth 2.0 with GitHub, chosen when the provider is made.",
)
@click.option(
    "--issuer",
    default=None,
    help="The provider's issuer URL, exactly as its tokens write it "
    "(a GitHub Enterprise Server's address for --kind github).",
)
@click.option("--client-id", default=None, help="The client id Noust is registered as.")
@click.option(
    "--client-secret-stdin", is_flag=True, help="Read the client secret from standard input."
)
@click.option("--prompt-secret", is_flag=True, help="Ask for the client secret at a hidden prompt.")
@click.option(
    "--clear-secret", is_flag=True, help="Forget the stored client secret (a public client)."
)
@click.option("--label", default=None, help="What the button says after 'Sign in with'.")
@click.option("--scopes", default=None, help="Space separated; openid is always asked.")
@click.option("--enable", "enable", is_flag=True, help="Offer it on the sign-in page.")
@click.option(
    "--disable", "disable", is_flag=True, help="Stop offering it, and refuse its answers."
)
@click.option(
    "--require-email-verified",
    "verified_on",
    is_flag=True,
    help="Refuse a sign-in whose e-mail the provider does not vouch for (the default).",
)
@click.option(
    "--allow-unverified-email",
    "verified_off",
    is_flag=True,
    help="Let in people whose e-mail is not verified; it is then never used to find an account.",
)
@click.option(
    "--assume-email-verified",
    "assume_on",
    is_flag=True,
    help="Treat an e-mail the provider says nothing about as verified (Microsoft Entra, a managed directory).",
)
@click.option(
    "--no-assume-email-verified", "assume_off", is_flag=True, help="Never assume it (the default)."
)
@click.option(
    "--clock-skew", type=int, default=None, help="Seconds of clock difference to forgive (0-300)."
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
    help="The role of an account that provisioning creates.",
)
@click.option(
    "--default-team",
    default=None,
    help="The team a created account joins ('' for the default team).",
)
@click.option(
    "--default-team-role", type=click.Choice(TEAM_ROLES), default=None, help="Its role there."
)
@click.option(
    "--link-by-email",
    "link_on",
    is_flag=True,
    help="Link a first sign-in to the account of its verified e-mail (only one with a second factor).",
)
@click.option("--no-link-by-email", "link_off", is_flag=True, help="Never link by e-mail.")
@click.option(
    "--allowed-domain",
    "domains",
    multiple=True,
    help="Only e-mail addresses of this domain (repeat for several).",
)
@click.option("--clear-domains", is_flag=True, help="Allow every domain again.")
@click.option(
    "--require-amr",
    "amr",
    multiple=True,
    help="The provider must say it used one of these methods, such as mfa (repeat for several).",
)
@click.option("--clear-amr", is_flag=True, help="Require no method.")
@click.option("--groups-claim", default=None, help="Where the token lists groups; nest with '>'.")
@click.option(
    "--group-sync",
    "sync_on",
    is_flag=True,
    help="Let groups decide roles and teams at every sign-in.",
)
@click.option("--no-group-sync", "sync_off", is_flag=True, help="Stop letting groups decide.")
@click.option(
    "--map-role", "map_roles", multiple=True, help="GROUP=ROLE: members get this role (repeat)."
)
@click.option(
    "--map-team",
    "map_teams",
    multiple=True,
    help="GROUP=TEAM:ROLE: members join this team (repeat).",
)
@click.option(
    "--map-binding",
    "map_bindings",
    multiple=True,
    help=(
        "GROUP=ROLE@TEAM[/PROJECT[/ENVIRONMENT]]: members hold this role there, a built-in "
        "one on a project or a custom one anywhere (repeat)."
    ),
)
@click.option("--clear-mappings", is_flag=True, help="Forget every group mapping.")
@click.option(
    "--redirect-uri",
    default=None,
    help="The callback address, when it cannot be worked out ('' to clear).",
)
@pass_context
def set_command(
    ctx: Context,
    name: str,
    kind: str | None,
    issuer: str | None,
    client_id: str | None,
    client_secret_stdin: bool,
    prompt_secret: bool,
    clear_secret: bool,
    label: str | None,
    scopes: str | None,
    enable: bool,
    disable: bool,
    verified_on: bool,
    verified_off: bool,
    assume_on: bool,
    assume_off: bool,
    clock_skew: int | None,
    provisioning: str | None,
    default_role: str | None,
    default_team: str | None,
    default_team_role: str | None,
    link_on: bool,
    link_off: bool,
    domains: tuple[str, ...],
    clear_domains: bool,
    amr: tuple[str, ...],
    clear_amr: bool,
    groups_claim: str | None,
    sync_on: bool,
    sync_off: bool,
    map_roles: tuple[str, ...],
    map_teams: tuple[str, ...],
    map_bindings: tuple[str, ...],
    clear_mappings: bool,
    redirect_uri: str | None,
) -> None:
    """
    Add an identity provider, or change one: only the settings given change.

    A new one needs --issuer and --client-id. Register Noust at the provider
    first (an OpenID Connect client with the authorization code flow), give it
    the redirect URI 'noust auth oidc show NAME' prints, and copy its client id
    and secret here.

    For GitHub: register an OAuth App (Settings > Developer settings), give it the
    redirect URI as its Authorization callback URL, and run

    \b
      noust auth oidc set github --kind github --client-id ID --prompt-secret
    """
    changes: dict[str, Any] = {
        "kind": kind,
        "issuer": issuer,
        "client_id": client_id,
        "label": label,
        "scopes": scopes,
        "enabled": either_flag((enable, disable), "--enable/--disable"),
        "require_email_verified": either_flag(
            (verified_on, verified_off), "--require-email-verified/--allow-unverified-email"
        ),
        "assume_email_verified": either_flag(
            (assume_on, assume_off), "--assume-email-verified/--no-assume-email-verified"
        ),
        "clock_skew_seconds": clock_skew,
        "provisioning": provisioning,
        "default_role": default_role,
        "default_team": default_team,
        "default_team_role": default_team_role,
        "link_by_email": either_flag((link_on, link_off), "--link-by-email/--no-link-by-email"),
        "groups_claim": groups_claim,
        "group_sync": either_flag((sync_on, sync_off), "--group-sync/--no-group-sync"),
        "redirect_uri": redirect_uri,
    }
    if domains or clear_domains:
        changes["allowed_domains"] = list(domains)
    if amr or clear_amr:
        changes["require_amr"] = list(amr)
    if map_roles or map_teams or map_bindings or clear_mappings:
        changes["group_mappings"] = mappings_from_options(map_roles, map_teams, map_bindings)
    changes = {key: value for key, value in changes.items() if value is not None}

    secret = read_secret(
        "client secret", name, client_secret_stdin, prompt_secret, "noust auth oidc set ..."
    )
    manager = _manager()
    existed = manager.provider(name) is not None
    provider = manager.configure(
        name, changes, client_secret=secret, clear_secret=clear_secret, actor="cli"
    )
    names = sorted([*changes, *(["client_secret"] if secret else [])])
    record_audit(
        "auth.oidc.config",
        f"provider:{provider.name}",
        {"change": "changed" if existed else "added", "settings": names},
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {**provider.to_dict(), "client_secret_set": manager.has_secret(provider.name)}
            )
        )
        return
    ctx.logger.success(f"Provider {'changed' if existed else 'added'}: {provider.name}")
    _describe(ctx, provider, manager.has_secret(provider.name))
    if provider.provisioning == "auto":
        ctx.logger.warning(
            f"Everyone this provider signs in gets an account as {provider.default_role}. "
            "Restrict who with --allowed-domain, or use --provisioning invite."
        )
    ctx.logger.info(f"Check it with: noust auth oidc test {provider.name}")


@oidc.command("show", read_only=True)
@click.argument("name", required=False)
@json_option("Print the providers as JSON.")
@pass_context
def show_command(ctx: Context, name: str | None) -> None:
    """Show the configured providers, or one in full. Secrets are never shown."""
    manager = _manager()
    providers = [manager.require_provider(name)] if name else manager.providers()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "providers": [
                        {
                            **found.to_dict(),
                            "client_secret_set": manager.has_secret(found.name),
                            "redirect_uri": _callback_for(found),
                        }
                        for found in providers
                    ]
                }
            )
        )
        return
    if not providers:
        ctx.logger.info(
            "No identity provider is configured. Add one with 'noust auth oidc set NAME "
            "--issuer URL --client-id ID --prompt-secret'."
        )
        return
    if name:
        _describe(ctx, providers[0], manager.has_secret(providers[0].name))
        return
    ctx.logger.table(
        ["Name", "Button", "Issuer", "State", "Provisioning", "Secret"],
        [
            [
                found.name,
                found.label,
                found.issuer,
                "enabled" if found.enabled else "disabled",
                found.provisioning,
                "set" if manager.has_secret(found.name) else "-",
            ]
            for found in providers
        ],
    )


@oidc.command("test", read_only=True)
@click.argument("name")
@json_option("Print the checks as JSON.")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """
    Look at a provider the way a sign-in would: discovery, keys, secret, callback.

    Signs nobody in and changes nothing. Exits 1 when a check fails.
    """
    manager = _manager()
    provider = manager.require_provider(name)
    checks = manager.test(provider.name, redirect_uri=_callback_for(provider))
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


@oidc.command("remove")
@click.argument("name")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, name: str, force: bool) -> None:
    """
    Remove a provider and its client secret.

    Accounts keep their links and every other way in. One that has no password
    cannot sign in until it is given one: 'noust user invite NAME'.
    """
    manager = _manager()
    provider = manager.require_provider(name)
    if not force and not click.confirm(
        f"Remove the provider {provider.name}? Nobody can sign in through it any more",
        default=False,
    ):
        ctx.logger.info("Cancelled")
        return
    manager.remove(name)
    record_audit("auth.oidc.config", f"provider:{provider.name}", {"change": "removed"})
    ctx.logger.success(f"Provider removed: {provider.name}")


@oidc.command("link")
@click.argument("name")
@click.argument("username")
@click.option(
    "--subject",
    required=True,
    help="The provider's id (sub) for the person: it is in the audit log of a refused sign-in.",
)
@pass_context
def link_command(ctx: Context, name: str, username: str, subject: str) -> None:
    """Link an account to a person at a provider, so they sign in as it."""
    identity = _manager().link(name, username, subject, actor="cli")
    record_audit(
        "auth.oidc.link",
        f"account:{username.strip().lower()}",
        {"provider": identity.provider, "subject": identity.subject[:64]},
    )
    if ctx.json_output:
        click.echo(json.dumps(identity.to_dict()))
        return
    ctx.logger.success(f"Linked {username} to {identity.subject} at {identity.provider}.")


@oidc.command("unlink")
@click.argument("identity_id", type=int)
@pass_context
def unlink_command(ctx: Context, identity_id: int) -> None:
    """Cut a link ('noust auth oidc identities' lists them with their ids)."""
    manager = _manager()
    found = manager.data.get_identity(identity_id)
    account = manager.accounts.get(found.account_id) if found else None
    removed = manager.unlink_identity(identity_id)
    record_audit(
        "auth.oidc.unlink",
        f"account:{account.username if account else removed.account_id}",
        {"provider": removed.provider, "subject": removed.subject[:64]},
    )
    ctx.logger.success(f"Unlinked {removed.subject} from {account.username if account else '?'}.")
    if account is not None and not account.has_password and account.identities <= 1:
        ctx.logger.warning(
            f"{account.username} has no password and no other link: it cannot sign in. "
            f"Give it a way: noust user invite {account.username}."
        )


@oidc.command("identities", read_only=True)
@click.option("--user", "username", default=None, help="Only this account's.")
@json_option("Print the links as JSON.")
@pass_context
def identities_command(ctx: Context, username: str | None) -> None:
    """List the people at providers that are linked to accounts."""
    manager = _manager()
    links = manager.identities(username)
    names = {account.id: account.username for account in manager.accounts.list_all()}
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "identities": [
                        {**item.to_dict(), "account": names.get(item.account_id, "")}
                        for item in links
                    ]
                }
            )
        )
        return
    if not links:
        ctx.logger.info("No account is linked to a person at a provider.")
        return
    ctx.logger.table(
        ["Id", "Account", "Provider", "Subject", "E-mail", "Last sign-in"],
        [
            [
                str(item.id),
                names.get(item.account_id, "?"),
                item.provider,
                item.subject,
                item.email or "-",
                format_time(item.last_login_at),
            ]
            for item in links
        ],
    )


@cli.group("local-login", cls=NoustGroup)
def local_login() -> None:
    """Turn the password and passkey sign-in off once single sign-on is in."""


def _local_login_report(ctx: Context) -> None:
    """
    Print where the password and passkey sign-in stands.

    Args:
        ctx: The command context.
    """
    from noust.core.accounts.local_login import load_policy, survivors

    policy = load_policy()
    names = survivors(exempt=policy.exempt)
    if ctx.json_output:
        click.echo(json.dumps({**policy.to_dict(), "survivors": names}))
        return
    ctx.logger.info(
        "Password and passkey sign-in: " + ("on" if policy.enabled else "off (single sign-on only)")
    )
    ctx.logger.info(
        "Break-glass accounts that keep their password: "
        + (", ".join(sorted(policy.exempt)) or "none")
    )
    ctx.logger.info(
        "Administrators who could sign in without a password: " + (", ".join(names) or "none")
    )
    ctx.logger.info("The master token and this command line work either way.")


@local_login.command("status", read_only=True)
@json_option("Print the setting as JSON.")
@pass_context
def local_login_status(ctx: Context) -> None:
    """Show whether passwords and passkeys still sign accounts in."""
    _local_login_report(ctx)


@local_login.command("off")
@pass_context
def local_login_off(ctx: Context) -> None:
    """
    Stop accepting passwords and passkeys: only the identity provider signs people in.

    Refused while that would leave no administrator or security officer able to
    sign in (link one with 'noust auth oidc link', or name a break-glass account
    with 'noust auth local-login exempt add'). The master token still recovers
    the server, and so does this command line.
    """
    Config().set("auth.local_login.enabled", False)
    record_audit("auth.local_login.change", "setting:local_login", {"change": "disabled"})
    ctx.logger.success("Password and passkey sign-in is off.")
    _local_login_report(ctx)


@local_login.command("on")
@pass_context
def local_login_on(ctx: Context) -> None:
    """Accept passwords and passkeys again."""
    Config().set("auth.local_login.enabled", True)
    record_audit("auth.local_login.change", "setting:local_login", {"change": "enabled"})
    ctx.logger.success("Password and passkey sign-in is on.")


@local_login.group("exempt", cls=NoustGroup)
def local_login_exempt() -> None:
    """The break-glass accounts that keep their password while it is off."""


@local_login_exempt.command("add")
@click.argument("username")
@pass_context
def local_login_exempt_add(ctx: Context, username: str) -> None:
    """Let an account keep its password and passkeys when they are off."""
    from noust.core.accounts.local_login import clean_exempt, load_policy

    account = _manager().accounts.require(username)
    names = clean_exempt([*sorted(load_policy().exempt), account.username])
    Config().set("auth.local_login.exempt", names)
    record_audit(
        "auth.local_login.change", f"account:{account.username}", {"change": "exempt added"}
    )
    ctx.logger.success(f"{account.username} keeps its password when the others cannot use theirs.")


@local_login_exempt.command("remove")
@click.argument("username")
@pass_context
def local_login_exempt_remove(ctx: Context, username: str) -> None:
    """Stop an account keeping its password when they are off."""
    from noust.core.accounts.local_login import clean_exempt, load_policy

    name = username.strip().lower()
    names = clean_exempt([item for item in sorted(load_policy().exempt) if item != name])
    Config().set("auth.local_login.exempt", names)
    record_audit("auth.local_login.change", f"account:{name}", {"change": "exempt removed"})
    ctx.logger.success(f"{name} no longer keeps its password.")
