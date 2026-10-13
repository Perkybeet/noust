# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust cert`` - where certificates come from: the authority, uploads, and each application's choice.

Presentation over :mod:`noust.managers.acme`, :mod:`noust.managers.custom_certificates` and
:mod:`noust.deployers.certificates`, which ``/api/certs/acme``, ``/api/certs/uploaded`` and
``/api/apps/{domain}/certificate`` call too. Attached to the ``cert`` group
(:mod:`noust.cli.commands.cert`), which owns ordering, listing and renewing.

A private key is read from a file or from standard input, never from an argument, and
the HMAC key of an external account the same.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.certificate_store import CustomCertificate
from noust.core.exceptions import ValidationError
from noust.deployers import certificates
from noust.managers.acme import AUTHORITIES, AcmeSettings
from noust.managers.cert_manager import CertManager
from noust.managers.custom_certificates import CustomCertificates
from noust.validators.domain import validate_domain

#: The most bytes read from a file given as a certificate or a key.
_MAX_FILE = 64 * 1024


def _read(path: str, what: str) -> str:
    """
    Read a PEM file, or standard input for ``-``.

    Args:
        path: The file, or ``-``.
        what: What it is, for the error.

    Returns:
        Its text.

    Raises:
        ValidationError: It cannot be read, or is far too large to be one.
    """
    try:
        text = sys.stdin.read(_MAX_FILE + 1) if path == "-" else Path(path).read_text("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read the {what} {path}: {exc}", details="Give a readable PEM file.", field=what
        ) from exc
    if len(text) > _MAX_FILE:
        raise ValidationError(f"The {what} {path} is far too large to be a PEM", field=what)
    return text


