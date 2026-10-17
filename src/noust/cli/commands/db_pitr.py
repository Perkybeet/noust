# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db pitr``: recover PostgreSQL to an instant (checklist H-32).

The client of :class:`~noust.managers.database.pitr.PitrManager`, after
``/api/databases/pitr``: the same archiving, base backups and recovery, in the same words.
This module parses arguments, asks before anything is replaced, and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Iterator
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.managers.database.pitr import (
    DEFAULT_BASE_EVERY_HOURS,
    DEFAULT_KEEP_DAYS,
    PitrManager,
)
from noust.managers.database.pitr_archive import parse_recovery_time
from noust.managers.database.postgres import PostgresManager


def _pitr(logger: Logger) -> PitrManager:
    """
    Build the manager every command here uses.

    Args:
        logger: Where progress is reported.

    Returns:
        The manager over the server's PostgreSQL, resolved the way every ``noust db`` command
        resolves an engine.

    Raises:
        ValidationError: When the engine resolved is not PostgreSQL's manager.
    """
    from noust.cli.commands.db import _service

    manager = _service(logger).manager("postgresql")
    if not isinstance(manager, PostgresManager):
        raise ValidationError("Point-in-time recovery is for PostgreSQL", field="engine")
    return PitrManager(manager)


@contextlib.contextmanager
def _quiet(ctx: Context) -> Iterator[None]:
    """
    Keep the managers' progress lines out of a ``--json`` answer.

    Args:
        ctx: The invocation's context.

    Yields:
        Nothing; standard output is a discard while the block runs, in JSON mode.
    """
    if not ctx.json_output:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _fail(logger: Logger, exc: NoustError) -> None:
    """
    Report a refused or failed operation, with its fix, and leave with status 1.

    Args:
        logger: Where to report it.
        exc: The error.
    """
    logger.error(str(exc))
    click.get_current_context().exit(1)


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _confirm(question: str, *, yes: bool) -> bool:
    """
    Ask before doing something that restarts or replaces the engine.

    Args:
        question: The question, naming the exact consequence.
        yes: Skip the question because the operator already said so.

    Returns:
        True when the operation may proceed.
    """
    from noust.cli.commands.db import _confirm as ask

    return ask(question, force=yes)


def _limit(value: str, option: str) -> int | None:
    """
    Read a retention option: a whole number, or ``none`` for no limit.

    Args:
        value: What the operator typed.
        option: The option's name, for the error.

    Returns:
        The number, or None for ``none``.

    Raises:
        click.BadParameter: When it is neither.
    """
    if value.strip().lower() in ("none", "off", "no"):
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise click.BadParameter(f"{option} takes a whole number or 'none'") from exc


def _destination(value: str) -> dict[str, Any]:
    """
    Read ``--to NAME[:COUNT[:DAYS]]``.

    Args:
        value: What the operator typed.

    Returns:
        ``{"name", "retention_count", "retention_days"}``.

    Raises:
        click.BadParameter: When a retention part is not a number.
    """
    name, _, rest = value.partition(":")
    count, _, days = rest.partition(":")
    entry: dict[str, Any] = {"name": name, "retention_count": None, "retention_days": None}
    for key, text in (("retention_count", count), ("retention_days", days)):
        if text:
            try:
                entry[key] = int(text)
            except ValueError as exc:
                raise click.BadParameter("--to takes NAME[:COUNT[:DAYS]]") from exc
    return entry


def _print_window(view: dict[str, Any]) -> None:
    """
    Print the instants a recovery can reach.

    Args:
        view: The status.
    """
    window = view.get("window") or {}
    if window.get("earliest") and window.get("latest"):
        click.echo(f"  Recoverable:   from {window['earliest']} to {window['latest']} (UTC)")
    else:
        click.echo("  Recoverable:   nothing yet")


@click.group("pitr", cls=NoustGroup)
def pitr() -> None:
    """Recover PostgreSQL to an instant: the log archived, base backups, and the recovery."""


