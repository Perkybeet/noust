# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust site unknown-hosts``: what answers a name no site on this server serves.

A presentation layer over :mod:`noust.managers.unknown_hosts`, which ``/api/unknown-hosts``
calls too. Without it the web server answers such a request with the first site it
loaded, so any name pointed at this address shows an application that never agreed to be
shown there.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.managers.unknown_hosts import ACTIONS, UnknownHosts, UnknownHostsStatus, web_manager

_ANSWERS = {
    "close": "nothing: the connection is closed (Apache answers 421)",
    "refuse": "503",
    "redirect": "a permanent redirect",
}


def _print(ctx: Context, status: UnknownHostsStatus) -> None:
    """
    Show the status, for a person or as JSON.

    Args:
        ctx: The CLI context.
        status: What the manager reported.
    """
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Web server", f"{status.webserver} {status.version}".strip())
    if status.enabled:
        answer = _ANSWERS[status.action]
        if status.action == "redirect":
            answer += f" to {status.redirect_to}"
        logger.key_value("An unknown name gets", answer)
        logger.key_value("Over TLS", "the handshake is refused" if status.tls else "not covered")
        logger.key_value("File", status.file)
        if status.disabled_distribution_site:
            logger.key_value(
                "Switched off to make room",
                f"{status.disabled_distribution_site} (put back on reset)",
            )
    else:
        logger.key_value("An unknown name gets", "the first site the web server loaded")
    for holder in status.holders:
        logger.warning(
            f"{holder.file} is the default server for port {holder.port}"
            + (" (the web server's own default site)" if holder.distribution else "")
        )
    for note in status.notes:
        logger.info(note)


@click.group("unknown-hosts", cls=NoustGroup)
def unknown_hosts() -> None:
    """What answers a request for a name no site serves: nothing, a 503, or a redirect."""


@unknown_hosts.command("show", read_only=True)
@click.option("--webserver", "-w", type=click.Choice(["nginx", "apache"]), help="The web server.")
@json_option("Print the status as JSON.")
@pass_context
def show_command(ctx: Context, webserver: str | None) -> None:
    """Say what answers an unknown name now, and what else claims to be the default."""
    _print(ctx, UnknownHosts(web_manager(webserver)).status())


@unknown_hosts.command("set")
@click.argument("action", type=click.Choice(list(ACTIONS)))
@click.option("--redirect-to", metavar="URL", help="Where a redirect goes (http or https).")
@click.option(
    "--switch-off-default-site",
    is_flag=True,
    help="Switch off the web server's own default site if it is in the way (put back on reset).",
)
@click.option("--webserver", "-w", type=click.Choice(["nginx", "apache"]), help="The web server.")
@json_option("Print the status now in force as JSON.")
@pass_context
def set_command(
    ctx: Context,
    action: str,
    redirect_to: str | None,
    switch_off_default_site: bool,
    webserver: str | None,
) -> None:
    """
    Answer an unknown name with ACTION: close, refuse (503) or redirect.

    The site is written ahead of the others as the default for ports 80 and 443,
    tested by the web server and reloaded; one it refuses is put away again.
    """
    if action == "redirect" and not redirect_to:
        raise click.UsageError("A redirect needs --redirect-to")
    if ctx.dry_run:
        ctx.logger.info(f"Would answer an unknown name with {action}")
        return
    status = UnknownHosts(web_manager(webserver)).set(
        action,
        redirect_to or "",
        switch_off_distribution_site=switch_off_default_site,
    )
    if not ctx.json_output:
        ctx.logger.success(f"An unknown name now gets: {_ANSWERS[action]}")
    _print(ctx, status)


@unknown_hosts.command("reset")
@click.option("--webserver", "-w", type=click.Choice(["nginx", "apache"]), help="The web server.")
@json_option("Print the status now in force as JSON.")
@pass_context
def reset_command(ctx: Context, webserver: str | None) -> None:
    """Take the site away: the first site answers an unknown name again."""
    if ctx.dry_run:
        ctx.logger.info("Would take away the answer to unknown names")
        return
    status = UnknownHosts(web_manager(webserver)).reset()
    if not ctx.json_output:
        ctx.logger.success("An unknown name gets the first site again")
    _print(ctx, status)
