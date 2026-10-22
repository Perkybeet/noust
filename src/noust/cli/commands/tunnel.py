# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust tunnel``: Cloudflare Tunnel as the way into an application.

A presentation layer over :mod:`noust.managers.tunnels`, which ``/api/tunnels`` calls too.
A server with no public address, or none the operator controls, opens no port: the
connector dials out to Cloudflare, which carries the names routed to it back through that
connection. Noust creates the tunnel, runs the connector as a unit with its token in a
file only root reads, keeps the whole ingress, and makes the proxied CNAME of each name
(only a record it made is ever deleted).
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.managers.tunnels import RouteResult, TunnelManager
from noust.validators.domain import validate_domain


@click.group("tunnel", cls=NoustGroup)
def cli() -> None:
    """Reach an application through a Cloudflare Tunnel: no public address, no open port."""


@cli.group("route", cls=NoustGroup)
def route() -> None:
    """The public names a tunnel carries, and where each one goes."""


@cli.command("check", read_only=True)
@json_option("Print the answer as JSON.")
@pass_context
def check_command(ctx: Context) -> None:
    """Say whether a tunnel can run here, and what is missing."""
    support = TunnelManager().support()
    if ctx.json_output:
        click.echo(json.dumps(support.to_dict()))
        return
    if support.available:
        ctx.logger.success(f"A tunnel can run here (connector: {support.cloudflared})")
        ctx.logger.key_value("Accounts", ", ".join(support.accounts))
        return
    ctx.logger.error(support.problem or "A tunnel cannot run here", details=support.hint or "")


@cli.command("create")
@click.argument("name")
@click.option(
    "--account",
    metavar="DNS_ACCOUNT",
    help="The Cloudflare account of 'noust dns provider' to create it through.",
)
@click.option(
    "--cloudflare-account",
    metavar="ID",
    help="The Cloudflare account id, when the token reaches several.",
)
@pass_context
def create_command(
    ctx: Context, name: str, account: str | None, cloudflare_account: str | None
) -> None:
    """
    Create a tunnel at Cloudflare and run its connector here.

    The token goes to a file only root reads and the unit hands it to the connector;
    nothing is put in an argument, in /tmp or in the unit. Needs cloudflared, installed
    from Cloudflare's package repository (https://pkg.cloudflare.com/).
    """
    from noust.core.audit.actor import cli_actor

    result = TunnelManager().create(
        name, account=account, cloudflare_account=cloudflare_account, actor=cli_actor().label
    )
    verb = "Would create" if result.rehearsal else "Created"
    ctx.logger.success(f"{verb} the tunnel {name}")
    if not result.rehearsal:
        ctx.logger.info(
            f"Carry a name through it: noust tunnel route add {name} HOSTNAME --app APP"
        )


@cli.command("list", read_only=True)
@json_option("Print the tunnels as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the tunnels, whether their connectors run and what each one carries."""
    manager = TunnelManager()
    rows = manager.all()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "tunnels": [
                        {
                            **tunnel.to_dict(),
                            "active": manager.active(tunnel),
                            "routes": [r.to_dict() for r in manager.records.routes(tunnel.name)],
                        }
                        for tunnel in rows
                    ]
                }
            )
        )
        return
    if not rows:
        ctx.logger.info("There are no tunnels. Create one: noust tunnel create NAME")
        return
    table: list[list[str]] = []
    for tunnel in rows:
        routes = manager.records.routes(tunnel.name)
        table.append(
            [
                tunnel.name,
                tunnel.state if tunnel.state != "ready" else manager.active(tunnel),
                tunnel.provider,
                ", ".join(r.hostname for r in routes) or "nothing yet",
            ]
        )
    ctx.logger.table(["Tunnel", "Connector", "Account", "Carries"], table)