def _actor() -> str:
    """
    Returns:
        The audit's label for whoever runs this command.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


#: ``noust cert acme`` only reads when it is asked to change nothing and register nothing.
_ACME_CHANGES = ("ca", "email", "acme_server", "eab_kid", "eab_hmac_key_stdin", "register")


@click.command(
    "acme",
    cls=NoustCommand,
    short_help="Choose the certificate authority and the account.",
    read_only=lambda params: not any(params.get(key) for key in _ACME_CHANGES),
)
@click.option(
    "--ca",
    type=click.Choice(sorted(AUTHORITIES)),
    help="letsencrypt, letsencrypt-staging (try a setup without spending the rate limit), "
    "zerossl, google or custom.",
)
@click.option("--email", help="The address registered with the authority.")
@click.option(
    "--server", "acme_server", metavar="URL", help="The directory URL of a custom authority."
)
@click.option(
    "--eab-kid", metavar="ID", help="The key id of the external account (ZeroSSL, Google)."
)
@click.option(
    "--eab-hmac-key-stdin",
    is_flag=True,
    help="Read the external account's HMAC key from standard input. An argument would be in ps.",
)
@click.option("--register", is_flag=True, help="Register the account with the authority now.")
@json_option("Print the settings as JSON.")
@pass_context
def acme_command(
    ctx: Context,
    ca: str | None,
    email: str | None,
    acme_server: str | None,
    eab_kid: str | None,
    eab_hmac_key_stdin: bool,
    register: bool,
) -> None:
    """
    Show, or change, which authority certificates are ordered from and as whom.

    Staging issues certificates browsers do not trust, and its rate limits are generous:
    the place to try DNS, ports and hooks before spending production's. ZeroSSL and Google
    ask for an external account; --register proves it now, instead of at the next order.
    """
    settings = AcmeSettings()
    hmac = sys.stdin.readline(2048).rstrip("\r\n") if eab_hmac_key_stdin else None
    changing = any(v is not None for v in (ca, email, acme_server, eab_kid, hmac))
    if changing and ctx.dry_run:
        ctx.logger.info("Would change the certificate authority settings")
        return
    if changing:
        settings.update(
            email=email, ca=ca, acme_server=acme_server, eab_kid=eab_kid, eab_hmac_key=hmac
        )
    if register:
        CertManager(verbose=ctx.verbose).register_account()
        ctx.logger.success("The account is registered")
    view = settings.current()
    if ctx.json_output:
        click.echo(json.dumps(view.to_dict()))
        return
    ctx.logger.key_value(
        "Authority", view.label + ("" if view.trusted else " (browsers do not trust it)")
    )
    ctx.logger.key_value("Directory", view.server or "certbot's default")
    ctx.logger.key_value("Account address", view.email or "none")
    if view.needs_eab:
        ctx.logger.key_value(
            "External account",
            f"key id {view.eab_kid or 'not set'}, HMAC key "
            + ("stored" if view.eab_configured else "not set"),
        )


@click.command("upload", cls=NoustCommand, short_help="Keep a certificate and its key you bring.")
@click.argument("name")
@click.option(
    "--cert",
    "cert_file",
    required=True,
    metavar="FILE",
    help="The certificate (PEM); '-' reads stdin.",
)
@click.option(
    "--key",
    "key_file",
    required=True,
    metavar="FILE",
    help="Its private key (PEM, no passphrase); '-' reads stdin.",
)
@click.option(
    "--chain", "chain_file", metavar="FILE", help="The intermediates (PEM), when they come apart."
)
@json_option("Print the record as JSON.")
@pass_context
def upload_command(
    ctx: Context, name: str, cert_file: str, key_file: str, chain_file: str | None
) -> None:
    """
    Check a certificate and its key, and keep them as NAME.

    A Cloudflare Origin Certificate, a corporate CA's or a purchased one. Before anything
    is written the key must be the certificate's and have no passphrase, and the
    certificate must not have expired and must name something. Serve it with
    'noust cert use APP --upload NAME'. It is never renewed or revoked by Noust; it is
    warned about as it expires.
    """
    if cert_file == "-" and key_file == "-":
        raise click.UsageError("Only one of --cert and --key can come from standard input")
    if ctx.dry_run:
        ctx.logger.info(f"Would keep the certificate {name}")
        return
    kept = CustomCertificates().upload(
        name,
        _read(cert_file, "certificate"),
        _read(key_file, "private_key"),
        _read(chain_file, "chain") if chain_file else "",
        uploaded_by=_actor(),
    )
    if ctx.json_output:
        click.echo(json.dumps(_describe(kept)))
        return
    ctx.logger.success(
        f"{name} is kept: {', '.join(kept.names)} until {(kept.not_after or '')[:10]}"
    )
    ctx.logger.info(f"Serve it with: noust cert use APP --upload {name}")


def _describe(item: CustomCertificate) -> dict[str, object]:
    """
    Args:
        item: An uploaded certificate record.

    Returns:
        Its fields, JSON-ready. Never its key.
    """
    return {
        "name": item.name,
        "names": list(item.names),
        "issuer": item.issuer,
        "not_after": item.not_after,
        "fingerprint": item.fingerprint,
    }


@click.command(
    "uploads", cls=NoustCommand, short_help="List the certificates you uploaded.", read_only=True
)
@json_option("Print the uploads as JSON.")
@pass_context
def uploads_command(ctx: Context) -> None:
    """List the uploaded certificates, what they cover, when they expire and who serves them."""
    uploads = CustomCertificates()
    rows = uploads.uploaded()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "certificates": [
                        {**_describe(r), "used_by": uploads.records.domains_using("custom", r.name)}
                        for r in rows
                    ]
                }
            )
        )
        return
    if not rows:
        ctx.logger.info(
            "No certificate was uploaded. Upload one: noust cert upload NAME --cert F --key F"
        )
        return
    ctx.logger.table(
        ["Name", "Covers", "Expires", "Issuer", "Served by"],
        [
            [
                r.name,
                ", ".join(r.names),
                (r.not_after or "")[:10],
                r.issuer or "-",
                ", ".join(uploads.records.domains_using("custom", r.name)) or "nobody",
            ]
            for r in rows
        ],
    )


@click.command(
    "remove-upload", cls=NoustCommand, short_help="Delete an uploaded certificate and its key."
)
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@pass_context
def remove_upload_command(ctx: Context, name: str, yes: bool) -> None:
    """Delete an uploaded certificate. One an application serves cannot be deleted."""
    if not yes and not click.confirm(f"Delete the uploaded certificate {name} and its key?"):
        return
    CustomCertificates().remove(name)
    ctx.logger.success(f"{name} is deleted")


@click.command(
    "use", cls=NoustCommand, short_help="Choose where an application's certificate comes from."
)
@click.argument("app_domain", metavar="APP")
@click.option("--acme", "use_acme", is_flag=True, help="Have it ordered from the authority.")
@click.option("--upload", "upload_name", metavar="NAME", help="Serve this uploaded certificate.")
@click.option(
    "--dns",
    "dns_account",
    metavar="ACCOUNT",
    help="Prove control with a DNS record in this account's zone (needed for a wildcard).",
)
@click.option("--no-order", is_flag=True, help="Only keep the choice; the next order uses it.")
@json_option("Print the certificate as JSON.")
@pass_context
def use_command(
    ctx: Context,
    app_domain: str,
    use_acme: bool,
    upload_name: str | None,
    dns_account: str | None,
    no_order: bool,
) -> None:
    """
    Choose where APP's certificate comes from: ordered (over HTTP, or DNS with --dns) or uploaded.

    Changing how a certificate that exists is proven orders it again, once: certbot keeps
    the method in the lineage's renewal configuration. A failed order puts the choice back
    and the certificate that served keeps serving.
    """
    validated = validate_domain(app_domain)
    if use_acme == bool(upload_name):
        raise click.UsageError("Give --acme or --upload NAME")
    if upload_name and dns_account:
        raise click.UsageError("--dns is for an ordered certificate, not an uploaded one")
    if ctx.dry_run:
        ctx.logger.info(f"Would change where the certificate of {validated} comes from")
        return
    if upload_name:
        change = certificates.use_uploaded(validated, upload_name, verbose=ctx.verbose)
    else:
        change = certificates.use_acme(
            validated,
            challenge="dns-01" if dns_account else "http-01",
            dns_provider=dns_account,
            order=not no_order,
            verbose=ctx.verbose,
        )
    view = change.view
    if ctx.json_output:
        click.echo(json.dumps({**view.to_dict(), "ordered": change.ordered}))
        return
    ctx.logger.success(
        f"{validated}: "
        + (
            f"serves the uploaded certificate {view.name}"
            if view.source == "custom"
            else f"ordered from {view.authority} over {view.challenge}"
            + (f" through {view.dns_provider}" if view.dns_provider else "")
        )
        + (" (ordered now)" if change.ordered else "")
    )


@click.command(
    "source",
    cls=NoustCommand,
    short_help="Show where an application's certificate comes from.",
    read_only=True,
)
@click.argument("app_domain", metavar="APP")
@json_option("Print the description as JSON.")
@pass_context
def source_command(ctx: Context, app_domain: str) -> None:
    """Show where APP's certificate comes from, and what is served."""
    view = certificates.describe(validate_domain(app_domain), verbose=ctx.verbose)
    if ctx.json_output:
        click.echo(json.dumps(view.to_dict()))
        return
    ctx.logger.key_value("Application", view.app)
    ctx.logger.key_value(
        "Comes from",
        f"the uploaded certificate {view.name}"
        if view.source == "custom"
        else f"another lineage, {view.name}"
        if view.source == "lineage"
        else f"{view.authority}, proven over {view.challenge}"
        + (f" through {view.dns_provider}" if view.dns_provider else ""),
    )
    ctx.logger.key_value("Serves TLS", "yes" if view.tls else "no")
    ctx.logger.key_value("Names", ", ".join(view.names))
    if view.certificate:
        cert = view.certificate
        ctx.logger.key_value(
            "Certificate",
            f"{cert['name']} until {cert['expires_on']} ({cert['days_remaining']} days), "
            f"issued by {cert['issuer'] or 'unknown'}",
        )
