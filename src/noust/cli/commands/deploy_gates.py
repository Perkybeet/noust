# Copyright (c) 2024-2026 Yago López Prado
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

from noust.cli.app import Context, NoustGroup, json_option, pass_context
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


@cli.group("schedule", cls=NoustGroup)
def schedule_group() -> None:
    """Update an application by itself, on a schedule."""


@schedule_group.command("set")
@click.argument("domain")
@click.option(
    "--at",
    "calendar",
    required=True,
    metavar="WHEN",
    help="daily, weekly or monthly (at 03:00, Sunday for a week), or a systemd calendar "
    "expression such as 'Sun *-*-* 03:00' or '*-*-* 04:30'.",
)
@click.option(
    "--backup/--no-backup",
    "backup_first",
    default=True,
    show_default=True,
    help="Refuse the update when the backup before it does not happen.",
)
@click.option(
    "--only-if-new/--always",
    "only_if_new",
    default=True,
    show_default=True,
    help="Do nothing when the branch (or the tags it follows) has nothing new.",
)
@click.option("--off", is_flag=True, help="Keep the schedule and stop its timer.")
@json_option("Print the schedule as JSON.")
@pass_context
def schedule_set(
    ctx: Context,
    domain: str,
    calendar: str,
    backup_first: bool,
    only_if_new: bool,
    off: bool,
) -> None:
    """
    Make an application update itself at a time, with a backup first.

    Each run asks whether there is anything new and does nothing if not; takes the backup an
    update needs and refuses to update if it cannot; is held by a pause or a freeze of deploys;
    and passes the health gate every update passes, which puts back what served when the new
    version does not answer. The timer is noust-update-<app>.timer.
    """
    from noust.managers.update_scheduler import UpdateScheduler

    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would schedule the updates of {validated} at {calendar}")
        return
    saved = UpdateScheduler().set(
        validated,
        calendar,
        enabled=not off,
        backup_first=backup_first,
        only_if_new=only_if_new,
        by=cli_actor().label,
    )
    if ctx.json_output:
        click.echo(json.dumps(saved.to_dict()))
        return
    ctx.logger.success(
        f"{validated} {'will update itself' if saved.enabled else 'has a schedule, off'}: "
        f"{saved.calendar}"
    )
    ctx.logger.key_value("Backup first", "yes, and no update without it" if backup_first else "no")
    ctx.logger.key_value("Only if there is something new", "yes" if only_if_new else "no")


@schedule_group.command("show", read_only=True)
@click.argument("domain")
@json_option("Print the schedule as JSON.")
@pass_context
def schedule_show(ctx: Context, domain: str) -> None:
    """Show when an application updates itself, and how its last run ended."""
    from noust.core import update_schedule_store
    from noust.managers.update_scheduler import UpdateScheduler

    validated = _known(domain)
    saved = update_schedule_store.get(validated)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": validated,
                    "schedule": saved.to_dict() if saved else None,
                    "timer": UpdateScheduler().state(validated) if saved else None,
                }
            )
        )
        return
    if saved is None:
        ctx.logger.info(f"{validated} does not update itself")
        return
    timer = UpdateScheduler().state(validated)
    ctx.logger.key_value("Application", validated)
    ctx.logger.key_value("When", saved.calendar + ("" if saved.enabled else " (off)"))
    ctx.logger.key_value("Next run", timer.get("next_run") or "-")
    ctx.logger.key_value("Backup first", "yes" if saved.backup_first else "no")
    ctx.logger.key_value("Only if new", "yes" if saved.only_if_new else "no")
    if saved.last_run_at:
        ctx.logger.key_value(
            "Last run", f"{saved.last_run_at}: {saved.last_outcome}, {saved.last_detail or ''}"
        )


@schedule_group.command("list", read_only=True)
@json_option("Print the schedules as JSON.")
@pass_context
def schedule_list(ctx: Context) -> None:
    """List the applications that update themselves."""
    from noust.core import update_schedule_store

    schedules = update_schedule_store.list_all()
    if ctx.json_output:
        click.echo(json.dumps({"schedules": [item.to_dict() for item in schedules]}))
        return
    if not schedules:
        ctx.logger.info("No application updates itself")
        return
    ctx.logger.table(
        ["Application", "When", "On", "Last run", "Outcome"],
        [
            [
                item.domain,
                item.calendar,
                "yes" if item.enabled else "no",
                item.last_run_at or "-",
                item.last_outcome or "-",
            ]
            for item in schedules
        ],
    )


@schedule_group.command("remove")
@click.argument("domain")
@pass_context
def schedule_remove(ctx: Context, domain: str) -> None:
    """Stop an application updating itself: the timer and the schedule go."""
    from noust.managers.update_scheduler import UpdateScheduler

    validated = _known(domain)
    if ctx.dry_run:
        ctx.logger.info(f"Would remove the scheduled update of {validated}")
        return
    removed = UpdateScheduler().remove(validated)
    ctx.logger.success(
        f"{validated} no longer updates itself" if removed else f"{validated} had no schedule"
    )


@schedule_group.command("run")
@click.argument("domain")
@pass_context
def schedule_run(ctx: Context, domain: str) -> None:
    """
    Run an application's scheduled update now, once, exactly as its timer does.

    It records how it ended in the schedule. The exit status is 1 when the update failed or was
    refused (its backup did not happen), so the timer's unit shows it; nothing to do and held by
    a pause or a freeze are not failures.
    """
    from noust.managers.update_scheduler import run_scheduled

    validated = _known(domain)
    outcome = run_scheduled(validated, logger=ctx.logger)
    if outcome.outcome in ("failed", "backup_failed"):
        raise click.exceptions.Exit(1)
