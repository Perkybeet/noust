# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust server``: managing the machine Noust runs on.

The command line and the console's Server page are two clients of the same
managers (:mod:`noust.managers.server`); this module formats and prints and holds
no logic of its own. Everything that changes the machine goes through the
process-wide command runner and filesystem, so ``--dry-run`` rehearses all of it.

Updates are run the way the console runs them, in their own systemd unit: the
``noust`` package is in the repository being updated, and its post-install
script restarts the console, which would kill a package manager running inside a
terminal that happens to close at the wrong moment as surely as one inside the
console. ``noust server updates run`` is the command that unit executes, and is
not meant to be typed.

**Attaching more subcommands.** The group is :data:`cli`. A module
``noust.cli.commands.server_security`` (owned by the security checks) is loaded
by name when it exists and joins the group: it defines either ``register(group)``,
called with :data:`cli`, or ``commands``, an iterable of Click commands that are
added to it. Nothing here imports it otherwise, so it needs no edit to this file.
"""

from __future__ import annotations

import getpass
import importlib
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.logger import Logger
from noust.core.utils import check_root, format_bytes
from noust.managers.server.context import ServerContext
from noust.managers.server.facts import FactCache
from noust.managers.server.pkg.base import UpdateScope
from noust.managers.server.power import resolve_delay
from noust.managers.server.processes import group_by_unit, list_processes
from noust.managers.server.storage import CLEANUP_ACTIONS
from noust.managers.server.summary import build_summary
from noust.managers.server.updates import RecordStore, UpdateRecord, new_update_id
from noust.managers.server.updates_unit import start_and_follow

_SIZE = re.compile(r"^(\d+)\s*([KMGT]?)i?B?$", re.IGNORECASE)
_MINUTES = re.compile(r"^(\d+)\s*(m|min|h)?$", re.IGNORECASE)
_CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


def build_context() -> ServerContext:
    """
    Build the managers this invocation uses.

    Returns:
        A context with no background work: a command waits for what it asks.
    """
    return ServerContext(cache=FactCache(background=False))


def _actor() -> str:
    """
    Name who is running the command, for the records it writes.

    Returns:
        ``cli:`` and the login the operator came in with (``sudo`` keeps it).
    """
    return f"cli:{os.environ.get('SUDO_USER') or getpass.getuser()}"


def _require_root(ctx: Context, action: str) -> None:
    """
    Refuse an action that cannot work without root, except in a rehearsal.

    Args:
        ctx: The command's context.
        action: What was asked, for the message.

    Raises:
        NoustError: The process is not root and this is not a dry run.
    """
    if ctx.dry_run or check_root():
        return
    raise NoustError(f"'{action}' needs root", details="Run it with sudo.")


def _confirm(ctx: Context, question: str, *, yes: bool) -> None:
    """
    Ask before doing something, or require ``--yes`` when nobody can answer.

    Args:
        ctx: The command's context.
        question: What is about to happen.
        yes: ``--yes`` was given.

    Raises:
        click.Abort: The answer was no.
        click.UsageError: There is no terminal to ask on and ``--yes`` was not given.
    """
    if yes or ctx.dry_run:
        return
    if ctx.json_output or not sys.stdin.isatty():
        raise click.UsageError(f"{question} Add --yes to confirm.")
    if not click.confirm(question, default=False):
        raise click.Abort()


def parse_size(text: str) -> int:
    """
    Read a size such as ``2G`` or ``512M`` into bytes.

    Args:
        text: A number with an optional K, M, G or T; without one, MiB.

    Returns:
        Bytes.

    Raises:
        click.BadParameter: It is not a size.
    """
    match = _SIZE.match(text.strip())
    if not match:
        raise click.BadParameter(f"{text!r} is not a size: use 2G or 512M")
    unit = match.group(2).upper() or "M"
    return int(match.group(1)) * {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}[unit]


def parse_delay(delay: str | None, at: str | None, now: bool) -> int:
    """
    Turn ``--in``, ``--at`` and ``--now`` into minutes from now.

    Args:
        delay: ``5``, ``5m`` or ``2h``.
        at: ``04:00`` (the next one, in local time) or an ISO date and time.
        now: ``--now``.

    Returns:
        Minutes; 0 means now, which only the command line allows.

    Raises:
        click.UsageError: More than one was given, or one is not readable.
        ValidationError: The moment is in the past or a week or more away.
    """
    given = [name for name, value in (("--in", delay), ("--at", at), ("--now", now)) if value]
    if len(given) > 1:
        raise click.UsageError(f"Give one of --in, --at and --now, not {' and '.join(given)}")
    if now:
        return 0
    if delay is not None:
        match = _MINUTES.match(delay.strip())
        if not match:
            raise click.UsageError(f"--in {delay!r} is not a delay: use 5, 5m or 2h")
        amount = int(match.group(1)) * (60 if (match.group(2) or "m").lower() == "h" else 1)
        return resolve_delay(in_minutes=amount)
    if at is not None:
        clock = _CLOCK.match(at.strip())
        moment: datetime
        if clock:
            current = datetime.now().astimezone()
            moment = current.replace(
                hour=int(clock.group(1)), minute=int(clock.group(2)), second=0, microsecond=0
            )
            if moment <= current:
                moment += timedelta(days=1)
        else:
            try:
                moment = datetime.fromisoformat(at)
            except ValueError:
                raise click.UsageError(
                    f"--at {at!r} is not a time: use 04:00 or 2026-09-30T04:00"
                ) from None
        return resolve_delay(at=moment)
    return 1


def _json(payload: Any) -> None:
    """
    Print a payload as JSON.

    Args:
        payload: JSON-serialisable data; anything else is printed as text.
    """
    click.echo(json.dumps(payload, default=str))


def _ago(seconds: float | int | None) -> str:
    """
    Say how old something is.

    Args:
        seconds: Its age, or None.

    Returns:
        ``3 min ago``, ``2 h ago``... or ``never``.
    """
    if seconds is None:
        return "never"
    if seconds < 90:
        return f"{int(seconds)} s ago"
    if seconds < 5400:
        return f"{int(seconds // 60)} min ago"
    if seconds < 172800:
        return f"{int(seconds // 3600)} h ago"
    return f"{int(seconds // 86400)} d ago"


@click.group("server", cls=NoustGroup)
def cli() -> None:
    """Manage this server: updates, reboots, disks, swap, the clock and the journal."""


# -- status ----------------------------------------------------------------------


@cli.command("status", read_only=True)
@json_option("Print the summary as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """
    Show how the server is, in one look.

    The same summary the console's overview reads: pending updates, whether a
    reboot is due, the disks, the clock, swap and whether systemd is well.
    """
    summary = build_summary(build_context(), wait=True)
    if ctx.json_output:
        _json(summary)
        return
    logger = ctx.logger
    logger.header(f"{summary['hostname']}: {summary['os']['name']}")
    logger.key_value("Kernel", str(summary["kernel"]))
    eol = summary["os"]["eol"]
    if eol["status"] in ("warn", "expired"):
        logger.check(
            "Support",
            f"{'ended' if eol['status'] == 'expired' else 'ends'} {eol['end_date']} "
            "(no security updates after that)",
            "error" if eol["status"] == "expired" else "warning",
        )

    updates = summary["updates"]
    if not updates["supported"]:
        logger.check("Updates", updates.get("reason") or "not managed here", "info")
    elif updates["error"]:
        logger.check("Updates", f"could not be checked: {updates['error']}", "warning")
    else:
        logger.check(
            "Updates",
            f"{updates['pending']} pending, {updates['security']} security "
            f"(lists {_ago(updates['lists_age_seconds'])})",
            "warning" if updates["security"] else "ok",
        )
    reboot = summary["reboot"]
    if reboot["required"]:
        logger.check(
            "Reboot", "required: " + "; ".join(reboot["reasons"] or reboot["packages"]), "warning"
        )
    elif reboot["required"] is False:
        logger.check("Reboot", "not needed", "ok")
    auto = summary["auto_updates"]
    if auto["supported"]:
        logger.check(
            "Automatic updates",
            f"{auto['mechanism']}: "
            + (
                ("security only" if auto["security_only"] else "all updates")
                if auto["enabled"]
                else "off"
            ),
            "ok" if auto["enabled"] else "info",
        )
    disk = summary["disk"]
    if disk["worst_mount"]:
        logger.check(
            "Disk",
            f"{disk['worst_mount']} {disk['worst_percent']}% used, {format_bytes(disk['free_bytes'])} free",
            {"ok": "ok", "warn": "warning", "critical": "error"}.get(str(disk["status"]), "info"),
        )
    clock = summary["time"]
    if clock["timezone"]:
        logger.check(
            "Time",
            f"{clock['timezone']}, {'synchronized' if clock['synchronized'] else 'not synchronized'}",
            "ok" if clock["synchronized"] else "warning",
        )
    swap = summary["swap"]
    if swap["total_bytes"] is not None:
        logger.check(
            "Swap",
            format_bytes(swap["total_bytes"]) if swap["total_bytes"] else "none",
            "warning" if swap["recommended"] else "info",
        )
    system = summary["system"]
    if system["state"]:
        failed = system["failed_units"] or []
        logger.check(
            "System",
            system["state"] + (f": {', '.join(failed)}" if failed else ""),
            "ok" if system["state"] == "running" else "warning",
        )
    scheduled = summary["power"]["scheduled"]
    if scheduled:
        logger.check(
            "Scheduled",
            f"{scheduled['action']} at {scheduled['scheduled_for']} by {scheduled['requested_by']}",
            "info",
        )


# -- updates -----------------------------------------------------------------------


@cli.group("updates", cls=NoustGroup)
def updates_group() -> None:
    """List, refresh and apply operating system updates."""


@updates_group.command("list", read_only=True)
@json_option("Print the pending updates as JSON.")
@pass_context
def updates_list(ctx: Context) -> None:
    """List the pending updates, security ones first."""
    manager = build_context().updates
    pending = manager.pending()
    probe = manager.restart_probe()
    if ctx.json_output:
        _json(
            {
                "pending": pending.pending,
                "security": pending.security,
                "packages": [vars(p) for p in pending.packages],
                "kept_back": pending.kept_back,
                "holds": pending.holds,
                "broken": pending.broken,
                "checked_at": pending.checked_at,
                "lists_age_seconds": pending.lists_age_seconds,
                "reboot_required": probe.reboot.required,
                "reboot_reasons": list(probe.reboot.reasons),
                "stale_services": list(probe.services),
                "notes": pending.notes,
            }
        )
        return
    logger = ctx.logger
    for note in pending.notes:
        logger.info(note)
    if not pending.packages:
        logger.success("Nothing is pending")
    else:
        ordered = sorted(pending.packages, key=lambda p: (not p.security, not p.kernel, p.name))
        logger.table(
            ["Package", "Installed", "New", "Kind"],
            [
                [
                    p.name,
                    p.installed or "-",
                    p.candidate,
                    ", ".join(
                        part
                        for part in (
                            "security" if p.security else "",
                            "kernel" if p.kernel else "",
                            p.advisory or "",
                        )
                        if part
                    )
                    or "-",
                ]
                for p in ordered
            ],
        )
        logger.blank()
        logger.info(f"{pending.pending} pending, {pending.security} security")
    if pending.kept_back:
        logger.warning(f"Kept back by the resolver: {', '.join(pending.kept_back)}")
    if pending.broken:
        logger.error("The package database is half configured: run 'noust server updates repair'")
    if probe.reboot.required:
        logger.warning(
            "A reboot is required: " + "; ".join(probe.reboot.reasons or probe.reboot.packages)
        )
    if probe.services:
        logger.info(f"Services running old libraries: {', '.join(probe.services)}")


@updates_group.command("refresh")
@global_flags
@pass_context
def updates_refresh(ctx: Context) -> None:
    """Download fresh package metadata, then count what is pending."""
    _require_root(ctx, "server updates refresh")
    manager = build_context().updates
    manager.refresh(ctx.logger.info)
    pending = manager.pending()
    ctx.logger.success(f"{pending.pending} pending, {pending.security} security")
    _noust_after_refresh(ctx)


def _noust_after_refresh(ctx: Context) -> None:
    """
    Say whether the refreshed index now offers a newer Noust, and how to install it.

    Only to a person at a terminal, with the update check on: the same rule as
    the banner every command prints, whose cached answer the refresh dropped.

    Args:
        ctx: The command's context.
    """
    from noust.core.update_checker import UpdateChecker, UpdateCheckInProgress

    if not (UpdateChecker.enabled() and UpdateChecker.should_announce(sys.argv[1:])):
        return
    try:
        check = UpdateChecker.check()
    except UpdateCheckInProgress:
        return
    if check.state == "update_available":
        ctx.logger.info(
            f"Noust {check.announced_version} can be installed now: {check.update_command}"
        )
    elif check.state == "index_behind":
        ctx.logger.info(
            f"Noust {check.announced_version} is published, but the refreshed package index "
            "does not list it yet: its package is still on the way."
        )


@updates_group.command("apply")
@click.option("--security-only", is_flag=True, help="Only the updates marked as security.")
@click.option(
    "--full", is_flag=True, help="A full upgrade (full-upgrade, dist-upgrade): may remove packages."
)
@click.option("--allow-removals", is_flag=True, help="Accept the removals a full upgrade lists.")
@click.option("--detach", is_flag=True, help="Start it and return; it keeps running on its own.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@pass_context
def updates_apply(
    ctx: Context, security_only: bool, full: bool, allow_removals: bool, detach: bool, yes: bool
) -> None:
    """
    Apply updates, in their own systemd unit, following them to the end.

    Ctrl+C stops following; the update itself keeps going. Nothing reboots the
    machine: the command says whether a reboot is due when it finishes.
    """
    _require_root(ctx, "server updates apply")
    server = build_context()
    scope = UpdateScope.SECURITY if security_only else UpdateScope.ALL
    manager = server.updates
    manager.preflight()
    plan = manager.plan(scope, full=full)
    logger = ctx.logger

    logger.info(f"{len(plan.packages)} package(s) would be updated: {' '.join(plan.argv[:2])} ...")
    if plan.impact:
        logger.info(f"It touches: {', '.join(plan.impact)}")
    if plan.restarts_console:
        logger.warning("Noust itself is among them: the console restarts, and its sessions survive")
    if plan.removals:
        logger.warning(
            f"It would REMOVE {len(plan.removals)} package(s): {', '.join(plan.removals)}"
        )
        if not allow_removals:
            from noust.managers.server.errors import ConfirmationRequiredError

            raise ConfirmationRequiredError(
                "That removal list needs your confirmation",
                "Read it, then repeat with --allow-removals.",
                required={"removals": list(plan.removals)},
            )
    if not plan.packages:
        logger.success("There is nothing to install")
        return
    _confirm(ctx, "Apply these updates?", yes=yes)

    update_id = new_update_id()
    if detach:
        if not server.unit.available():
            raise NoustError(
                "--detach needs systemd",
                details="Without a systemd to give it its own unit, the update runs in this terminal.",
            )
        unit = server.unit.start(
            update_id,
            scope=scope.value,
            full=full,
            allow_removals=allow_removals,
            actor=_actor(),
        )
        logger.success(f"Started in {unit}")
        logger.info(
            f"Follow it: journalctl -fu {unit}    Result: noust server updates history {update_id}"
        )
        return

    record = start_and_follow(
        manager,
        server.unit,
        update_id,
        logger.info,
        scope=scope.value,
        full=full,
        allow_removals=allow_removals,
        job_id=None,
        actor=_actor(),
    )
    if record.status == "dry-run":
        return
    _report_run(logger, record)
    if record.status == "failed":
        raise NoustError(record.error or "The update failed", details="Its output is above.")


@updates_group.command("restart-services")
@click.argument("units", nargs=-1)
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@pass_context
def updates_restart_services(ctx: Context, units: tuple[str, ...], yes: bool) -> None:
    """
    Restart the services an update left running on replaced libraries.

    UNITS are chosen from the update check's list; every one that may be
    restarted when none is given. D-Bus, logind, user sessions, the network
    and container runtimes are never restarted: a reboot restarts them.
    """
    from noust.managers.server.restarts import plan_service_restarts, restart_services
    from noust.managers.service_manager import ServiceManager

    _require_root(ctx, "server updates restart-services")
    server = build_context()
    probe = server.updates.restart_probe()
    plan = plan_service_restarts(probe.services, list(units) or None)
    logger = ctx.logger
    for unit, reason in plan.refused.items():
        logger.info(f"Left as it is: {unit} ({reason})")
    if not plan.restart:
        logger.success("There is nothing to restart")
        return
    logger.info(f"To restart: {', '.join(plan.restart)}")
    if plan.restarts_console:
        logger.warning("The console restarts last: open consoles reconnect by themselves")
    _confirm(ctx, "Restart these services?", yes=yes)
    outcome = restart_services(plan, ServiceManager(runner=server.runner), on_line=logger.info)
    if outcome.failed:
        raise NoustError(
            f"{len(outcome.failed)} service(s) did not restart",
            details="\n".join(f"{unit}: {words}" for unit, words in outcome.failed.items()),
        )
    logger.success(f"Restarted: {', '.join(outcome.restarted)}")


def _report_run(logger: Logger, record: UpdateRecord) -> None:
    """
    Say how an update ended.

    Args:
        logger: Where to write.
        record: The run's record.
    """
    logger.blank()
    if record.status == "completed":
        logger.success(f"Update {record.id} finished: {len(record.packages)} package(s)")
    else:
        logger.error(f"Update {record.id} {record.status}: {record.error or ''}")
    if record.reboot_required:
        logger.warning("A reboot is required: noust server reboot --in 5m")
    if record.stale_services:
        logger.info(f"Services running old libraries: {', '.join(record.stale_services)}")
    if record.conffiles_kept:
        logger.info(f"Configuration files kept as you had them: {', '.join(record.conffiles_kept)}")


@updates_group.command("repair")
@global_flags
@pass_context
def updates_repair(ctx: Context) -> None:
    """Finish an update that was interrupted (dpkg --configure -a)."""
    _require_root(ctx, "server updates repair")
    ran = build_context().updates.repair(ctx.logger.info)
    ctx.logger.success("Ran: " + "; ".join(ran))


@updates_group.command("history", read_only=True)
@click.argument("update_id", required=False)
@click.option("-n", "--limit", default=10, show_default=True, type=click.IntRange(1, 50))
@json_option("Print the runs as JSON.")
@pass_context
def updates_history(ctx: Context, update_id: str | None, limit: int) -> None:
    """Show the updates that were run, or one of them with its last output."""
    records = RecordStore()
    if update_id:
        record = records.read(update_id)
        if record is None:
            raise NoustError(f"No update run {update_id}", details="Use an id from 'history'.")
        if ctx.json_output:
            _json(record.to_dict())
            return
        _report_run(ctx.logger, record)
        for line in record.tail:
            click.echo(line)
        return
    runs = records.recent(limit)
    if ctx.json_output:
        _json({"items": [run.to_dict() for run in runs]})
        return
    if not runs:
        ctx.logger.info("No update has been run through Noust yet")
        return
    ctx.logger.table(
        ["Id", "Scope", "Status", "Started", "Packages", "By"],
        [
            [r.id, r.scope, r.status, r.started_at[:19], len(r.packages), r.actor or "-"]
            for r in runs
        ],
    )


# read_only: the request was audited where it was made (this terminal, or the API
# and its job); this is the executor inside the unit, and auditing it too
# would record every update twice.
@updates_group.command("run", hidden=True, read_only=True)
@click.option("--id", "update_id", required=True)
@click.option("--scope", type=click.Choice(["security", "all"]), required=True)
@click.option("--full", is_flag=True)
@click.option("--allow-removals", is_flag=True)
@click.option("--unit", default=None)
@click.option("--job-id", default=None)
@click.option("--actor", default=None)
def updates_run(
    update_id: str,
    scope: str,
    full: bool,
    allow_removals: bool,
    unit: str | None,
    job_id: str | None,
    actor: str | None,
) -> None:
    """
    Apply an update in this process. The command a transient unit runs.

    Everything this needs was imported when the module was, before the package
    manager replaces a single file of Noust's own tree: a lazy import after
    ``dpkg`` has unpacked a new version would find a different Noust than the
    one that started.
    """
    build_context().updates.apply(
        UpdateScope(scope),
        full=full,
        allow_removals=allow_removals,
        on_line=click.echo,
        update_id=update_id,
        job_id=job_id,
        unit=unit,
        actor=actor,
    )


@updates_group.group("auto", cls=NoustGroup)
def auto_group() -> None:
    """The distribution's own automatic updates."""


