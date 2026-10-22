# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust cert ca``: the server's own certificate authority, for names that only resolve inside a
private network.

Presentation over :mod:`noust.managers.internal_ca` (the authority and what it signs),
:mod:`noust.managers.ca_trust` (how to trust it on each device) and
:mod:`noust.deployers.internal_certificates` (serving an application with one and keeping them
renewed), which ``/api/certs/ca`` calls too. Attached to the ``cert`` group
(:mod:`noust.cli.commands.cert`).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.fs import get_fs
from noust.deployers import internal_certificates
from noust.managers import ca_trust
from noust.managers.internal_ca import LEAF_DAYS, CaStatus, InternalCA, NoAuthorityError

#: The mode of the authority's certificate written to a file: it is public.
_PUBLIC_MODE = 0o644


def _require(authority: InternalCA) -> CaStatus:
    """
    Args:
        authority: The authority.

    Returns:
        Its state.

    Raises:
        NoAuthorityError: This server has none.
    """
    found = authority.status()
    if found is None:
        raise NoAuthorityError(
            "This server has no internal certificate authority",
            details="Make it: noust cert ca init",
        )
    return found


def _status_lines(ctx: Context, status: CaStatus, trusted_here: bool) -> None:
    """
    Show the authority for a person.

    Args:
        ctx: The CLI context.
        status: The authority.
        trusted_here: Whether this server trusts it.
    """
    logger = ctx.logger
    logger.key_value("Authority", status.subject)
    logger.key_value("Fingerprint", status.fingerprint)
    logger.key_value("Valid until", f"{status.not_after[:10]} ({status.days_left} days)")
    logger.key_value("Signs for", ", ".join(status.permitted_dns) or "no names")
    logger.key_value("And for addresses in", ", ".join(status.permitted_networks) or "none")
    logger.key_value("Certificates kept", str(status.issued))
    logger.key_value("Trusted by this server", "yes" if trusted_here else "no")
    for warning in status.warnings:
        logger.warning(warning)


@click.group("ca", cls=NoustGroup)
def ca() -> None:
    """The server's own certificate authority, for names that only resolve inside a network."""


@ca.command("init")
@click.option(
    "--permit-dns",
    "permit_dns",
    multiple=True,
    metavar="SUFFIX",
    help="Also sign for names under this suffix (corp.example.com). Repeatable.",
)
@click.option(
    "--permit-network",
    "permit_networks",
    multiple=True,
    metavar="RANGE",
    help="Also sign for addresses in this private range (10.20.0.0/16). Repeatable.",
)
@click.option(
    "--only",
    is_flag=True,
    help="Sign for exactly what --permit-dns and --permit-network name, not for the defaults "
    "(lan, internal, home.arpa, local, localhost, test and the private ranges).",
)
@click.option(
    "--days",
    type=click.IntRange(30, 7300),
    default=3650,
    show_default=True,
    help="How long the authority is valid.",
)
@click.option("--name", metavar="TEXT", help="What the authority is called on a device.")
@click.option(
    "--trust-here", is_flag=True, help="Also trust it on this server (programs here use it)."
)
@json_option("Print the authority as JSON.")
@pass_context
def init_command(
    ctx: Context,
    permit_dns: tuple[str, ...],
    permit_networks: tuple[str, ...],
    only: bool,
    days: int,
    name: str | None,
    trust_here: bool,
) -> None:
    """
    Make this server's authority.

    It can only ever sign for the suffixes and private ranges it is made with: the limit is in its
    certificate, critical, so every device that trusts it enforces it, and a leaked key cannot
    impersonate a public name. It is made once and never again by itself, because a new one has to
    be trusted again on every device.
    """
    authority = InternalCA()
    if ctx.dry_run:
        ctx.logger.info("Would make the internal certificate authority")
        return
    with ctx.quiet():
        status = authority.init(
            permit_dns=permit_dns, permit_networks=permit_networks, only=only, days=days, name=name
        )
        if trust_here:
            ca_trust.ServerTrust().install(authority.root_pem())
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    ctx.logger.success("The internal certificate authority is made")
    _status_lines(ctx, status, trust_here)
    ctx.logger.info("Trust it on the devices that open your sites: noust cert ca trust TARGET")
    ctx.logger.info("Serve an application with it: noust cert use APP --internal")


