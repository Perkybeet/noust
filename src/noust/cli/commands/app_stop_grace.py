# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app stop-grace``: how long an application's unit has to stop (checklist B-21).

Shows the unit's ``TimeoutStopSec`` and ``KillSignal`` and sets them. systemd sends the
signal and waits that long before it kills what is left; an application that drains
connections or finishes a job needs longer than its 90 seconds, and one that dies on
``SIGTERM`` may want another signal. It is :func:`noust.deployers.lifecycle.set_stop_policy`,
which ``PUT /api/apps/{d}/rollout`` calls too: every unit of the application is rewritten
or none, nothing restarts, and a redeploy keeps it.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core import app_rollout_store
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.deployers.lifecycle import set_stop_policy
from noust.validators.domain import validate_domain


@click.command(
    "stop-grace",
    cls=NoustCommand,
    # Only the form that sets something changes anything.
    read_only=lambda params: (
        params["seconds"] is None and params["signal"] is None and not params["reset"]
    ),
)
@click.argument("domain")
@click.option(
    "--seconds",
    type=click.IntRange(app_rollout_store.MIN_STOP_TIMEOUT, app_rollout_store.MAX_STOP_TIMEOUT),
    default=None,
    help="How long the unit may take to stop before systemd kills it (1 to 3600).",
)
@click.option(
    "--signal",
    "signal",
    type=click.Choice(app_rollout_store.STOP_SIGNALS, case_sensitive=False),
    default=None,
    help="The signal that asks it to stop (default SIGTERM).",
)
@click.option("--reset", is_flag=True, help="Go back to systemd's own: 90 seconds and SIGTERM.")
@json_option("Print the stop policy now in force as JSON.")
@pass_context
def stop_grace_command(
    ctx: Context, domain: str, seconds: int | None, signal: str | None, reset: bool
) -> None:
    """
    Show or set how long an application's unit has to stop, and with which signal.

    Without options it shows the policy. The numbers are written into the unit now and
    systemd reads them the next time it stops it; nothing restarts. A Compose stack's
    containers have their own (stop_grace_period in the compose file).
    """
    validated = validate_domain(domain)
    if get_store().get_app(validated) is None:
        raise NoustError(
            f"Application not found: {validated}",
            details="Run 'noust list' to see what is deployed.",
        )
    if reset and (seconds is not None or signal is not None):
        raise click.UsageError("Give --reset, or --seconds and --signal, not both")
    if reset or seconds is not None or signal is not None:
        current = app_rollout_store.get(validated)
        if ctx.dry_run:
            ctx.logger.info(f"Would change the stop policy of {validated}")
            return
        change = set_stop_policy(
            validated,
            timeout=None if reset else (seconds if seconds is not None else current.stop_timeout),
            signal=None if reset else (signal if signal is not None else current.stop_signal),
            logger=ctx.logger,
        )
        if not ctx.json_output:
            ctx.logger.success(
                f"{validated} stops with systemd's own policy"
                if change.timeout is None and change.signal is None
                else f"{validated} is given {change.timeout or 90} seconds to stop after "
                f"{change.signal or 'SIGTERM'}"
            )
    settings = app_rollout_store.get(validated)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": validated,
                    "stop_timeout": settings.stop_timeout,
                    "stop_signal": settings.stop_signal,
                }
            )
        )
        return
    if seconds is None and signal is None and not reset:
        ctx.logger.key_value("Application", validated)
        ctx.logger.key_value(
            "Time to stop",
            f"{settings.stop_timeout} seconds"
            if settings.stop_timeout
            else "systemd's (90 seconds)",
        )
        ctx.logger.key_value("Signal", settings.stop_signal or "SIGTERM")