def _report(ctx: Context, result: RouteResult, verb: str) -> None:
    """
    Say what a route change did, and what is left for the operator to do.

    Args:
        ctx: The command's context.
        result: What the manager did.
        verb: ``added`` or ``removed``.
    """
    ctx.logger.success(
        f"{result.route.hostname} {verb}"
        + (f" (goes to {result.route.service})" if verb == "added" else "")
    )
    dns: dict[str, Any] = result.dns
    outcome = dns.get("outcome")
    if outcome in ("created", "adopted", "replaced", "exists"):
        ctx.logger.info(
            f"DNS: CNAME {result.route.hostname} to {dns.get('content')} ({outcome}, proxied)"
        )
    elif outcome == "conflict":
        ctx.logger.warning(f"A record is in the way: {dns.get('detail')}")
        ctx.logger.info(
            "To replace it: noust tunnel route add "
            f"{result.tunnel.name} {result.route.hostname} --replace-record "
            f"{dns.get('record_id')} --replace-current {dns.get('current')}"
        )
    elif outcome == "manual":
        ctx.logger.warning(f"DNS was not made: {dns.get('detail')}")
        ctx.logger.info(
            f"Make it in your DNS provider: CNAME {result.route.hostname} -> {dns.get('content')}, "
            "proxied (the orange cloud)"
        )
    elif outcome == "failed":
        ctx.logger.warning(f"DNS failed: {dns.get('detail')}")
    elif outcome in ("deleted", "already_gone", "not_owned", "still_used"):
        said = {
            "deleted": "its record was deleted",
            "already_gone": "its record was already gone",
            "not_owned": "its record was there before Noust and is left",
            "still_used": "another application still uses its record",
        }
        ctx.logger.info(f"DNS: {said[outcome]}")


@route.command("add")
@click.argument("tunnel")
@click.argument("hostname")
@click.option(
    "--app", "app_domain", metavar="APP", help="The application it is for: its site is reached."
)
@click.option(
    "--service",
    metavar="URL",
    help="Where the connector sends it (http://localhost:3000), when it is not an application's site.",
)
@click.option("--no-dns", is_flag=True, help="Do not make the name's CNAME.")
@click.option(
    "--replace-record",
    metavar="ID",
    help="Replace the record in the way: its id, as the conflict named it.",
)
@click.option("--replace-current", metavar="VALUE", help="What that record held when you looked.")
@json_option("Print the result as JSON.")
@pass_context
def route_add_command(
    ctx: Context,
    tunnel: str,
    hostname: str,
    app_domain: str | None,
    service: str | None,
    no_dns: bool,
    replace_record: str | None,
    replace_current: str | None,
) -> None:
    """
    Carry HOSTNAME through TUNNEL.

    For an application the connector reaches its site on this machine, so the site's own
    headers and policy apply: plain HTTP when the site serves none (a redirect to HTTPS
    would loop through Cloudflare), TLS when it does. The name's CNAME is made in the DNS
    account that holds its zone; one somebody else made is a conflict that names it.
    """
    from noust.core.audit.actor import cli_actor

    result = TunnelManager().add_route(
        tunnel,
        hostname,
        app_domain=validate_domain(app_domain) if app_domain else None,
        service=service,
        dns=not no_dns,
        replace_record=replace_record,
        replace_current=replace_current,
        actor=cli_actor().label,
    )
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    _report(ctx, result, "added")


@route.command("remove")
@click.argument("tunnel")
@click.argument("hostname")
@click.option("--keep-dns", is_flag=True, help="Do not touch the name's CNAME.")
@json_option("Print the result as JSON.")
@pass_context
def route_remove_command(ctx: Context, tunnel: str, hostname: str, keep_dns: bool) -> None:
    """Stop carrying HOSTNAME, and let go of its record (a record Noust did not make stays)."""
    from noust.core.audit.actor import cli_actor

    result = TunnelManager().remove_route(
        tunnel, hostname, keep_dns=keep_dns, actor=cli_actor().label
    )
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    _report(ctx, result, "removed")


@cli.command("remove")
@click.argument("name")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@pass_context
def remove_command(ctx: Context, name: str, yes: bool) -> None:
    """
    Stop a tunnel's connector, delete the tunnel at Cloudflare and let go of its records.

    Cloudflare may still count the connector as connected for a moment after it stops and
    refuse to delete the tunnel: run the command again and it finishes.
    """
    if not yes and not ctx.dry_run and not click.confirm(f"Delete the tunnel {name}?"):
        return
    from noust.core.audit.actor import cli_actor

    result = TunnelManager().delete(name, actor=cli_actor().label)
    verb = "Would delete" if result.rehearsal else "Deleted"
    ctx.logger.success(f"{verb} the tunnel {name}")
    for warning in result.warnings:
        ctx.logger.warning(warning)


@cli.command("repair")
@pass_context
def repair_command(ctx: Context) -> None:
    """Finish the creations and removals that were interrupted."""
    done = TunnelManager().repair()
    if not done:
        ctx.logger.info("Nothing was interrupted.")
        return
    ctx.logger.success("Put right: " + ", ".join(done))
