# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust deploy pause``, ``resume``, ``freeze``, ``unfreeze``, ``freezes`` and ``keep``:
what holds a deploy, and how much history an application keeps (checklist B-08, B-17, B-26).

- ``pause`` and ``resume`` hold and release what deploys an application *by itself*:
  its pushes, its tags and its schedules. Going back pauses it (``pause-on-rollback``),
  so the next push does not undo the rollback. An update you start yourself runs.
- ``freeze`` and ``unfreeze`` stop every deploy for a stretch of time or an incident,
  of one application or of the server. Rolling back is never held.
- ``keep`` says how many deployments of an application's history are kept.

Each is :mod:`noust.core.app_rollout_store`, :mod:`noust.core.deploy_freeze` or
:mod:`noust.core.deploy_guard`, which the API calls too, validates and audits. This
module only parses and presents.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, json_option, pass_context
from noust.cli.commands.deploy import cli
from noust.core import app_rollout_store, delivery, deploy_freeze, deploy_guard
from noust.core import webhook_deliveries as deliveries_log
from noust.core.audit.actor import cli_actor
from noust.core.exceptions import NoustError
from noust.core.store import get_store
from noust.validators.domain import validate_domain


def _known(domain: str) -> str:
    """
    Validate a domain and require an application behind it.

    Args:
        domain: What the operator typed.

    Returns:
        The validated domain.

    Raises:
        NoustError: Nothing is deployed there.
    """
    validated = validate_domain(domain)
    if get_store().get_app(validated) is None:
        raise NoustError(
            f"Application not found: {validated}",
            details="Run 'noust list' to see what is deployed.",
        )
    return validated


def _held(domain: str, since: str | None) -> int:
    """
    Args:
        domain: The application.
        since: When the pause began.

    Returns:
        How many deliveries it held back since.
    """
    app = get_store().get_app(domain)
    if since is None or app is None or app.id is None:
        return 0
    return deliveries_log.held_since(app.id, since)


def _freeze_line(freeze: deploy_freeze.Freeze) -> list[Any]:
    """
    Args:
        freeze: A freeze.

    Returns:
        A row of the table of freezes.
    """
    return [
        freeze.id,
        freeze.state(),
        freeze.domain or "the whole server",
        freeze.starts_at,
        freeze.ends_at or "until lifted",
        freeze.reason,
    ]


@cli.command("pause")
@click.argument("domain")
@click.option(
    "--reason", "-m", default=None, help="Why, for whoever meets it ('rolled back to 20261001')."
)
@json_option("Print the settings now in force as JSON.")
@pass_context
def pause_command(ctx: Context, domain: str, reason: str | None) -> None:
    """
    Hold what deploys an application by itself: its pushes, its tags and its schedules.

    An update you start yourself (noust update, the console's button) still runs.
    Going back already does this on its own for an application something deploys by
    itself, so that the next push does not undo the rollback. Release it with:
    noust deploy resume DOMAIN
    """
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would pause what deploys {validated} by itself")
        return
    settings = app_rollout_store.pause(validated, reason=reason, by=cli_actor().label)
    if ctx.json_output:
        click.echo(json.dumps({"domain": validated, **settings.to_dict()}))
        return
    ctx.logger.success(f"What deploys {validated} by itself is paused")
    ctx.logger.info(f"Release it with: noust deploy resume {validated}")


@cli.command("resume")
@click.argument("domain")
@json_option("Print what resuming did as JSON.")
@pass_context
def resume_command(ctx: Context, domain: str) -> None:
    """Let an application's pushes, tags and schedules deploy it again."""
    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would resume what deploys {validated} by itself")
        return
    settings = app_rollout_store.get(validated)
    held = _held(validated, settings.paused_at)
    was_paused = app_rollout_store.resume(validated, by=cli_actor().label)
    if ctx.json_output:
        click.echo(json.dumps({"domain": validated, "was_paused": was_paused, "held": held}))
        return
    if not was_paused:
        ctx.logger.info(f"What deploys {validated} by itself was not paused")
        return
    ctx.logger.success(f"What deploys {validated} by itself runs again")
    if held:
        ctx.logger.info(
            f"{held} push(es) or tag(s) arrived meanwhile and were not deployed. The next "
            f"push deploys the newest; to deploy it now: noust update {validated}"
        )


