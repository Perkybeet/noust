# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust dns server``: Noust DNS on this server, a name server for your own domains.

Presentation over :mod:`noust.managers.authdns.server`, which ``/api/dns/noust/server`` calls
too. Attached to the ``dns`` group (:mod:`noust.cli.commands.dns`).

A name server is a service the whole internet depends on and can ask, so it is an option and never
a default: setting one up prints what that takes on first (availability, and being a tool others
could try to turn against third parties) and what Noust does about the second. The key a primary
signs its transfers with comes out of ``noust dns server key`` on standard output and goes into a
secondary through ``--key-stdin``, never through an argument: everything on a command line is
visible in ``ps`` to every local user.
"""

from __future__ import annotations

import json
import sys

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.audit.actor import cli_actor
from noust.core.authdns_store import PRIMARY, SECONDARY
from noust.core.server_text import say
from noust.managers.authdns.server import WARNINGS, AuthDnsServers, ServerStatus, TransferKey


def _show(ctx: Context, status: ServerStatus) -> None:
    """
    Show Noust DNS on this server for a person.

    Args:
        ctx: The CLI context.
        status: Its state.
    """
    logger = ctx.logger
    logger.key_value("Program", f"{status.engine} {status.version or '(not installed)'}")
    logger.key_value("Running", "yes" if status.running else "no")
    if not status.configured:
        logger.key_value("Set up", "no")
        if status.candidates:
            logger.key_value("It could answer on", ", ".join(status.candidates))
        for zone in status.foreign_zones:
            logger.warning(f"This machine already serves {zone} with a Knot of its own")
        logger.info("Set it up: noust dns server setup --role primary --hostname ns1.example.net")
        return
    logger.key_value("Role", status.role or "")
    logger.key_value("Name", status.hostname or "(none)")
    logger.key_value("Answers on", ", ".join(status.listen))
    if status.role == SECONDARY:
        logger.key_value("Copies from", status.primary_address or "")
    logger.key_value("Zones", str(status.zones))
    for secondary in status.secondaries:
        logger.key_value(
            "Secondary", f"{secondary['address']} {secondary['hostname'] or ''}".strip()
        )


@click.group("server", cls=NoustGroup)
def server() -> None:
    """Noust DNS on this server: install it, make it a primary or a secondary, and remove it."""


@server.command("status", read_only=True)
@json_option("Print the state as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Show Noust DNS on this server: whether it is installed, its role and its zones."""
    status = AuthDnsServers().status()
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    _show(ctx, status)


@server.command("install")
@click.option("--epel", is_flag=True, help="Agree to enable EPEL, where Knot comes from on RHEL.")
@json_option("Print the state as JSON.")
@pass_context
def install_command(ctx: Context, epel: bool) -> None:
    """Install Knot DNS, the program that answers; configure nothing."""
    manager = AuthDnsServers()
    if ctx.dry_run:
        ctx.logger.info("Would install Knot DNS")
        return
    with ctx.quiet():
        status = manager.install(epel=epel, on_output=ctx.logger.substep)
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    ctx.logger.success(f"Knot DNS {status.version} is installed")
    ctx.logger.info("Set it up: noust dns server setup --role primary --hostname ns1.example.net")


