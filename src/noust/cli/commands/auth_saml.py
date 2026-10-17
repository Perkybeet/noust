# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust auth saml``: the SAML 2.0 identity providers people sign in with.

A thin front end over :class:`~noust.core.accounts.saml.SamlManager`, the manager the console's
``/api/auth/saml`` routes call (rule 3). Nothing here is secret: a provider is its entity id, its
sign-in address and the public certificates that verify it, so every setting may be given on the
command line. Registering Noust with the provider takes ``noust auth saml metadata NAME``.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.auth_common import (
    describe_mapping,
    either_flag,
    group_changes,
    group_options,
    record_audit,
)
from noust.core.accounts.oidc_model import PROVIDER_ROLES, PROVISIONING
from noust.core.accounts.saml import (
    ACS_PATH,
    SamlManager,
    describe_certificates,
    entity_id_of,
    sp_metadata,
)
from noust.core.accounts.saml_store import SamlProvider
from noust.core.config import Config
from noust.core.exceptions import ValidationError
from noust.core.teams.model import TEAM_ROLES

#: The largest file read for a provider's metadata or certificates: the same ceiling a message has.
MAX_FILE_BYTES = 1 << 20


def _manager() -> SamlManager:
    """
    Returns:
        The SAML manager over this server's store.
    """
    return SamlManager()


def _read_text(path: str, what: str) -> str:
    """
    Args:
        path: A file, or ``-`` for standard input.
        what: What it should hold, for the message.

    Returns:
        Its text.

    Raises:
        ValidationError: When it cannot be read, is not text, or is longer than is accepted.
    """
    try:
        data = sys.stdin.buffer.read(MAX_FILE_BYTES + 1) if path == "-" else Path(path).read_bytes()
    except OSError as exc:
        raise ValidationError(f"Cannot read the {what} {path}: {exc.strerror or exc}") from exc
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError(f"The {what} {path} is longer than {MAX_FILE_BYTES} bytes")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValidationError(f"The {what} {path} is not text") from exc


def _origin(given: str | None) -> str | None:
    """
    Args:
        given: ``--origin``.

    Returns:
        The address people reach the console at: the option, else ``web.public_url``, else None.
    """
    configured = str(Config().get("web.public_url") or "").strip()
    chosen = (given or configured).strip().rstrip("/")
    return chosen or None


def _describe(ctx: Context, provider: SamlProvider, origin: str | None) -> None:
    """
    Print one provider in full.

    Args:
        ctx: The command context.
        provider: The provider.
        origin: The console's address, when it is known.
    """
    logger = ctx.logger
    rows = [
        ("name", provider.name),
        ("label", provider.label),
        ("state", "enabled" if provider.enabled else "disabled"),
        ("identity provider", provider.idp_entity_id),
        ("signs in at", provider.sso_url),
        ("this server is", entity_id_of(provider, origin) if origin else "(set web.public_url)"),
        ("response posted to", f"{origin}{ACS_PATH}" if origin else "(set web.public_url)"),
        ("identifies a person by", provider.subject_attribute or "the NameID"),
        ("e-mail", provider.email_attribute or "(from a NameID that is one)"),
        ("display name", provider.name_attribute or "-"),
        ("login name", provider.username_attribute or "-"),
        ("groups listed in", provider.groups_attribute or "-"),
        ("clock tolerance", f"{provider.clock_skew_seconds} s"),
        ("a first sign-in", provider.provisioning),
        ("new accounts start as", provider.default_role),
        (
            "new accounts join team",
            f"{provider.default_team} ({provider.default_team_role})"
            if provider.default_team
            else "the default one",
        ),
        ("link by e-mail", "yes" if provider.link_by_email else "no"),
        ("allowed e-mail domains", ", ".join(provider.allowed_domains) or "any"),
        ("authentication required", ", ".join(provider.require_amr) or "any"),
        ("groups decide roles", "yes" if provider.group_sync else "no"),
    ]
    for key, value in rows:
        logger.key_value(key, value)
    for index, item in enumerate(describe_certificates(provider), start=1):
        expires = datetime.fromtimestamp(item.not_after, tz=timezone.utc)
        logger.key_value(
            f"certificate {index}",
            f"{item.subject}, valid until {expires:%Y-%m-%d}, SHA-256 {item.fingerprint[:32]}...",
        )
    for mapping in provider.group_mappings:
        logger.key_value(f"group {mapping.group}", describe_mapping(mapping))


@click.group("saml", cls=NoustGroup)
def saml() -> None:
    """SAML 2.0 identity providers people sign in with (Entra ID, Okta, Keycloak, ADFS)."""