@cli.command("pause-on-rollback")
@click.argument("domain")
@click.argument("choice", required=False, type=click.Choice(["on", "off"]))
@json_option("Print the setting as JSON.")
@pass_context
def pause_on_rollback_command(ctx: Context, domain: str, choice: str | None) -> None:
    """
    Show or set whether going back pauses what deploys the application by itself.

    On by default: after a rollback the next push would otherwise deploy again the
    code that was rolled back from.
    """
    validated = _known(domain)
    if choice is not None:
        if ctx.dry_run:
            ctx.logger.info(f"Would turn pause-on-rollback {choice} for {validated}")
            return
        app_rollout_store.set_pause_on_rollback(validated, choice == "on", actor=cli_actor().label)
    enabled = app_rollout_store.get(validated).pause_on_rollback
    if ctx.json_output:
        click.echo(json.dumps({"domain": validated, "pause_on_rollback": enabled}))
        return
    ctx.logger.key_value("Application", validated)
    ctx.logger.key_value("Pause after a rollback", "on" if enabled else "off")


@cli.command("freeze")
@click.argument("domain", required=False)
@click.option("--reason", "-m", required=True, help="Why; whoever tries to deploy is told it.")
@click.option(
    "--from",
    "starts_at",
    metavar="WHEN",
    default=None,
    help="When it starts (2026-12-24T18:00); now when left out.",
)
@click.option(
    "--until",
    "ends_at",
    metavar="WHEN",
    default=None,
    help="When it ends; none lasts until lifted.",
)
@click.option(
    "--hours", type=float, default=None, help="Instead of --until: how long from its start."
)
@json_option("Print the freeze as JSON.")
@pass_context
def freeze_command(
    ctx: Context,
    domain: str | None,
    reason: str,
    starts_at: str | None,
    ends_at: str | None,
    hours: float | None,
) -> None:
    """
    Freeze deploys: nothing is deployed, of DOMAIN or of the whole server, for a while.

    Updates, pushes, tags and schedules are all held; someone at the terminal can still
    deploy with --during-freeze, and that is recorded. Rolling back is never held:
    going back is how an incident ends. Previews are not production and are not held.

    Lift it with: noust deploy unfreeze [DOMAIN]
    """
    validated = _known(domain) if domain else None
    if ctx.dry_run:
        ctx.logger.info(f"Would freeze deploys of {validated or 'the whole server'}")
        return
    freeze = deploy_freeze.create(
        reason,
        domain=validated,
        starts_at=starts_at,
        ends_at=ends_at,
        until_hours=hours,
        by=cli_actor().label,
    )
    if ctx.json_output:
        click.echo(json.dumps(freeze.to_dict()))
        return
    scope = validated or "the whole server"
    when = freeze.state()
    ctx.logger.success(
        f"Deploys of {scope} are frozen" if when == "active" else f"Deploys of {scope} will freeze"
    )
    ctx.logger.key_value("From", freeze.starts_at)
    ctx.logger.key_value("Until", freeze.ends_at or "until somebody lifts it")
    ctx.logger.info(f"Lift it with: noust deploy unfreeze{' ' + validated if validated else ''}")