@pitr.command("status", read_only=True)
@json_option("Print the status as JSON.")
@pass_context
def status(ctx: Context) -> None:
    """Show whether the log is archived, the base backups, and the window a recovery can reach."""
    try:
        with _quiet(ctx):
            view = _pitr(ctx.logger).status()
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(view)
        return
    if not view["enabled"]:
        click.echo("Point-in-time recovery is off for PostgreSQL.")
        click.echo("Turn it on with: noust db pitr enable")
        return
    policy = view["policy"]
    click.echo("Point-in-time recovery of PostgreSQL: on")
    click.echo(f"  Archive:       {view['archive']['path']}")
    click.echo(
        f"                 {view['archive']['files']} files, {view['archive']['bytes']} bytes"
    )
    _print_window(view)
    bases = view.get("bases", [])
    click.echo(f"  Base backups:  {len(bases)}, one every {policy['base_every_hours']} hours")
    for base in bases[-3:]:
        click.echo(f"                 {base['id']}  finished {base['finished_at']}")
    keep = []
    if policy["retention_count"]:
        keep.append(f"last {policy['retention_count']} base backups")
    if policy["retention_days"]:
        keep.append(f"{policy['retention_days']} days back")
    click.echo(f"  Kept:          {', '.join(keep) if keep else 'everything'}")
    destinations = [entry["name"] for entry in policy["destinations"]]
    click.echo(f"  Destinations:  {', '.join(destinations) if destinations else 'none'}")
    timer = view.get("timer") or {}
    click.echo(f"  Next upkeep:   {timer.get('next_run') or 'unknown'}")
    archiver = view.get("archiver")
    if archiver:
        click.echo(
            f"  Archiver:      {archiver['archived']} segments archived, "
            f"{archiver['failed']} failures, last at {archiver['last_archived_at'] or 'never'}"
        )
    for warning in view["warnings"]:
        ctx.logger.warning(warning)


@pitr.command("enable")
@click.option(
    "--every",
    "base_every_hours",
    type=int,
    default=DEFAULT_BASE_EVERY_HOURS,
    show_default=True,
    help="Take a base backup when the last is this many hours old.",
)
@click.option(
    "--keep",
    "keep",
    default="none",
    show_default=True,
    help="Base backups to keep, or 'none' for no limit by number.",
)
@click.option(
    "--keep-days",
    "keep_days",
    default=str(DEFAULT_KEEP_DAYS),
    show_default=True,
    help="How many days back a recovery must be possible, or 'none'. With both limits, the "
    "one that keeps more wins.",
)
@click.option(
    "--to",
    "destinations",
    multiple=True,
    metavar="NAME[:COUNT[:DAYS]]",
    help="Send the log and the base backups to this backup destination. Repeatable.",
)
@click.option("--plan", is_flag=True, help="Say what it would do and change nothing.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting PostgreSQL.")
@json_option("Print the result as JSON.")
@pass_context
def enable(
    ctx: Context,
    base_every_hours: int,
    keep: str,
    keep_days: str,
    destinations: tuple[str, ...],
    plan: bool,
    yes: bool,
) -> None:
    """
    Start archiving the write-ahead log, prove it works and take the first base backup.

    PostgreSQL restarts once (archive_mode only changes with a restart): every connection is
    dropped for a few seconds. The settings are put back if the archive does not work.
    """
    try:
        retention_count = _limit(keep, "--keep")
        retention_days = _limit(keep_days, "--keep-days")
        entries = [_destination(value) for value in destinations]
        with _quiet(ctx):
            manager = _pitr(ctx.logger)
            planned = manager.plan_enable(
                base_every_hours=base_every_hours,
                retention_count=retention_count,
                retention_days=retention_days,
                destinations=entries,
            )
        if plan:
            _show_plan(ctx, planned)
            return
        if planned["restart"] and not ctx.json_output:
            click.echo(
                f"PostgreSQL will archive its log to {planned['archive_path']} and restart once."
            )
        if planned["restart"] and not _confirm("Restart PostgreSQL now?", yes=yes):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = manager.enable(
                confirmed=True,
                base_every_hours=base_every_hours,
                retention_count=retention_count,
                retention_days=retention_days,
                destinations=entries,
            )
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success("The log of PostgreSQL is archived and the first base backup is taken")
    _print_window(result)


def _show_plan(ctx: Context, planned: dict[str, Any]) -> None:
    """
    Print what turning archiving on would do.

    Args:
        ctx: The invocation's context.
        planned: :meth:`PitrManager.plan_enable`'s answer.
    """
    if ctx.json_output:
        _echo_json(planned)
        return
    click.echo(f"Archive:   {planned['archive_path']}")
    click.echo("PostgreSQL would be given:")
    for name, value in planned["settings"].items():
        click.echo(f"  {name} = {value}")
    click.echo(f"Restart:   {'yes, once' if planned['restart'] else 'no'}")
    click.echo(f"Base backup every {planned['base_every_hours']} hours.")


