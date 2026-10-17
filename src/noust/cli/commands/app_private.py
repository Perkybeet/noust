# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app policy private``: an application that answers a private network only.

A tailnet or a WireGuard interface, named instead of its ranges. It is the site's list of allowed
addresses set by :mod:`noust.deployers.private_access`, which ``/api/apps/{d}/private`` calls too;
this module only parses, presents and asks.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.deployers import access, private_access
from noust.managers.private_networks import PrivateNetwork
from noust.validators.domain import validate_domain


@click.group("private", cls=NoustGroup)
def private() -> None:
    """Publish an application on a private network only: a tailnet or a WireGuard interface."""


def _describe(network: PrivateNetwork) -> str:
    """
    Args:
        network: A private network.

    Returns:
        What to tell an operator about it, in a line.
    """
    state = "up" if network.up else f"down ({network.problem})"
    own = f", this server is {', '.join(network.addresses)}" if network.addresses else ""
    return f"{network.kind} {network.interface or ''} {state}{own}".replace("  ", " ")


def _print(ctx: Context, status: private_access.PrivateStatus) -> None:
    """
    Show whether an application is private, for a human or as JSON.

    Args:
        ctx: The CLI context.
        status: What the operation returned.
    """
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Application", status.app)
    if not status.applicable:
        logger.warning(f"A policy does not reach this site: {status.reason}")
    if status.private:
        logger.key_value(
            "Private", "yes, to " + (", ".join(status.networks) or ", ".join(status.allow_ips))
        )
    elif status.public_ranges:
        logger.key_value("Private", f"no, it also allows {', '.join(status.public_ranges)}")
    else:
        logger.key_value("Private", "no, it answers everybody")
    if status.allow_ips:
        logger.key_value("Allowed addresses", ", ".join(status.allow_ips))
    if status.tailnet_name:
        logger.key_value("Name on the tailnet", status.tailnet_name)
    if status.tailnet_certificate:
        logger.key_value("Served with", status.tailnet_certificate)


@private.command("networks", read_only=True)
@json_option("Print the networks as JSON.")
@pass_context
def networks_command(ctx: Context) -> None:
    """List the private networks this server is on."""
    found = private_access.networks()
    if ctx.json_output:
        click.echo(json.dumps([network.to_dict() for network in found]))
        return
    if not found:
        ctx.logger.info(
            "This server is on no private network Noust knows: no Tailscale and no WireGuard "
            "interface. Name ranges yourself with --cidr."
        )
        return
    ctx.logger.table(
        ["Network", "Ranges", "Detail"],
        [[network.name, ", ".join(network.cidrs), _describe(network)] for network in found],
    )


@private.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the answer as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Say whether an application answers a private network only, and which."""
    _print(ctx, private_access.status(validate_domain(domain), verbose=ctx.verbose))


@private.command("enable")
@click.argument("domain")
@click.option("--network", help="A network of 'noust app policy private networks': tailscale, wg0.")
@click.option(
    "--cidr", "cidrs", multiple=True, metavar="RANGE", help="A private range to allow as well."
)
@click.option(
    "--accept-lockout",
    is_flag=True,
    help="Go ahead although the list refuses the address you are connected from.",
)
@json_option("Print the answer as JSON.")
@pass_context
def enable_command(
    ctx: Context,
    domain: str,
    network: str | None,
    cidrs: tuple[str, ...],
    accept_lockout: bool,
) -> None:
    """
    Make an application answer a private network only.

    The site's allowed addresses become the network's ranges; anybody else is refused with 403.
    Your own SSH address is checked first: connected over the internet, you would be refused
    yourself unless you pass --accept-lockout.
    """
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would allow only {network or ', '.join(cidrs) or 'a private network'}")
        return
    address = access.ssh_client_address()
    status = private_access.enable(
        validated,
        network=network,
        cidrs=cidrs,
        operator_addresses=[address] if address else [],
        accept_lockout=accept_lockout,
        verbose=ctx.verbose,
    )
    if not ctx.json_output:
        ctx.logger.success(f"{validated} answers a private network only")
    _print(ctx, status)


@private.command("disable")
@click.argument("domain")
@click.option(
    "--accept-lockout",
    is_flag=True,
    help="Go ahead although the list refuses the address you are connected from.",
)
@json_option("Print the answer as JSON.")
@pass_context
def disable_command(ctx: Context, domain: str, accept_lockout: bool) -> None:
    """Let an application answer anybody again: the private ranges leave the allowed addresses."""
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would take the private ranges off the addresses {validated} allows")
        return
    address = access.ssh_client_address()
    status = private_access.disable(
        validated,
        operator_addresses=[address] if address else [],
        accept_lockout=accept_lockout,
        verbose=ctx.verbose,
    )
    if not ctx.json_output:
        ctx.logger.success(f"{validated} no longer answers a private network only")
    _print(ctx, status)


@private.command("certificate")
@click.argument("domain")
@json_option("Print the certificate as JSON.")
@pass_context
def certificate_command(ctx: Context, domain: str) -> None:
    """
    Serve an application with the certificate Tailscale issues for this server's name.

    The application must answer on the tailnet's name (web.tail1234.ts.net) and nothing else.
    The certificate is asked for again by the network upkeep before it expires.
    """
    validated = validate_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would ask Tailscale for a certificate and serve {validated} with it")
        return
    issued = private_access.tailnet_certificate(validated, verbose=ctx.verbose)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "name": issued.name,
                    "hostname": issued.hostname,
                    "not_after": issued.not_after,
                    "applications": list(issued.applications),
                }
            )
        )
        return
    ctx.logger.success(
        f"{validated} is served with the tailnet's certificate for {issued.hostname}"
    )
    ctx.logger.key_value("Expires", issued.not_after)
