# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app networks``: the Docker networks a Compose stack is on, read and never changed.

Compose gives each stack a network of its own, and that isolation stops being true quietly when a
service is attached to a network another stack uses. This shows what Docker reports; the work is
:func:`noust.managers.stack_networks.stack_networks`, which ``GET /api/apps/{d}/networks`` calls too.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.store import get_store
from noust.core.teams.model import ResourceNotFoundError
from noust.managers.stack_networks import stack_networks
from noust.validators.domain import validate_domain


@click.command("networks", cls=NoustCommand, read_only=True)
@click.argument("domain")
@json_option("Print the networks as JSON.")
@pass_context
def networks_command(ctx: Context, domain: str) -> None:
    """Show the Docker networks a Compose stack is on and who else is on them."""
    validated = validate_domain(domain)
    app = get_store().get_app(validated)
    if app is None:
        raise ResourceNotFoundError(
            f"No application is deployed at {validated}", details="List them with: noust list"
        )
    answer = stack_networks(app)
    if ctx.json_output:
        click.echo(json.dumps(answer.to_dict()))
        return
    logger = ctx.logger
    if not answer.applicable:
        logger.info(answer.reason)
        return
    logger.key_value("Application", answer.app)
    logger.key_value("Containers", ", ".join(answer.containers) or "none")
    logger.key_value(
        "Isolated",
        "yes, every network is the stack's own and nothing else is on it"
        if answer.isolated
        else "no, see below",
    )
    if not answer.networks:
        logger.info("The stack is on no Docker network now (its containers are not running).")
        return
    logger.table(
        ["Network", "Driver", "Ranges", "Members"],
        [
            [
                network.name,
                network.driver + (", internal" if network.internal else ""),
                ", ".join(network.subnets) or "-",
                ", ".join(f"{m.name}{'' if m.own else ' (other)'}" for m in network.members)
                or "none",
            ]
            for network in answer.networks
        ],
    )
    for network in answer.networks:
        for finding in network.findings:
            logger.warning(f"{network.name}: {finding}")