@auto_group.command("status", read_only=True)
@json_option("Print the state as JSON.")
@pass_context
def auto_status(ctx: Context) -> None:
    """Show whether automatic updates are on, and how."""
    status = build_context().updates.backend.auto_status()
    if ctx.json_output:
        _json(vars(status))
        return
    logger = ctx.logger
    logger.key_value("Mechanism", status.mechanism)
    logger.key_value("Installed", "yes" if status.installed else "no")
    logger.key_value(
        "Enabled",
        "yes" + (" (security only)" if status.security_only else " (all updates)")
        if status.enabled
        else "no",
    )
    logger.key_value("Reboots by itself", "yes" if status.reboots else "no")
    if status.detail:
        logger.info(status.detail)


@auto_group.command("enable")
@click.option("--all-updates", is_flag=True, help="Not only security updates.")
@global_flags
@pass_context
def auto_enable(ctx: Context, all_updates: bool) -> None:
    """Turn automatic updates on (installing their package if needed). Never reboots."""
    _require_root(ctx, "server updates auto enable")
    steps = build_context().updates.backend.set_auto(True, not all_updates, ctx.logger.info)
    for step in steps:
        ctx.logger.success(step)


@auto_group.command("disable")
@global_flags
@pass_context
def auto_disable(ctx: Context) -> None:
    """Turn automatic updates off."""
    _require_root(ctx, "server updates auto disable")
    for step in build_context().updates.backend.set_auto(False, True, ctx.logger.info):
        ctx.logger.success(step)