@pitr.command("disable")
@click.option(
    "--delete-archive",
    is_flag=True,
    help="Also delete the archived log, the base backups and what the destinations hold of them.",
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting PostgreSQL.")
@pass_context
def disable(ctx: Context, delete_archive: bool, yes: bool) -> None:
    """Stop archiving the log. The archive and the base backups stay unless --delete-archive."""
    what = " and delete the archive and every base backup" if delete_archive else ""
    if not _confirm(f"Stop archiving{what}? PostgreSQL restarts.", yes=yes):
        click.echo("Nothing was changed.")
        return
    try:
        result = _pitr(ctx.logger).disable(confirmed=True, delete_archive=delete_archive)
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if not result["disabled"]:
        click.echo(result.get("note", "Nothing was changed."))
        return
    ctx.logger.success("Archiving is off")
    if result["deleted"]:
        click.echo(f"Deleted {result['deleted']} files.")


@pitr.command("base")
@json_option("Print the base backup as JSON.")
@pass_context
def base(ctx: Context) -> None:
    """Take a base backup now, and apply retention."""
    try:
        with _quiet(ctx):
            taken = _pitr(ctx.logger).take_base()
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(taken.to_dict())
        return
    ctx.logger.success(f"Base backup {taken.id} ({taken.size} bytes)")


@pitr.command("sync")
@json_option("Print what was sent as JSON.")
@pass_context
def sync(ctx: Context) -> None:
    """Send the log and the base backups to the destinations, and prune them there."""
    try:
        with _quiet(ctx):
            report = _pitr(ctx.logger).sync()
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(report)
        return
    if not report:
        click.echo("The policy names no destination.")
    for name, sent in report.items():
        ctx.logger.info(
            f"{name}: {len(sent['uploaded'])} base backups and {len(sent['segments'])} "
            f"segments sent, {len(sent['deleted'])} deleted"
        )


@pitr.command("fetch")
@click.argument("destination")
@json_option("Print what was downloaded as JSON.")
@pass_context
def fetch(ctx: Context, destination: str) -> None:
    """
    Bring the base backups and the log back from DESTINATION, for a recovery.

    For a server whose archive is gone: what is here already is left alone, and every base
    backup is checked against the digest it was sent with.
    """
    try:
        with _quiet(ctx):
            result = _pitr(ctx.logger).fetch(destination)
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)


@pitr.command("run")
@json_option("Print what the run did as JSON.")
@pass_context
def run(ctx: Context) -> None:
    """
    Do the upkeep: a base backup when one is due, retention, and sending the archive.

    This is what the timer runs, every five minutes. It exits 1 when a step failed, so the
    timer's unit shows as failed.
    """
    try:
        with _quiet(ctx):
            result = _pitr(ctx.logger).run()
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)


@pitr.command("restore")
@click.option(
    "--time",
    "when",
    metavar="INSTANT",
    help="Recover to this instant, such as '2026-10-17 12:34:56' (UTC unless it carries an offset).",
)
@click.option("--latest", is_flag=True, help="Recover to the end of the archived log.")
@click.option("--base", "base_id", metavar="ID", help="Start from this base backup (see: status).")
@click.option("--plan", is_flag=True, help="Say what it would do and change nothing.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before replacing the cluster.")
@json_option("Print the plan or the result as JSON.")
@pass_context
def restore(
    ctx: Context, when: str | None, latest: bool, base_id: str | None, plan: bool, yes: bool
) -> None:
    """
    Recover the whole cluster to an instant.

    The data directory is renamed aside (never deleted), a base backup is put in its place and
    the log is replayed over it up to the instant. Every database goes back to that instant and
    every connection is dropped. When the recovery fails the previous data is put back.
    """
    try:
        if bool(when) == latest:
            raise ValidationError(
                "Give --time INSTANT or --latest, one of them",
                details="Example: noust db pitr restore --time '2026-10-17 12:34:56'",
            )
        target = parse_recovery_time(when) if when else None
        with _quiet(ctx):
            manager = _pitr(ctx.logger)
            planned = manager.plan_restore(target, base_id=base_id)
        if plan:
            if ctx.json_output:
                _echo_json(planned.to_dict())
            else:
                _show_restore_plan(planned.to_dict())
            return
        if not ctx.json_output:
            _show_restore_plan(planned.to_dict())
        if not _confirm(
            f"Replace the cluster in {planned.data_directory} with the state at "
            f"{planned.target or 'the end of the log'}?",
            yes=yes,
        ):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = manager.restore(target, confirmed=True, base_id=base_id)
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"Recovered in {result['seconds']} seconds")
    click.echo(f"The previous data directory is kept at {result['saved_as']}")


def _show_restore_plan(planned: dict[str, Any]) -> None:
    """
    Print what a recovery would do.

    Args:
        planned: :meth:`RestorePlan.to_dict`.
    """
    click.echo(
        f"Base backup:   {planned['base']['id']} (finished {planned['base']['finished_at']})"
    )
    click.echo(f"Recover to:    {planned['target'] or 'the end of the archived log'} (UTC)")
    click.echo(
        f"Log:           {planned['segments']} segments, newest archived {planned['last_archived_at']}"
    )
    click.echo(f"Data:          {planned['data_directory']} is renamed to {planned['saved_as']}")
    for warning in planned["warnings"]:
        click.echo(f"Note:          {warning}")


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(pitr)
