# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app backup-before-update``: whether an update copies a stack's databases first.

A Docker Compose application's update takes a backup before it changes
anything, and that backup carries a dump of the databases the stack runs
(:mod:`noust.managers.stack_databases`): going back to the previous containers
does not undo what a migration did to the data. When the dump cannot be taken
the update stops, and this command is how to go on without it. The setting is
:func:`~noust.managers.stack_databases.set_backup_before_update`, which records
the change in the audit trail. This module only parses and presents.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.deployers.lifecycle import set_update_backup_required, update_backup_policy
from noust.managers.stack_databases import set_backup_before_update
from noust.validators.domain import validate_domain


# Showing the setting only reads; setting it is audited like every change.
@click.command(
    "backup-before-update",
    cls=NoustCommand,
    read_only=lambda params: params.get("state") is None,
)
@click.argument("domain")
@click.argument("state", required=False, type=click.Choice(["on", "off"], case_sensitive=False))
@json_option("Print the setting as JSON.")
@pass_context
def backup_before_update_command(ctx: Context, domain: str, state: str | None) -> None:
    """
    Show or set whether an update copies a stack's databases first.

    On by default for a Docker Compose application: its update dumps each
    database the stack runs (Postgres, MySQL, MariaDB, MongoDB) into the backup
    it takes first, and stops when a dump fails, so that a migration can always
    be undone. Turn it off when the database is backed up another way or cannot
    be dumped from its container.
    """
    domain = validate_domain(domain)
    previous: bool | None = None
    if state is not None:
        previous = set_backup_before_update(domain, state.lower() == "on")
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    enabled = bool(app.backup_before_update)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": app.domain,
                    "backup_before_update": enabled,
                    "previous": enabled if previous is None else previous,
                }
            )
        )
        return
    if state is not None:
        ctx.logger.success(
            f"An update of {app.domain} now "
            + ("copies its databases first" if enabled else "takes no copy of its databases")
        )
        return
    ctx.logger.key_value("Copy databases before an update", "on" if enabled else "off")


# Showing the setting only reads; setting it is audited like every change.
@click.command(
    "backup-required",
    cls=NoustCommand,
    read_only=lambda params: params.get("state") is None,
)
@click.argument("domain")
@click.argument(
    "state", required=False, type=click.Choice(["on", "off", "default"], case_sensitive=False)
)
@json_option("Print the setting as JSON.")
@pass_context
def backup_required_command(ctx: Context, domain: str, state: str | None) -> None:
    """
    Show or set whether an update is refused when its backup fails.

    With a database the default is on: the backup that comes before an update
    (the database dumps included) must succeed, or nothing is changed, because
    a migration that ran after a backup that did not happen cannot be undone.
    Without one a failed backup is a warning and the update goes on. ``default``
    goes back to that rule.
    """
    domain = validate_domain(domain)
    if state is None:
        policy = update_backup_policy(domain)
    else:
        policy = set_update_backup_required(
            domain, None if state.lower() == "default" else state.lower() == "on"
        )
    if ctx.json_output:
        click.echo(json.dumps(policy.to_dict()))
        return
    if state is not None:
        ctx.logger.success(
            f"An update of {policy.domain} "
            + (
                "is refused when its backup fails"
                if policy.required
                else "goes on without a backup"
            )
        )
        return
    reason = "chosen" if policy.explicit else "the default"
    ctx.logger.key_value(
        "Backup required before an update", f"{'on' if policy.required else 'off'} ({reason})"
    )
    if policy.databases:
        ctx.logger.key_value("Databases it protects", ", ".join(policy.databases))
