# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app identity``: the system account an application runs as.

``status`` says which account it runs as, and the one it would get; ``migrate``
moves an application created before 3.2 from the shared service account to
its own, behind its health gate, putting everything back when it does not
answer. Both are :mod:`noust.managers.app_identity`, which
``/api/apps/{d}/identity`` calls too and which records the change in the audit
trail. This module only parses, presents and asks.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.managers import app_identity
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


@click.group("identity", cls=NoustGroup)
def identity() -> None:
    """The system account an application runs as: see it, or give it its own."""


@identity.command("status", read_only=True)
@click.argument("domain")
@json_option("Print the account as JSON.")
@pass_context
def status_command(ctx: Context, domain: str) -> None:
    """
    Show the account the application runs as, and whether it is its own.
    """
    info = app_identity.status(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps(info))
        return
    logger = ctx.logger
    logger.key_value("Application", info["domain"])
    logger.key_value(
        "Runs as",
        f"{info['account']}:{info['group']}"
        + (" (its own)" if info["own"] else " (the shared service account)"),
    )
    if info["reason"]:
        logger.key_value("Own account", f"not applicable: {info['reason']}")
    elif info["proposed"]:
        logger.blank()
        logger.info(
            f"Give it its own account, {info['proposed']}, with: "
            f"noust app identity migrate {info['domain']}"
        )


@identity.command("migrate")
@click.argument("domain")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print what was done as JSON.")
@pass_context
def migrate_command(ctx: Context, domain: str, yes: bool) -> None:
    """
    Run the application as its own system account instead of the shared one.

    Creates the account (no shell, no home), hands it the files the shared
    account owned (its tree, its .env, its build cache), rewrites its unit or
    its PHP-FPM pool, and restarts it behind its health gate. When it does
    not answer, the owners, the unit or the pool and the records are put back
    exactly as they were and it is restarted as before.
    """
    validated = validate_domain(domain)
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(
            f"Restart {validated} as its own account? It is restarted behind its health check",
            abort=True,
        )
    done = app_identity.migrate(validated, actor=_actor(), logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps(done.to_dict()))
