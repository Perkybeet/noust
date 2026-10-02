# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app headless``: record a Compose worker as one (owner item 57).

A stack that publishes no port is a worker, judged by its containers. WASM
1.x registered every stack with the default port (3000) and wrote it a site,
so ``noust list``, ``noust health``, ``noust diagnose`` and the console called
a healthy worker down. This command clears the port and, when asked, removes
the site Noust wrote for it; it is never done on its own. The work is
:func:`~noust.deployers.docker_compose.make_headless`; this module only asks
and presents.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.deployers.docker_compose import make_headless, site_retirable
from noust.validators.domain import validate_domain


@click.command("headless", cls=NoustCommand)
@click.argument("domain")
@click.option(
    "--remove-site/--keep-site",
    default=None,
    help="Also remove the site Noust wrote for it (asked when not given).",
)
@click.option("--yes", "-y", is_flag=True, help="Remove the site without asking.")
@json_option("Print what was done as JSON.")
@pass_context
def headless_command(ctx: Context, domain: str, remove_site: bool | None, yes: bool) -> None:
    """
    Record a Docker Compose stack that publishes no port as a worker.

    Its port is cleared, so it is judged by its containers instead of by a
    port nothing listens on. When Noust wrote its site and it answers on no
    other name, the site can be removed too: asked, unless --remove-site,
    --keep-site or --yes says.
    """
    domain = validate_domain(domain)
    if remove_site is None:
        if yes:
            remove_site = True
        elif ctx.json_output or not site_retirable(domain):
            remove_site = False
        else:
            remove_site = click.confirm(
                f"Remove the nginx site Noust wrote for {domain}? A worker serves nothing",
                default=False,
            )
    change = make_headless(domain, remove_site=remove_site, logger=ctx.logger)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": change.domain,
                    "previous_port": change.previous_port,
                    "site": change.site,
                }
            )
        )
        return
    if change.previous_port is None:
        ctx.logger.success(f"{change.domain} already had no port; it is judged by its containers")
    else:
        ctx.logger.success(
            f"{change.domain} is a worker: port {change.previous_port} cleared, judged by its "
            "containers"
        )
    if change.site == "kept":
        ctx.logger.info(
            f"Its site is kept. Remove it with: noust app headless {domain} --remove-site"
        )