# -- power -------------------------------------------------------------------------


def _print_checks(logger: Logger, checks: list[Any]) -> None:
    """
    Show what a reboot would break.

    Args:
        logger: Where to write.
        checks: The power manager's checks.
    """
    for check in checks:
        logger.check(check.id, check.message, "ok" if check.status == "ok" else "warning")


def _schedule(
    ctx: Context,
    action: str,
    delay: str | None,
    at: str | None,
    now: bool,
    force: bool,
    yes: bool,
    message: str | None,
) -> None:
    """
    Schedule a reboot or a shutdown, showing the checks first.

    Args:
        ctx: The command's context.
        action: ``reboot`` or ``poweroff``.
        delay: ``--in``.
        at: ``--at``.
        now: ``--now``.
        force: Go ahead although a check warned.
        yes: Do not ask.
        message: The wall message.
    """
    _require_root(ctx, f"server {'reboot' if action == 'reboot' else 'shutdown'}")
    minutes = parse_delay(delay, at, now)
    power = build_context().power
    logger = ctx.logger
    _print_checks(logger, power.checks())
    what = (
        "Reboot"
        if action == "reboot"
        else "Shut down (it can only be started again from your provider's panel)"
    )
    _confirm(ctx, f"{what} {'now' if minutes == 0 else f'in {minutes} minute(s)'}?", yes=yes)
    scheduled = power.schedule(
        action, minutes=minutes, actor=_actor(), message=message, force=force
    )
    logger.success(f"{action} scheduled for {scheduled.scheduled_for}")
    logger.info("Cancel it with: noust server reboot --cancel")