@server.command("setup")
@click.option(
    "--role",
    type=click.Choice([PRIMARY, SECONDARY]),
    required=True,
    help="A primary holds the zones; a secondary copies them from a primary.",
)
@click.option("--hostname", metavar="NAME", help="The name this server has as a name server.")
@click.option(
    "--listen",
    "listen",
    multiple=True,
    metavar="ADDRESS",
    help="An address to answer on. Repeatable. The server's public addresses by default.",
)
@click.option("--primary", "primary_address", metavar="ADDRESS", help="A secondary: the primary.")
@click.option(
    "--key-stdin",
    is_flag=True,
    help="A secondary: read the primary's transfer key from one line of standard input.",
)
@click.option("--epel", is_flag=True, help="Agree to enable EPEL, where Knot comes from on RHEL.")
@json_option("Print the state as JSON.")
@pass_context
def setup_command(
    ctx: Context,
    role: str,
    hostname: str | None,
    listen: tuple[str, ...],
    primary_address: str | None,
    key_stdin: bool,
    epel: bool,
) -> None:
    """
    Make this server a primary or a secondary of Noust DNS.

    It installs Knot if it is not here, opens port 53 in the firewall and answers on the addresses
    given. It is an option, not a default: read what it takes on first with
    ``noust dns server status --json``.
    """
    key: TransferKey | None = None
    if key_stdin:
        secret = sys.stdin.readline(4096).strip()
        if not secret:
            raise click.UsageError("Nothing was read from standard input")
        key = TransferKey("noust-xfr", "hmac-sha256", secret)
    if ctx.dry_run:
        ctx.logger.info(f"Would set this server up as a {role} of Noust DNS")
        return
    for code in () if ctx.json_output else WARNINGS:
        sentence = say(code)
        ctx.logger.warning(sentence.text)
        if sentence.hint:
            ctx.logger.info(sentence.hint)
    with ctx.quiet():
        status = AuthDnsServers().setup(
            role,
            hostname=hostname,
            listen=list(listen) or None,
            primary_address=primary_address,
            transfer_key=key,
            epel=epel,
            actor=cli_actor().label,
            on_output=ctx.logger.substep,
        )
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    ctx.logger.success(f"This server is a {role} of Noust DNS")
    _show(ctx, status)
    if role == PRIMARY:
        ctx.logger.info("Add a secondary: noust dns server add-secondary ADDRESS --hostname NAME")
        ctx.logger.info("Make a zone: noust dns zone create example.com")
    else:
        ctx.logger.info("It copies every zone the primary lists; there is nothing more to do here.")


@server.command("key")
@json_option("Print the key as JSON.")
@pass_context
def key_command(ctx: Context) -> None:
    """
    Print the key a primary signs its transfers with, for a secondary's --key-stdin.

    Anyone who holds it can ask the primary for its zones, which are public data, and nothing
    else; still, it is a secret: it goes on standard input, never in an argument.
    """
    key = AuthDnsServers().transfer_key()
    if ctx.json_output:
        click.echo(json.dumps(key.to_dict()))
        return
    click.echo(key.secret)


@server.command("add-secondary")
@click.argument("address", metavar="ADDRESS")
@click.option("--hostname", metavar="NAME", help="The name it has as a name server.")
@json_option("Print the secondary as JSON.")
@pass_context
def add_secondary_command(ctx: Context, address: str, hostname: str | None) -> None:
    """Let a server copy this primary's zones, and tell it when they change."""
    if ctx.dry_run:
        ctx.logger.info(f"Would let {address} copy the zones of this primary")
        return
    with ctx.quiet():
        added = AuthDnsServers().add_secondary(address, hostname, actor=cli_actor().label)
    if ctx.json_output:
        click.echo(json.dumps(added.to_dict()))
        return
    ctx.logger.success(f"{added.address} may copy the zones of this primary")
    ctx.logger.info("Set it up as a secondary there: noust dns server setup --role secondary")


@server.command("remove-secondary")
@click.argument("address", metavar="ADDRESS")
@pass_context
def remove_secondary_command(ctx: Context, address: str) -> None:
    """Stop copying this primary's zones to a server."""
    if ctx.dry_run:
        ctx.logger.info(f"Would stop copying zones to {address}")
        return
    with ctx.quiet():
        removed = AuthDnsServers().remove_secondary(address, actor=cli_actor().label)
    if removed:
        ctx.logger.success(f"{address} no longer gets this primary's zones")
    else:
        ctx.logger.info(f"{address} was not a secondary of this primary")


@server.command("remove")
@click.option(
    "--delete-zones",
    is_flag=True,
    help="Also delete the zones and records this server serves.",
)
@pass_context
def remove_command(ctx: Context, delete_zones: bool) -> None:
    """
    Stop the name server and take Noust DNS off this server.

    The Knot package and the signing keys stay: the keys are the only copy of what the DS
    record at the registrar points at.
    """
    if ctx.dry_run:
        ctx.logger.info("Would stop Noust DNS and put the previous Knot configuration back")
        return
    with ctx.quiet():
        AuthDnsServers().remove(delete_zones=delete_zones, actor=cli_actor().label)
    ctx.logger.success("Noust DNS is removed from this server")