@ca.command("status", read_only=True)
@json_option("Print the authority as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Show the authority: what it signs for, when it expires, and whether this server trusts it."""
    authority = InternalCA()
    status = authority.status()
    trusted = status is not None and ca_trust.ServerTrust().installed(authority.root_pem())
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "authority": status.to_dict() if status else None,
                    "trusted_here": trusted,
                }
            )
        )
        return
    if status is None:
        ctx.logger.info("This server has no internal certificate authority.")
        ctx.logger.info("Make it: noust cert ca init")
        return
    _status_lines(ctx, status, trusted)


@ca.command("list", read_only=True)
@json_option("Print the certificates as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the certificates the authority signed that are kept, soonest to expire first."""
    authority = InternalCA()
    issued = authority.issued() if authority.exists() else []
    if ctx.json_output:
        click.echo(json.dumps({"certificates": [item.to_dict() for item in issued]}))
        return
    if not issued:
        ctx.logger.info("The authority has signed no certificate that is kept.")
        ctx.logger.info("Sign one: noust cert ca issue NAME HOST...")
        return
    ctx.logger.table(
        ["Name", "Covers", "Expires", "Left", "Served by"],
        [
            [
                item.name,
                ", ".join(item.names),
                item.not_after[:10],
                f"{item.days_left} days" + (" (renewing)" if item.expiring else ""),
                ", ".join(item.apps) or "nobody",
            ]
            for item in issued
        ],
    )


@ca.command("issue")
@click.argument("label")
@click.argument("hosts", nargs=-1, required=True, metavar="HOST...")
@click.option(
    "--days",
    type=click.IntRange(1, 397),
    default=LEAF_DAYS,
    show_default=True,
    help="How long the certificate is valid.",
)
@json_option("Print the certificate as JSON.")
@pass_context
def issue_command(ctx: Context, label: str, hosts: tuple[str, ...], days: int) -> None:
    """
    Sign a certificate for some names and keep it as internal-LABEL.

    HOST is a name (wiki.home.arpa, *.corp.lan) or an address (10.0.0.5), at least one a name. Serve
    it with: noust cert use APP --upload internal-LABEL
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would sign a certificate for {', '.join(hosts)}")
        return
    with ctx.quiet():
        kept = InternalCA().issue(label, hosts, days=days)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "name": kept.name,
                    "names": list(kept.names),
                    "not_after": kept.not_after,
                    "fingerprint": kept.fingerprint,
                }
            )
        )
        return
    ctx.logger.success(
        f"{kept.name} is kept: {', '.join(kept.names)} until {(kept.not_after or '')[:10]}"
    )
    ctx.logger.info(f"Serve it with: noust cert use APP --upload {kept.name}")


@ca.command("renew")
@click.argument("name", required=False)
@click.option("--due", is_flag=True, help="Renew every certificate that is within its window.")
@json_option("Print what was renewed as JSON.")
@pass_context
def renew_command(ctx: Context, name: str | None, due: bool) -> None:
    """
    Sign a certificate again, for the same names and as long, and reload the sites that serve it.

    The network maintenance pass does this by itself thirty days before one expires; this is for
    doing it now.
    """
    if bool(name) == due:
        raise click.UsageError("Name a certificate (internal-wiki), or give --due")
    if ctx.dry_run:
        ctx.logger.info("Would sign again " + (name or "the certificates that are due"))
        return
    with ctx.quiet():
        renewed = [internal_certificates.renew(name)] if name else internal_certificates.renew_due()
    if ctx.json_output:
        click.echo(json.dumps({"renewed": renewed}))
        return
    if not renewed:
        ctx.logger.info("Nothing is within its renewal window.")
        return
    for item in renewed:
        ctx.logger.success(f"{item} is signed again")


@ca.command("root", read_only=True)
@click.option("--der", is_flag=True, help="The binary form Windows, Android and Java import.")
@click.option(
    "--out",
    "out",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Write it to this file instead of printing it (needed with --der).",
)
@json_option("Print the certificate and its fingerprint as JSON.")
@pass_context
def root_command(ctx: Context, der: bool, out: Path | None) -> None:
    """
    Print the authority's certificate: public, what a device installs. The key never leaves.

    Compare the fingerprint it prints with the one on the device before trusting the file.
    """
    authority = InternalCA()
    status = _require(authority)
    if der and out is None:
        raise click.UsageError("--der is binary: give --out FILE")
    if out is not None:
        fs = get_fs()
        fs.write_bytes(
            out, authority.root_der() if der else authority.root_pem().encode(), mode=_PUBLIC_MODE
        )
        if not ctx.json_output:
            ctx.logger.success(f"Wrote {out} ({'DER' if der else 'PEM'})")
            ctx.logger.key_value("Fingerprint", status.fingerprint)
        else:
            click.echo(json.dumps({"written": str(out), "fingerprint": status.fingerprint}))
        return
    if ctx.json_output:
        click.echo(
            json.dumps({"certificate": authority.root_pem(), "fingerprint": status.fingerprint})
        )
        return
    click.echo(authority.root_pem(), nl=False)


@ca.command(
    "trust",
    read_only=lambda params: not params.get("here"),
)
@click.argument("target", required=False, type=click.Choice(ca_trust.TARGETS))
@click.option("--here", is_flag=True, help="Trust the authority on this server.")
@click.option("--remove", is_flag=True, help="With --here: stop trusting it on this server.")
@json_option("Print the steps as JSON.")
@pass_context
def trust_command(ctx: Context, target: str | None, here: bool, remove: bool) -> None:
    """
    Say how to trust the authority on a device (TARGET), or trust it on this server (--here).

    Nothing is changed on any device but this server: the steps are text to follow on the device,
    with the file to carry and the fingerprint to compare.
    """
    if here and target:
        raise click.UsageError("Give a TARGET, or --here, not both")
    if remove and not here:
        raise click.UsageError("--remove goes with --here")
    authority = InternalCA()
    trust = ca_trust.ServerTrust()
    if here:
        if ctx.dry_run:
            ctx.logger.info(
                "Would " + ("stop trusting" if remove else "trust") + " it on this server"
            )
            return
        if remove:
            was = trust.remove()
            ctx.logger.success(
                "This server no longer trusts the authority" if was else "It was not trusted here"
            )
            return
        _require(authority)
        store = trust.install(authority.root_pem())
        ctx.logger.success(f"This server trusts the authority now ({store.title})")
        ctx.logger.info(
            "Programs with a store of their own (Java, Node, Firefox, a container) need the "
            "steps for them: noust cert ca trust java"
        )
        return
    status = _require(authority)
    host = socket.gethostname() or "server"
    if target is None:
        guides = ca_trust.guides(status.fingerprint, host)
        if ctx.json_output:
            click.echo(json.dumps({"guides": [item.to_dict() for item in guides]}))
            return
        ctx.logger.key_value("Fingerprint", status.fingerprint)
        ctx.logger.table(["Target", "For"], [[item.id, item.title] for item in guides])
        ctx.logger.info("Steps for one: noust cert ca trust TARGET")
        ctx.logger.info("The file to carry: noust cert ca root --out ca.crt (--der for ca.cer)")
        return
    chosen = ca_trust.guide(target, status.fingerprint, host)
    if ctx.json_output:
        click.echo(json.dumps(chosen.to_dict()))
        return
    ctx.logger.key_value("For", chosen.title)
    ctx.logger.key_value("File", "ca.cer (--der)" if chosen.file == "der" else "ca.crt")
    ctx.logger.key_value("Fingerprint", status.fingerprint)
    for step in chosen.steps:
        click.echo(f"  {step}")
    if chosen.note:
        ctx.logger.info(chosen.note)


@ca.command("remove")
@click.option("--force", is_flag=True, help="Also when certificates it signed are still kept.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@pass_context
def remove_command(ctx: Context, force: bool, yes: bool) -> None:
    """
    Delete the authority and its key.

    Devices that trusted it keep a root that signs nothing new, and what it signed stops being
    renewable. Making another means trusting the new root again on every device.
    """
    if ctx.dry_run:
        ctx.logger.info("Would delete the internal certificate authority")
        return
    authority = InternalCA()
    _require(authority)
    trusted_here = ca_trust.ServerTrust().installed(authority.root_pem())
    if not yes and not click.confirm(
        "Delete the authority and its key? Certificates it signed stop being renewable, and a "
        "new one has to be trusted again on every device",
        default=False,
    ):
        ctx.logger.info("Cancelled")
        return
    authority.remove(force=force)
    ctx.logger.success("The authority is deleted")
    if trusted_here:
        ctx.logger.info("This server still trusts it: noust cert ca trust --here --remove")
