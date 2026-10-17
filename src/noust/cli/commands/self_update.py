# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust self-update``: Noust installing its own new releases by itself, inside limits.

The CLI of :class:`~noust.managers.auto_update.AutoUpdate` (checklist N-28); the console's
Settings > About and ``/api/system/update/auto`` are its other clients. Off until turned on; then a
timer looks every ten minutes while the window is open, and the run it starts is the same update
the console's "Update Noust" runs, behind the same preflight, in its own transient unit.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.utils import check_root
from noust.managers.auto_update import AutoUpdate

#: The sentences a skipped run is told in, by reason code.
REASONS = {
    "disabled": "The automatic update is off.",
    "outside_window": "The window is closed.",
    "done_today": "This window has already been answered.",
    "busy": "The console is working on a job; the next look tries again.",
    "check_off": "The update check is off (updates.check), so there is nothing to install from.",
    "checking": "Another update check is reading; the next look tries again.",
}


def _json(payload: Any) -> None:
    """
    Print a payload as JSON.

    Args:
        payload: JSON-serialisable data.
    """
    click.echo(json.dumps(payload, default=str))


def _require_root(ctx: Context, action: str) -> None:
    """
    Refuse what cannot work without root, except in a rehearsal.

    Args:
        ctx: The command's context.
        action: What was asked, for the message.

    Raises:
        NoustError: The process is not root and this is not a dry run.
    """
    if ctx.dry_run or check_root():
        return
    raise NoustError(f"'{action}' needs root", details="Run it with sudo.")


@click.group("self-update", cls=NoustGroup, invoke_without_command=True)
@click.pass_context
def cli(click_ctx: click.Context) -> None:
    """Let this server install Noust's own new releases: when, which, and what it did."""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(show)


@cli.command("show", read_only=True)
@json_option("Print the settings and the last run as JSON.")
@pass_context
def show(ctx: Context) -> None:
    """Show the settings of the automatic update, its timer and its last run."""
    status = AutoUpdate().status()
    if ctx.json_output:
        _json(status)
        return
    logger = ctx.logger
    settings = status["settings"]
    if not status["supported"]:
        logger.warning(f"{status['reason']}. {status['hint']}")
    if not settings["enabled"]:
        logger.info("This server does not install Noust's new releases by itself. Turn it on with:")
        logger.list_item("noust self-update set --enable")
    else:
        logger.info(
            f"Installing {settings['channel']} releases between {settings['window']} ({settings['days']})."
        )
        if status["next_window"]:
            logger.info(f"The window next opens {status['next_window']}.")
    if not status["check_enabled"]:
        logger.warning("The update check is off (updates.check), so nothing will be installed.")
    last = status["last_run"]
    if last:
        line = f"Last run {last['at']}: {last['outcome']} ({last['reason']}). {last['detail']}"
        (logger.warning if last["outcome"] == "failed" else logger.info)(line)


@cli.command("set")
@click.option(
    "--enable/--disable", "enabled", default=None, help="Switch the automatic update on or off."
)
@click.option(
    "--channel",
    type=click.Choice(["security", "patch", "stable"]),
    default=None,
    help="security: only a release that fixes a vulnerability. patch: also patch releases of the "
    "minor it runs. stable: also a new minor. A new major is never installed by itself.",
)
@click.option(
    "--window",
    default=None,
    metavar="HH:MM-HH:MM",
    help="The hours it may start in, on the server's clock.",
)
@click.option("--days", default=None, metavar="daily|mon,thu", help="The days the window opens on.")
@json_option("Print the settings as JSON.")
@pass_context
def set_command(
    ctx: Context, enabled: bool | None, channel: str | None, window: str | None, days: str | None
) -> None:
    """Change the settings of the automatic update; what is not given stays as it was."""
    _require_root(ctx, "self-update set")
    settings = AutoUpdate().set_settings(enabled=enabled, channel=channel, window=window, days=days)
    if ctx.json_output:
        _json(settings.to_dict())
        return
    if settings.enabled:
        ctx.logger.success(
            f"Noust installs its {settings.channel} releases between {settings.window}, {settings.to_dict()['days']}"
        )
    else:
        ctx.logger.success("Noust does not install its new releases by itself")


@cli.command("run")
@click.option(
    "--scheduled",
    is_flag=True,
    help="Run as the timer does: only inside the window, on a chosen day, once per window.",
)
@json_option("Print the run as JSON.")
@pass_context
def run(ctx: Context, scheduled: bool) -> None:
    """Decide and, if the settings say so, install a new release now (the timer's command is --scheduled)."""
    _require_root(ctx, "self-update run")
    result = AutoUpdate().run(
        trigger="schedule" if scheduled else "manual",
        force=not scheduled,
        on_line=(lambda line: None) if ctx.json_output else ctx.logger.info,
    )
    if ctx.json_output:
        _json(result.to_dict())
        return
    if result.outcome == "skipped":
        ctx.logger.info(
            REASONS.get(result.reason, result.detail) if result.reason in REASONS else result.detail
        )
    elif result.outcome == "updated":
        ctx.logger.success(
            f"Noust {result.to_version or result.target} is installed. {result.detail}"
        )
    else:
        ctx.logger.error(f"{result.detail}")
        if result.output:
            ctx.logger.info(result.output)
        click.get_current_context().exit(1)