@cli.command("reboot")
@click.option("--in", "delay", default=None, metavar="MINUTES", help="In 5, 5m or 2h.")
@click.option("--at", default=None, metavar="TIME", help="At 04:00 (next one) or 2026-09-30T04:00.")
@click.option("--now", is_flag=True, help="Right now, with no minute to change your mind.")
@click.option("--cancel", is_flag=True, help="Cancel the scheduled reboot or shutdown.")
@click.option(
    "--status", "show_status", is_flag=True, help="Show what is scheduled and the checks."
)
@click.option("--force", is_flag=True, help="Go ahead although a check warned.")
@click.option("--message", default=None, help="What to tell logged-in users.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@json_option("Print the status as JSON.")
@pass_context
def reboot_command(
    ctx: Context,
    delay: str | None,
    at: str | None,
    now: bool,
    cancel: bool,
    show_status: bool,
    force: bool,
    message: str | None,
    yes: bool,
) -> None:
    """
    Reboot the server, now or later, after looking at what it would break.

    Without --in, --at or --now it is in one minute. Nothing reboots the server
    because an update finished: this is always the operator's decision.
    """
    power = build_context().power
    if cancel:
        _require_root(ctx, "server reboot --cancel")
        ctx.logger.success("Cancelled" if power.cancel() else "Nothing was scheduled")
        return
    if show_status:
        state = power.status()
        checks = power.checks()
        if ctx.json_output:
            _json({**state.to_dict(), "checks": [vars(c) for c in checks]})
            return
        if state.scheduled:
            ctx.logger.info(
                f"{state.scheduled.action} scheduled for {state.scheduled.scheduled_for} "
                f"by {state.scheduled.requested_by}"
            )
        else:
            ctx.logger.info("Nothing is scheduled")
        _print_checks(ctx.logger, checks)
        return
    _schedule(ctx, "reboot", delay, at, now, force, yes, message)


@cli.command("shutdown")
@click.option("--in", "delay", default=None, metavar="MINUTES", help="In 5, 5m or 2h.")
@click.option("--at", default=None, metavar="TIME", help="At 04:00 (next one) or 2026-09-30T04:00.")
@click.option("--now", is_flag=True, help="Right now.")
@click.option("--force", is_flag=True, help="Go ahead although a check warned.")
@click.option("--message", default=None, help="What to tell logged-in users.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@pass_context
def shutdown_command(
    ctx: Context,
    delay: str | None,
    at: str | None,
    now: bool,
    force: bool,
    message: str | None,
    yes: bool,
) -> None:
    """
    Power the server off.

    A powered-off VPS is started again from your provider's panel, not from Noust.
    """
    _schedule(ctx, "poweroff", delay, at, now, force, yes, message)


# -- storage -----------------------------------------------------------------------


@cli.command("storage", read_only=True)
@click.option(
    "--analyze", is_flag=True, help="Also measure caches, releases, backups and logs (slow)."
)
@json_option("Print the storage report as JSON.")
@pass_context
def storage_command(ctx: Context, analyze: bool) -> None:
    """Show the real filesystems and what takes their space."""
    manager = build_context().storage
    analysis = (
        manager.analyze(ctx.logger.info if not ctx.json_output else None) if analyze else None
    )
    usage = manager.usage(analysis)
    if ctx.json_output:
        _json(
            {
                "mounts": [m.to_dict() for m in usage["mounts"]],
                "worst": usage["worst"].to_dict() if usage["worst"] else None,
                "candidates": [c.to_dict() for c in usage["candidates"]],
                "analysis_at": usage["analysis_at"],
                "errors": analysis.errors if analysis else [],
            }
        )
        return
    logger = ctx.logger
    logger.table(
        ["Mount", "Device", "Type", "Used", "Free", "Inodes"],
        [
            [
                m.mount_point,
                m.device,
                m.fstype,
                f"{m.percent_used}%",
                format_bytes(m.free_bytes),
                f"{m.inodes_percent}%" if m.inodes_total else "-",
            ]
            for m in usage["mounts"]
        ],
        justify=["left", "left", "left", "right", "right", "right"],
    )
    if usage["candidates"]:
        logger.blank()
        logger.table(
            ["What", "Size", "Could free", "Clean with"],
            [
                [
                    c.id,
                    format_bytes(c.size_bytes) if c.size_bytes is not None else "not measured",
                    format_bytes(c.reclaimable_bytes) if c.reclaimable_bytes is not None else "-",
                    f"noust server cleanup {c.action}" if c.action else "-",
                ]
                for c in usage["candidates"]
            ],
            justify=["left", "right", "right", "left"],
        )
    if not analyze:
        logger.blank()
        logger.info("Run with --analyze to measure caches, releases, backups and logs")


@cli.command("cleanup")
@click.argument("action", type=click.Choice(CLEANUP_ACTIONS))
@click.option(
    "--size-mb", type=click.IntRange(1), default=None, help="journal: vacuum to this size."
)
@click.option("--days", type=click.IntRange(1), default=None, help="journal: keep this many days.")
@click.option("--target", default=None, help="docker-image: the image id.")
@click.option("--plan", "plan_only", is_flag=True, help="Say what it would do and change nothing.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@json_option("Print the result as JSON.")
@pass_context
def cleanup_command(
    ctx: Context,
    action: str,
    size_mb: int | None,
    days: int | None,
    target: str | None,
    plan_only: bool,
    yes: bool,
) -> None:
    """
    Free disk space with one of a closed list of actions.

    \b
    journal                 rotate and vacuum the journal
    pkg-cache               empty the downloaded packages
    releases                prune every application to its own retention
    docker-build-cache      Docker build cache older than a week
    docker-dangling-images  Docker images with no name and no container
    docker-image            one unused Docker image, by --target id

    There is no action for Docker volumes, which hold your databases, and none
    that removes every unused image, which would take the way back of each
    Compose application.
    """
    params = {
        key: value
        for key, value in (("size_mb", size_mb), ("days", days), ("target", target))
        if value is not None
    }
    manager = build_context().storage
    plan = manager.plan_cleanup(action, **params)
    logger = ctx.logger
    if plan_only:
        if ctx.json_output:
            _json(vars(plan))
            return
        logger.info(plan.effect)
        for command in plan.commands:
            logger.list_item(command)
        for item in plan.items:
            logger.list_item(item)
        return
    _require_root(ctx, "server cleanup")
    if plan.needs_confirmation:
        logger.warning(plan.effect)
        _confirm(ctx, "Go ahead?", yes=yes)
    result = manager.cleanup(action, confirm=True, on_line=logger.info, **params)
    if ctx.json_output:
        _json(vars(result))
        return
    freed = f", {format_bytes(result.freed_bytes)} freed" if result.freed_bytes else ""
    logger.success(f"{action} done{freed}")


# -- swap --------------------------------------------------------------------------


@cli.group("swap", cls=NoustGroup, invoke_without_command=True)
@click.pass_context
def swap_group(click_ctx: click.Context) -> None:
    """Swap: what there is, making a swap file, removing it."""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(swap_status)


@swap_group.command("status", read_only=True)
@json_option("Print the state as JSON.")
@pass_context
def swap_status(ctx: Context) -> None:
    """Show the swap of this machine."""
    status = build_context().swap.status()
    if ctx.json_output:
        _json(status.to_dict())
        return
    logger = ctx.logger
    logger.key_value("RAM", format_bytes(status.memory_bytes))
    logger.key_value("Swap", format_bytes(status.total_bytes) if status.total_bytes else "none")
    for device in status.devices:
        logger.list_item(
            f"{device.name} ({device.kind}) {format_bytes(device.size_bytes)}, "
            f"{format_bytes(device.used_bytes)} used" + (" - made by Noust" if device.noust else "")
        )
    logger.key_value("Swappiness", str(status.swappiness))
    if status.recommended:
        logger.warning(
            f"There is no swap and this machine has little RAM: builds can be killed for memory. "
            f"noust server swap create --size {status.suggested_bytes // 1024**2}M"
        )
    if not status.supported:
        logger.info(status.reason)
    for warning in status.warnings:
        logger.warning(warning)


@swap_group.command("create")
@click.option("--size", "size", required=True, help="For example 2G or 2048M.")
@click.option("--swappiness", type=click.IntRange(0, 100), default=None, help="Default 10.")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@pass_context
def swap_create(ctx: Context, size: str, swappiness: int | None, yes: bool) -> None:
    """Make /swapfile, switch it on, and keep it across reboots."""
    _require_root(ctx, "server swap create")
    manager = build_context().swap
    size_bytes = parse_size(size)
    manager.check_size(size_bytes)
    _confirm(ctx, f"Make a {format_bytes(size_bytes)} swap file at /swapfile?", yes=yes)
    steps = manager.create(
        size_bytes, on_line=ctx.logger.info, swappiness=10 if swappiness is None else swappiness
    )
    for step in steps:
        ctx.logger.success(step)


@swap_group.command("remove")
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@global_flags
@pass_context
def swap_remove(ctx: Context, yes: bool) -> None:
    """Switch off and delete the swap file Noust made."""
    _require_root(ctx, "server swap remove")
    _confirm(ctx, "Remove /swapfile?", yes=yes)
    for step in build_context().swap.remove(ctx.logger.info):
        ctx.logger.success(step)


@swap_group.command("swappiness")
@click.argument("value", type=click.IntRange(0, 100))
@global_flags
@pass_context
def swap_swappiness(ctx: Context, value: int) -> None:
    """Set how eagerly the kernel swaps, now and at boot."""
    _require_root(ctx, "server swap swappiness")
    for step in build_context().swap.set_swappiness(value):
        ctx.logger.success(step)


# -- time and name ---------------------------------------------------------------------


@cli.group("time", cls=NoustGroup, invoke_without_command=True)
@click.pass_context
def time_group(click_ctx: click.Context) -> None:
    """The clock: time zone and synchronisation."""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(time_status)


@time_group.command("status", read_only=True)
@json_option("Print the clock as JSON.")
@pass_context
def time_status(ctx: Context) -> None:
    """Show the time, the zone and whether the clock is synchronised."""
    status = build_context().clock.status()
    if ctx.json_output:
        _json(status.to_dict())
        return
    logger = ctx.logger
    logger.key_value("Time zone", status.timezone)
    logger.key_value("Local time", status.local_time)
    logger.key_value("UTC", status.utc)
    logger.key_value("Synchronised", "yes" if status.synchronized else "no")
    logger.key_value(
        "Time daemon",
        "on" if status.ntp_enabled else ("off" if status.ntp_supported else "none installed"),
    )
    if status.offset_seconds is not None:
        logger.key_value("Offset", f"{status.offset_seconds:+.6f} s")


@time_group.command("timezone")
@click.argument("zone")
@global_flags
@pass_context
def time_timezone(ctx: Context, zone: str) -> None:
    """Change the time zone, and list the cron and backup timers that move."""
    _require_root(ctx, "server time timezone")
    change = build_context().clock.set_timezone(zone)
    ctx.logger.success(f"Time zone: {change.previous} -> {change.timezone}")
    if change.moved_timers:
        ctx.logger.warning(
            "These timers fire at local times and now fire at different moments: "
            + ", ".join(change.moved_timers)
        )


@time_group.command("ntp")
@click.argument("state", type=click.Choice(["on", "off"]))
@click.option("--install", is_flag=True, help="Install chrony when there is no time daemon.")
@global_flags
@pass_context
def time_ntp(ctx: Context, state: str, install: bool) -> None:
    """Turn time synchronisation on or off."""
    _require_root(ctx, "server time ntp")
    ctx.logger.success(
        build_context().clock.set_ntp(state == "on", install=install) or f"NTP {state}"
    )


@cli.command("hostname")
@click.argument("name", required=False)
@click.option(
    "--keep-against-cloud-init", is_flag=True, help="Tell cloud-init not to rename it back."
)
@global_flags
@json_option("Print the names as JSON.")
@pass_context
def hostname_command(ctx: Context, name: str | None, keep_against_cloud_init: bool) -> None:
    """Show the host name, or rename the machine to NAME."""
    identity = build_context().identity
    if name is None:
        info = identity.hostname()
        if ctx.json_output:
            _json(info.to_dict())
            return
        ctx.logger.key_value("Host name", info.hostname)
        ctx.logger.key_value("Static", info.static)
        if info.cloud_init and info.cloud_init_resets:
            ctx.logger.warning("cloud-init renames this machine on every boot unless told not to")
        return
    _require_root(ctx, "server hostname")
    for step in identity.set_hostname(name, keep_against_cloud_init=keep_against_cloud_init):
        (ctx.logger.warning if step.startswith("Warning") else ctx.logger.success)(step)


# -- logs and processes -------------------------------------------------------------


@cli.command("logs", read_only=True)
@click.argument("unit", required=False)
@click.option("-n", "--lines", default=100, show_default=True, type=click.IntRange(1, 1000))
@click.option("-p", "--priority", default=None, help="err, warning... or 0 to 7: it and worse.")
@click.option("--since", default=None, help="2026-09-29, '2026-09-29 10:30' or -30min, -2h, -7d.")
@click.option("--until", default=None)
@click.option("-k", "--kernel", is_flag=True, help="Only the kernel's messages.")
@click.option(
    "-b", "--boot", default=None, type=click.IntRange(max=0), help="0 this boot, -1 the previous."
)
@click.option(
    "-g", "--grep", "text", default=None, help="Only lines containing this, ignoring case."
)
@click.option("-f", "--follow", is_flag=True, help="Keep printing new entries as they are written.")
@json_option("Print the entries as JSON.")
@pass_context
def logs_command(
    ctx: Context,
    unit: str | None,
    lines: int,
    priority: str | None,
    since: str | None,
    until: str | None,
    kernel: bool,
    boot: int | None,
    text: str | None,
    follow: bool,
) -> None:
    """Read the journal of any unit, with filters."""
    if follow:
        if ctx.json_output:
            raise click.UsageError("--follow prints the journal's own lines: drop --json")
        build_context().journal.follow(
            click.echo,
            unit=unit,
            priority=priority,
            since=since,
            until=until,
            lines=lines,
            boot=boot,
            kernel=kernel,
        )
        return
    page = build_context().journal.read(
        text=text,
        unit=unit,
        priority=priority,
        since=since,
        until=until,
        lines=lines,
        boot=boot,
        kernel=kernel,
    )
    if ctx.json_output:
        _json(
            {
                "entries": [e.to_dict() for e in page.entries],
                "next_cursor": page.next_cursor,
                "truncated": page.truncated,
            }
        )
        return
    for entry in page.entries:
        click.echo(f"{entry.timestamp[:19]} {entry.unit or '-'}: {entry.message}")
    if page.truncated:
        click.echo("(there are earlier entries: raise --lines, or narrow with --since)", err=True)


@cli.command("processes", read_only=True)
@click.option(
    "--sort", "sort_by", type=click.Choice(["cpu", "memory", "pid", "name"]), default="cpu"
)
@click.option("--by-unit", is_flag=True, help="Add the processes up by the unit they belong to.")
@click.option("-n", "--limit", default=20, show_default=True, type=click.IntRange(1, 500))
@json_option("Print the processes as JSON.")
@pass_context
def processes_command(ctx: Context, sort_by: str, by_unit: bool, limit: int) -> None:
    """List the processes, or the units that use the machine. Observation only."""
    rows, total = list_processes(
        sort_by=sort_by, limit=100000 if by_unit else limit, show_commands=check_root()
    )
    if by_unit:
        units = group_by_unit(rows)[:limit]
        if ctx.json_output:
            _json({"units": [vars(u) for u in units], "total": total})
            return
        ctx.logger.table(
            ["Unit", "Processes", "CPU %", "Memory MB"],
            [[u.unit, u.processes, u.cpu_percent, u.memory_mb] for u in units],
            justify=["left", "right", "right", "right"],
        )
        return
    if ctx.json_output:
        _json({"processes": [r.to_dict() for r in rows], "total": total})
        return
    ctx.logger.table(
        ["PID", "Name", "User", "CPU %", "Mem %", "Unit"],
        [[r.pid, r.name, r.user, r.cpu_percent, r.memory_percent, r.unit or "-"] for r in rows],
        justify=["right", "left", "left", "right", "right", "left"],
    )


def _attach_security_commands() -> None:
    """
    Add the security checks' subcommands to the group, when their module exists.

    The module is found by name and imported plainly: an error inside it is a
    bug to see, not a reason to quietly have no such commands.
    """
    name = "noust.cli.commands.server_security"
    if importlib.util.find_spec(name) is None:
        return
    module = importlib.import_module(name)
    register = getattr(module, "register", None)
    if callable(register):
        register(cli)
        return
    for command in getattr(module, "commands", ()):
        cli.add_command(command)


_attach_security_commands()
