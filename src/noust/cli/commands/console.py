# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust console`` command group: the console on a public name, with TLS.

The console listens on loopback and is reached through an SSH tunnel. ``noust
console expose`` is the guided alternative for an operator who wants a browser
and a name: an nginx site, a Let's Encrypt certificate and, optionally, a list
of who may connect, after checking that every account holds a second factor.
``unexpose`` removes exactly what ``expose`` made. The work is in
:mod:`noust.managers.console_exposure`; these handlers only ask, show progress
and print.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context


@click.group("console", cls=NoustGroup)
def cli() -> None:
    """Publish the console on a name of its own, over TLS, or take it off again."""


def _manager(ctx: Context) -> Any:
    """
    Build the exposure manager.

    Args:
        ctx: The command's context.

    Returns:
        A :class:`~noust.managers.console_exposure.ConsoleExposure`.
    """
    from noust.managers.console_exposure import ConsoleExposure

    return ConsoleExposure(verbose=ctx.verbose)


@cli.command("expose")
@click.option(
    "--domain",
    required=True,
    metavar="NAME",
    help="The public name, already pointing at this server (console.example.com).",
)
@click.option(
    "--allow-ip",
    "allow_ips",
    multiple=True,
    metavar="ADDR/CIDR",
    help="Only these addresses or networks may reach the console. Repeatable. Without it "
    "anyone can open the sign-in page; the second factor is then all that stops them.",
)
@click.option(
    "--force-ens",
    is_flag=True,
    help="Publish although the ENS profile is on (it keeps the console on loopback). Asks for "
    "confirmation, needs --allow-ip, and is audited.",
)
@click.option(
    "--skip-dns-check",
    is_flag=True,
    help="Do not require the name to resolve to this server's addresses (a server behind NAT).",
)
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the outcome as JSON.")
@pass_context
def expose_command(
    ctx: Context,
    domain: str,
    allow_ips: tuple[str, ...],
    force_ens: bool,
    skip_dns_check: bool,
    assume_yes: bool,
) -> None:
    """
    Open the console at https://DOMAIN, with a certificate, instead of an SSH tunnel.

    Writes an nginx site for DOMAIN that proxies to the console on 127.0.0.1,
    obtains a Let's Encrypt certificate, makes the console believe nginx's
    forwarding headers (it restarts) and answer only that name. It refuses
    when an account can sign in without a second factor, when DOMAIN belongs
    to an application or to a site Noust did not write, when DOMAIN does not
    point at this server, and under the ENS profile unless forced.
    'noust console unexpose' removes exactly what this made.
    """
    manager = _manager(ctx)
    logger = ctx.logger
    steps = manager.plan(
        domain, allow_ips=allow_ips, force_ens=force_ens, skip_dns_check=skip_dns_check
    )
    if ctx.dry_run:
        for step in steps:
            logger.info(f"would {step}")
        return
    if force_ens and not assume_yes and not _confirm_ens_override(ctx):
        logger.info("Cancelled: the console stays on loopback")
        return
    if not assume_yes and not ctx.json_output:
        logger.info("This will:")
        for step in steps:
            logger.info(f"  - {step}")
        if not click.confirm(f"Publish the console at https://{domain}?", default=False):
            logger.info("Cancelled")
            return

    def progress(line: str) -> None:
        if not ctx.json_output:
            logger.substep(line)

    result = manager.expose(
        domain,
        allow_ips=allow_ips,
        force_ens=force_ens,
        skip_dns_check=skip_dns_check,
        restart="now",
        actor=_actor(),
        progress=progress,
    )
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger.success(f"The console is published at {result.url}")
    if result.allow_ips:
        logger.key_value("Allowed", ", ".join(result.allow_ips))
    for note in result.notes:
        logger.warning(note)
    logger.info("Remove it with: noust console unexpose")


def _confirm_ens_override(ctx: Context) -> bool:
    """
    Ask the operator to confirm publishing the console under the ENS profile.

    Args:
        ctx: The command's context.

    Returns:
        True when they confirmed.
    """
    ctx.logger.warning(
        "The ENS profile keeps the console on loopback because it is root-equivalent. "
        "Publishing it is a deviation from the profile, and is audited."
    )
    return bool(
        click.confirm(
            "Publish the console anyway, restricted to the --allow-ip list?", default=False
        )
    )


def _actor() -> str:
    """
    Name the operator at this terminal, for the record of who exposed the console.

    Returns:
        The audit actor's label.
    """
    from noust.core import audit

    return audit.cli_actor().label


@cli.command("unexpose")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the outcome as JSON.")
@pass_context
def unexpose_command(ctx: Context, assume_yes: bool) -> None:
    """
    Take the console off its public name, back to an SSH tunnel only.

    Removes what 'noust console expose' made and nothing else: the nginx site
    and the certificate only if it created them, the trusted proxy of the
    console's unit and the two settings it changed. The console restarts.
    """
    manager = _manager(ctx)
    logger = ctx.logger
    state = manager.state()
    if state is None:
        if ctx.json_output:
            click.echo(json.dumps({"domain": "", "removed": []}))
        else:
            logger.info("The console is not published")
        return
    if ctx.dry_run:
        logger.info(f"would remove the publication of the console at https://{state.domain}")
        return
    if not assume_yes and not ctx.json_output:
        if not click.confirm(
            f"Take the console off https://{state.domain}? It is reachable through an SSH tunnel only",
            default=False,
        ):
            logger.info("Cancelled")
            return
    result = manager.unexpose(restart="now")
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    for item in result.removed:
        (logger.warning if item.startswith("NOT removed") else logger.success)(item)
    logger.success(f"The console is no longer published at https://{result.domain}")


@cli.command("status", read_only=True)
@json_option("Print the state as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Show whether the console is published, where, and whether it is still in place."""
    status = _manager(ctx).status()
    if ctx.json_output:
        click.echo(json.dumps(status))
        return
    logger = ctx.logger
    if not status["exposed"] and "domain" not in status:
        logger.info("The console is not published: it answers on loopback only.")
        if not status["ens_allows"]:
            logger.info(
                "The ENS profile keeps it that way unless 'noust console expose --force-ens'."
            )
        return
    logger.key_value("Address", status["url"])
    logger.key_value("Allowed", ", ".join(status["allow_ips"]) or "anyone")
    logger.key_value("State", status["state"])
    for problem in status["problems"]:
        logger.warning(problem)
