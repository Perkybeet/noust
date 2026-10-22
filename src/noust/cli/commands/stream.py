# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust stream``: TCP and UDP ports published in front of a backend.

A presentation layer over :mod:`noust.managers.stream_exposure`, which
``/api/streams`` calls too. Not everything an application offers is HTTP: a game
server, a message broker, a DNS or WireGuard endpoint. nginx's ``stream`` module
carries the port, and the firewall is told to allow it for the addresses given.
"""

from __future__ import annotations

import ipaddress
import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.managers.stream_exposure import DEFAULT_TIMEOUT, StreamExposures
from noust.validators.domain import validate_domain


@click.group("stream", cls=NoustGroup)
def cli() -> None:
    """Publish a TCP or UDP port in front of a backend, through nginx and the firewall."""


def parse_target(target: str) -> tuple[str, int]:
    """
    Split ``PORT``, ``HOST:PORT`` or ``[V6]:PORT`` into an address and a port.

    Args:
        target: What the operator typed after ``--to``.

    Returns:
        ``(host, port)``; the host is this machine's when only a port was given.

    Raises:
        click.UsageError: It is neither a port nor an address and a port.
    """
    text = target.strip()
    if text.isdigit():
        return "127.0.0.1", int(text)
    host, separator, port = text.rpartition(":")
    host = host.strip("[]")
    if not separator or not port.isdigit():
        raise click.UsageError(f"--to wants PORT or HOST:PORT, not {target!r}")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        raise click.UsageError(f"{host!r} is not an IP address") from None
    return host, int(port)


@cli.command("add")
@click.argument("name")
@click.option(
    "--protocol",
    type=click.Choice(["tcp", "udp"]),
    default="tcp",
    show_default=True,
    help="What the port carries. nginx cannot publish SCTP.",
)
@click.option("--port", "listen_port", required=True, type=int, help="The port to publish.")
@click.option(
    "--to",
    "target",
    metavar="[HOST:]PORT",
    help="The backend: a port on this machine, or an address and port. "
    "The application's own port with --app.",
)
@click.option(
    "--allow",
    "sources",
    multiple=True,
    metavar="ADDRESS",
    help="An address or network that may reach it; repeat. Without one, anyone can.",
)
@click.option(
    "--timeout",
    "timeout_seconds",
    type=int,
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help="Seconds a quiet connection (TCP) or session (UDP) is kept.",
)
@click.option("--app", "app_domain", metavar="APP", help="The application it is for.")
@click.option("--plan", is_flag=True, help="Say what would be done and change nothing.")
@json_option("Print the result as JSON.")
@pass_context
def add_command(
    ctx: Context,
    name: str,
    protocol: str,
    listen_port: int,
    target: str | None,
    sources: tuple[str, ...],
    timeout_seconds: int,
    app_domain: str | None,
    plan: bool,
) -> None:
    """
    Publish PORT and carry it to the backend, opening it in the firewall.

    Every question about the port is answered first: whether something listens on it,
    whether an exposure or a public database holds it, whether it is SSH's or the
    console's. nginx tests the whole configuration before it reloads, and its words
    are the error. Only what is made here is closed again by 'noust stream remove'.

    Example: noust stream add minecraft --port 25565 --to 25565 --allow 203.0.113.0/24
    """
    host, port = parse_target(target) if target else ("127.0.0.1", None)
    streams = StreamExposures()
    request: dict[str, Any] = {
        "protocol": protocol,
        "listen_port": listen_port,
        "target_port": port,
        "target_host": host,
        "sources": sources,
        "timeout_seconds": timeout_seconds,
        "app_domain": validate_domain(app_domain) if app_domain else None,
    }
    if plan:
        said = streams.plan(name, **request)
        if ctx.json_output:
            click.echo(json.dumps(said.to_dict()))
            return
        ctx.logger.key_value("Would publish", f"{protocol.upper()} {listen_port}")
        ctx.logger.key_value("To", said.exposure.target)
        ctx.logger.key_value("Allowed from", ", ".join(said.exposure.sources) or "anyone")
        ctx.logger.key_value("File", str(said.file))
        if said.loader == "write":
            ctx.logger.key_value("Also", f"the block that makes nginx read {streams.directory}")
        for warning in said.warnings:
            ctx.logger.warning(warning)
        return
    from noust.core.audit.actor import cli_actor

    result = streams.open(name, actor=cli_actor().label, **request)
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    verb = "Would publish" if result.rehearsal else "Published"
    ctx.logger.success(
        f"{verb} {protocol.upper()} {listen_port} to {result.exposure.target} as {name}"
    )
    for warning in result.warnings:
        ctx.logger.warning(warning)
    if result.firewall:
        ctx.logger.info(result.firewall)


@cli.command("list", read_only=True)
@json_option("Print the ports as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the published ports and how far each one got."""
    rows = StreamExposures().all()
    if ctx.json_output:
        click.echo(json.dumps({"exposures": [row.to_dict() for row in rows]}))
        return
    if not rows:
        ctx.logger.info("No TCP or UDP port is published. Publish one: noust stream add")
        return
    ctx.logger.table(
        ["Name", "Port", "To", "Allowed from", "State"],
        [
            [
                row.name,
                f"{row.protocol.upper()} {row.listen_port}",
                row.target,
                ", ".join(row.sources) or "anyone",
                row.state,
            ]
            for row in rows
        ],
    )


@cli.command("check", read_only=True)
@json_option("Print the answer as JSON.")
@pass_context
def check_command(ctx: Context) -> None:
    """Say whether nginx here can publish a TCP or UDP port, and what is missing."""
    support = StreamExposures().support()
    if ctx.json_output:
        click.echo(json.dumps(support.to_dict()))
        return
    if support.available:
        ctx.logger.success(f"nginx can publish ports (stream module: {support.module})")
        return
    ctx.logger.error(support.problem or "nginx cannot publish a port", details=support.hint or "")


@cli.command("remove")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@pass_context
def remove_command(ctx: Context, name: str, yes: bool) -> None:
    """
    Close a published port: the firewall rules made for it, its file, and nginx's use of it.

    Only what 'noust stream add' made is removed; a rule that was already in the
    firewall stays. Run it again after a failure and it finishes.
    """
    if not yes and not ctx.dry_run and not click.confirm(f"Close the published port {name}?"):
        return
    from noust.core.audit.actor import cli_actor

    result = StreamExposures().close(name, actor=cli_actor().label)
    verb = "Would close" if result.rehearsal else "Closed"
    ctx.logger.success(
        f"{verb} {result.exposure.protocol.upper()} {result.exposure.listen_port} ({name})"
    )


@cli.command("repair")
@pass_context
def repair_command(ctx: Context) -> None:
    """Finish the openings and closings that were interrupted."""
    done = StreamExposures().repair()
    if not done:
        ctx.logger.info("Nothing was interrupted.")
        return
    ctx.logger.success("Put right: " + ", ".join(done))
