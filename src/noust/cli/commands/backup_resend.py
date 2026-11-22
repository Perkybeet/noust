# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup resend``: send again the copies that were made and could not be sent (item 201).

The CLI of :class:`~noust.managers.backup_resend.Resender`, which a timer (``noust-backup-resend``) also
runs every few minutes, and the central's own scheduler where there are no timers: a copy that did not
reach its destination is kept as owed (:mod:`noust.managers.backup_sending`) and tried again after a
quarter of an hour, an hour, four hours and then every day. This is the pass that does it.

That a copy stays owed is the ordinary state of a destination that is down, so the command ends well
with copies still owed; it fails only when the pass itself could not be made.

``noust backup`` loads it through :func:`register`.
"""

from __future__ import annotations

import json
import sqlite3

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.managers.backup_resend import Resender


@click.command("resend", cls=NoustCommand)
@click.option(
    "--now",
    "everything",
    is_flag=True,
    help="Try every copy that is owed, not only the ones whose time has come.",
)
@json_option("Print the report as JSON.")
@pass_context
def resend(ctx: Context, everything: bool) -> None:
    """
    Send again the copies that could not reach their destination.

    A copy that was made and could not be sent stays on this server and is owed to the destination:
    it is tried again after a quarter of an hour, an hour, four hours and then every day, and by the
    next scheduled backup of the same thing before it takes its own. This does it now. A destination
    that does not answer ends the tries to it for this pass; the others go on. A copy that was
    deleted, or a destination the schedule no longer sends to, is forgotten. A copy that another pass
    (the timer's, or one started by hand) is sending right now is left to it.
    """
    try:
        report = Resender().run(force=everything)
    except (NoustError, OSError, sqlite3.Error) as exc:
        ctx.logger.error(f"The copies that are owed could not be sent: {exc}")
        click.get_current_context().exit(1)
        return
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return
    if report.sent:
        ctx.logger.success(f"{report.sent} copy(ies) reached their destination")
    if report.failed:
        ctx.logger.warning(f"{report.failed} copy(ies) could not be sent and stay owed")
    if report.skipped:
        ctx.logger.info(f"{report.skipped} more were not tried: their destination did not answer")
    if report.busy:
        ctx.logger.info(f"{report.busy} more are already being sent by another pass")
    if report.dropped:
        ctx.logger.info(f"{report.dropped} were forgotten: nothing owes them any more")
    if report.owed:
        ctx.logger.info(f"{report.owed} copy(ies) are still owed to a destination")
    elif not (report.sent or report.dropped or report.busy):
        ctx.logger.info("No copy is owed to any destination")


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(resend)