@saml.command("set")
@click.argument("name")
@click.option(
    "--metadata-file",
    "metadata_file",
    default=None,
    help="The identity provider's metadata XML ('-' reads standard input): its entity id, sign-in "
    "address and signing certificates.",
)
@click.option("--entity-id", "idp_entity_id", default=None, help="Its entity id (issuer).")
@click.option("--sso-url", default=None, help="Its sign-in address (HTTP-Redirect binding).")
@click.option(
    "--certificate-file",
    "certificate_files",
    multiple=True,
    help="A file with its signing certificate in PEM (repeat for a rollover). Replaces the list.",
)
@click.option(
    "--sp-entity-id",
    default=None,
    help="The entity id this server presents ('' to derive it from the console's address).",
)
@click.option("--label", default=None, help="What the sign-in page says.")
@click.option("--enable", is_flag=True, help="Offer it on the sign-in page.")
@click.option("--disable", is_flag=True, help="Stop offering it.")
@click.option(
    "--subject-attribute",
    default=None,
    help="The attribute that identifies a person ('' to use the NameID, which should be persistent).",
)
@click.option("--email-attribute", default=None, help="Where the address is (email).")
@click.option("--name-attribute", default=None, help="Where the display name is (displayName).")
@click.option("--username-attribute", default=None, help="Where the login name is.")
@click.option("--groups-attribute", default=None, help="Where the groups are listed (groups).")
@click.option(
    "--clock-skew",
    "clock_skew_seconds",
    type=int,
    default=None,
    help="Tolerated clock difference, seconds.",
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
@click.option(
    "--require-context",
    "contexts",
    multiple=True,
    help="An authentication context class the provider must report (repeat; one of them).",
)
@click.option("--clear-contexts", is_flag=True, help="Require no particular authentication.")
@click.option("--require-email", "email_on", is_flag=True, help="Refuse a person with no address.")
@click.option(
    "--no-require-email", "email_off", is_flag=True, help="Let in people with no address."
)
@group_options
@click.option("--origin", default=None, help="The console's address, to show what to register.")
@json_option("Print the provider as JSON.")
@pass_context
def set_command(
    ctx: Context,
    name: str,
    metadata_file: str | None,
    idp_entity_id: str | None,
    sso_url: str | None,
    certificate_files: tuple[str, ...],
    sp_entity_id: str | None,
    label: str | None,
    enable: bool,
    disable: bool,
    subject_attribute: str | None,
    email_attribute: str | None,
    name_attribute: str | None,
    username_attribute: str | None,
    groups_attribute: str | None,
    clock_skew_seconds: int | None,
    provisioning: str | None,
    default_role: str | None,
    default_team: str | None,
    default_team_role: str | None,
    link_on: bool,
    link_off: bool,
    domains: tuple[str, ...],
    clear_domains: bool,
    contexts: tuple[str, ...],
    clear_contexts: bool,
    email_on: bool,
    email_off: bool,
    sync_on: bool,
    sync_off: bool,
    map_roles: tuple[str, ...],
    map_teams: tuple[str, ...],
    map_bindings: tuple[str, ...],
    clear_mappings: bool,
    origin: str | None,
) -> None:
    """
    Add a SAML provider, or change one: only the settings given change.

    A new one needs its metadata (--metadata-file), or --entity-id, --sso-url and
    --certificate-file. Assertions must be signed by a certificate given here; encrypted ones are
    refused. Register this server with the provider using 'noust auth saml metadata NAME'.
    """
    changes: dict[str, Any] = {
        "idp_entity_id": idp_entity_id,
        "sso_url": sso_url,
        "sp_entity_id": sp_entity_id,
        "label": label,
        "enabled": either_flag((enable, disable), "--enable/--disable"),
        "subject_attribute": subject_attribute,
        "email_attribute": email_attribute,
        "name_attribute": name_attribute,
        "username_attribute": username_attribute,
        "groups_attribute": groups_attribute,
        "clock_skew_seconds": clock_skew_seconds,
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
    if metadata_file:
        changes["idp_metadata"] = _read_text(metadata_file, "metadata file")
    if certificate_files:
        changes["certificates"] = [
            _read_text(path, "certificate file") for path in certificate_files
        ]
    if domains or clear_domains:
        changes["allowed_domains"] = list(domains)
    if contexts or clear_contexts:
        changes["require_amr"] = list(contexts)
    changes = {key: value for key, value in changes.items() if value is not None}

    manager = _manager()
    existed = manager.provider(name) is not None
    provider = manager.configure(name, changes, actor="cli")
    record_audit(
        "auth.saml.config",
        f"provider:{provider.name}",
        {"change": "changed" if existed else "added", "settings": sorted(changes)},
    )
    where = _origin(origin)
    if ctx.json_output:
        click.echo(json.dumps(provider.to_dict()))
        return
    ctx.logger.success(f"SAML provider {'changed' if existed else 'added'}: {provider.name}")
    _describe(ctx, provider, where)
    if provider.provisioning == "auto":
        ctx.logger.warning(
            f"Everyone this provider signs in gets an account as {provider.default_role}. Restrict "
            "who with --allowed-domain or an assignment at the provider, or use --provisioning invite."
        )
    ctx.logger.info(f"Register this server with it: noust auth saml metadata {provider.name}")
    ctx.logger.info(f"Check it with: noust auth saml test {provider.name}")


@saml.command("show", read_only=True)
@click.argument("name", required=False)
@click.option("--origin", default=None, help="The console's address, to show what to register.")
@json_option("Print the providers as JSON.")
@pass_context
def show_command(ctx: Context, name: str | None, origin: str | None) -> None:
    """Show the configured SAML providers, or one in full."""
    manager = _manager()
    found = [manager.require_provider(name)] if name else manager.providers()
    if ctx.json_output:
        click.echo(json.dumps({"providers": [item.to_dict() for item in found]}))
        return
    if not found:
        ctx.logger.info(
            "No SAML provider is configured. Add one with 'noust auth saml set NAME "
            "--metadata-file FILE'."
        )
        return
    if name:
        _describe(ctx, found[0], _origin(origin))
        return
    ctx.logger.table(
        ["Name", "Sign-in page", "Identity provider", "State", "A first sign-in"],
        [
            [
                item.name,
                item.label,
                item.idp_entity_id,
                "enabled" if item.enabled else "disabled",
                item.provisioning,
            ]
            for item in found
        ],
    )


@saml.command("metadata", read_only=True)
@click.argument("name")
@click.option(
    "--origin",
    default=None,
    help="The console's address, https://host (default: web.public_url).",
)
@pass_context
def metadata_command(ctx: Context, name: str, origin: str | None) -> None:
    """
    Print this server's SAML metadata for a provider, to register it with the identity provider.

    The document holds the entity id, the address the signed response is posted to and that
    assertions must be signed. Nothing in it is secret.
    """
    provider = _manager().require_provider(name)
    where = _origin(origin)
    if where is None:
        raise ValidationError(
            "The console's address is not known",
            details="Pass --origin https://noust.example.com, or set web.public_url.",
            field="origin",
        )
    click.echo(sp_metadata(provider, origin=where))


@saml.command("test", read_only=True)
@click.argument("name")
@click.option("--origin", default=None, help="The console's address (default: web.public_url).")
@json_option("Print the checks as JSON.")
@pass_context
def test_command(ctx: Context, name: str, origin: str | None) -> None:
    """
    Look at a provider the way a sign-in would: its certificates, its address, what it is told.

    Signs nobody in and changes nothing. Exits 1 when a check fails.
    """
    checks = _manager().test(name, origin=_origin(origin))
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


@saml.command("remove")
@click.argument("name")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, name: str, force: bool) -> None:
    """Remove a SAML provider. People linked to it can no longer sign in through it."""
    manager = _manager()
    provider = manager.require_provider(name)
    if not force and not click.confirm(
        f"Remove the provider {provider.name}? People linked to it can no longer sign in with it",
        default=False,
    ):
        ctx.logger.info("Cancelled.")
        return
    manager.remove(provider.name)
    record_audit("auth.saml.config", f"provider:{provider.name}", {"change": "removed"})
    ctx.logger.success(f"SAML provider removed: {provider.name}")


@saml.command("link")
@click.argument("name")
@click.argument("username")
@click.option(
    "--subject",
    required=True,
    help="The provider's identifier for the person: their NameID, or the subject attribute.",
)
@pass_context
def link_command(ctx: Context, name: str, username: str, subject: str) -> None:
    """
    Link an account to a person at a SAML provider by hand.

    The way in for a provider whose first sign-in is 'off', and for an account whose
    e-mail is not the person's address there.
    """
    identity = _manager().link(name, username, subject, actor="cli")
    record_audit(
        "auth.saml.link",
        f"account:{username}",
        {"detail": f"linked by hand to {name}", "subject": subject[:64]},
    )
    ctx.logger.success(f"Linked {username} to {name} (identity {identity.id})")
