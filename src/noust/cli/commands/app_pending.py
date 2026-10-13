# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app pending``: what an application has been told that it does not run with yet.

A client of :mod:`noust.deployers.pending_changes`, like
``GET /api/apps/{d}/pending``. It changes nothing and shows no secret value:
only which settings differ, and what applying each takes.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.deployers import pending_changes
from noust.validators.domain import validate_domain

#: How each need is said, and the command that meets it.
_NEEDS = {
    pending_changes.NEEDS_REBUILD: "needs a rebuild",
    pending_changes.NEEDS_RESTART: "needs a restart",
    pending_changes.NEEDS_NEXT_DEPLOY: "applies at the next deploy",
}


@click.command("pending", cls=NoustCommand, read_only=True)
@click.argument("domain")
@json_option("Print the changes as JSON.")
@pass_context
def pending_command(ctx: Context, domain: str) -> None:
    """
    Show what the application is set to that it does not run with yet.

    Compared with the configuration it was last deployed or restarted with.
    The values of secret settings are never shown.
    """
    validated = validate_domain(domain)
    changes = pending_changes.pending(validated)
    if ctx.json_output:
        click.echo(json.dumps({**changes.to_dict(), "counts": pending_changes.summary(changes)}))
        return
    logger = ctx.logger
    if changes.baseline_created:
        logger.info(
            f"{validated} had no record of what it runs with; it has one now, as it is set. "
            "Changes from here on are listed."
        )
        return
    if not changes.pending:
        logger.success(f"{validated} runs with what it is set to")
        return
    logger.warning(f"{validated} is set to {len(changes.items)} thing(s) it does not run with yet")
    for item in changes.items:
        where = f"{item.area}: {item.key}" if item.key else item.area
        shown = f" ({item.before} -> {item.after})" if item.before or item.after else ""
        logger.info(f"  {item.change:<8} {where}{shown}: {_NEEDS[item.needs]}")
    logger.blank()
    if changes.needs == pending_changes.NEEDS_REBUILD:
        logger.info(f"Apply them with: noust update {validated}")
    elif changes.needs == pending_changes.NEEDS_RESTART:
        logger.info(f"Apply them with: noust restart {validated}")
    else:
        logger.info("Nothing to restart: the next deploy uses them.")