@cli.command("unfreeze")
@click.argument("domain", required=False)
@click.option("--id", "identifier", type=int, default=None, help="Lift this freeze, by its number.")
@json_option("Print the freezes that were lifted as JSON.")
@pass_context
def unfreeze_command(ctx: Context, domain: str | None, identifier: int | None) -> None:
    """
    Lift the freeze of an application, or of the whole server without DOMAIN.

    Only the freezes of that scope are lifted: lifting an application's leaves the
    server's.
    """
    validated = _known(domain) if domain else None
    if ctx.dry_run:
        ctx.logger.info(f"Would lift the freeze of {validated or 'the whole server'}")
        return
    lifted = deploy_freeze.lift(validated, identifier=identifier, by=cli_actor().label)
    if ctx.json_output:
        click.echo(json.dumps({"lifted": [freeze.to_dict() for freeze in lifted]}))
        return
    if not lifted:
        ctx.logger.info(f"Deploys of {validated or 'the whole server'} were not frozen")
        return
    for freeze in lifted:
        ctx.logger.success(
            f"Lifted freeze {freeze.id} of {freeze.domain or 'the whole server'}: {freeze.reason}"
        )


@cli.command("freezes", read_only=True)
@click.option(
    "--all", "include_past", is_flag=True, help="Also the ones that ended or were lifted."
)
@json_option("Print the freezes as JSON.")
@pass_context
def freezes_command(ctx: Context, include_past: bool) -> None:
    """List the freezes that hold deploys now, or will."""
    freezes = deploy_freeze.list_freezes(include_past=include_past)
    if ctx.json_output:
        click.echo(json.dumps({"freezes": [freeze.to_dict() for freeze in freezes]}))
        return
    if not freezes:
        ctx.logger.info("Nothing is frozen")
        return
    ctx.logger.table(
        ["Id", "State", "Covers", "From", "Until", "Why"], [_freeze_line(f) for f in freezes]
    )


@cli.command("keep")
@click.argument("domain")
@click.argument("count", required=False, metavar="[N|default]")
@json_option("Print the setting as JSON.")
@pass_context
def keep_command(ctx: Context, domain: str, count: str | None) -> None:
    """
    Show or set how many deployments of an application's history are kept.

    N is from 1 to 1000; 'default' follows the server (deploy.history_keep, 20). Older
    deployments and their build logs are deleted at the application's next deployment.
    This is the history shown in the console and by 'noust app deploylog'; the builds
    kept on disk to roll back to are 'noust releases keep'.
    """
    validated = _known(domain)
    if count is not None:
        value: int | None
        if count.strip().lower() == "default":
            value = None
        else:
            try:
                value = int(count)
            except ValueError:
                raise click.UsageError(
                    f"{count!r} is not a number of deployments or 'default'"
                ) from None
        if ctx.dry_run:
            ctx.logger.info(f"Would keep {count} deployments of {validated}")
            return
        app_rollout_store.set_history_keep(validated, value, actor=cli_actor().label)
    own = app_rollout_store.get(validated).history_keep
    server = delivery.current().history_keep
    effective = own if own is not None else server
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": validated,
                    "history_keep": own,
                    "history_keep_server": server,
                    "history_keep_effective": effective,
                }
            )
        )
        return
    ctx.logger.key_value("Application", validated)
    ctx.logger.key_value(
        "Deployments kept",
        f"{effective} ({'its own' if own is not None else 'the server' + chr(39) + 's'})",
    )
    if own is not None:
        ctx.logger.info("Back to the server's number: noust deploy keep " + validated + " default")


@cli.command("held", read_only=True)
@click.argument("domain")
@json_option("Print the answer as JSON.")
@pass_context
def held_command(ctx: Context, domain: str) -> None:
    """Say what holds the deploys of an application, if anything: a pause or a freeze."""
    validated = _known(domain)
    automatic = deploy_guard.gate_for(validated, automatic=True)
    manual = deploy_guard.gate_for(validated, automatic=False)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": validated,
                    "automatic": automatic.message if automatic else None,
                    "manual": manual.message if manual else None,
                    "sources": deploy_guard.automatic_sources(validated),
                }
            )
        )
        return
    sources = deploy_guard.automatic_sources(validated)
    ctx.logger.key_value("Deploys by themselves", ", ".join(sources) if sources else "none")
    ctx.logger.key_value("By themselves", automatic.message if automatic else "run")
    ctx.logger.key_value("By hand", manual.message if manual else "run")
