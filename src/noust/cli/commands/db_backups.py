# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db backup-*``: a database's backup policy, and what it produced.

The other client of :class:`~noust.managers.database.backups.DatabaseBackups`,
after ``/api/databases/backup-policies`` and ``/api/databases/backups``: the
same policies, timers, checks, destinations and safety copies, in the same
words. This module parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls
:func:`register`, so the commands join its group without this module importing
that one at load time (which would make the order of the two imports matter).

The timer a policy installs runs ``noust db backup-run DATABASE --engine ENGINE``: it
reads everything else from the store, dumps, checks, sends to each destination,
applies retention and, on a failure, tells the operator through the
notification channels they configured.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Iterator
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.managers.database.backups import DatabaseBackups, DumpView, PolicyView


class _Engine(click.ParamType):
    """
    An engine name, checked by ``noust db``'s own engine type.

    Deferred to call time so this module never imports ``noust.cli.commands.db``
    while that module is still loading.
    """

    name = "engine"

    def convert(self, value: Any, param: click.Parameter | None, ctx: click.Context | None) -> str:
        """
        Args:
            value: What the operator typed.
            param: The parameter being converted.
            ctx: The Click context.

        Returns:
            The name, once the registry knows it.
        """
        from noust.cli.commands.db import ENGINE

        return str(ENGINE.convert(value, param, ctx))

    def shell_complete(
        self, ctx: click.Context, param: click.Parameter, incomplete: str
    ) -> list[Any]:
        """
        Args:
            ctx: The Click context.
            param: The parameter being completed.
            incomplete: What was typed so far.

        Returns:
            The completions ``noust db``'s own engine type offers.
        """
        from noust.cli.commands.db import ENGINE

        return ENGINE.shell_complete(ctx, param, incomplete)


ENGINE = _Engine()


def _backups(logger: Logger) -> DatabaseBackups:
    """
    Build the class every command here uses.

    Args:
        logger: Where progress is reported.

    Returns:
        The backups class over ``noust db``'s service, so an engine resolves
        the way every ``noust db`` command resolves it.
    """
    from noust.cli.commands.db import _service

    return DatabaseBackups(_service(logger))


@contextlib.contextmanager
def _quiet(ctx: Context) -> Iterator[None]:
    """
    Keep the managers' progress lines out of a ``--json`` answer.

    A dump, a scheduler and a destination each report what they do through a
    logger that writes to standard output, and a machine reading the answer
    wants the JSON alone. Errors are printed after the block, as they are
    everywhere in ``noust db``.

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


def _fail(logger: Logger, exc: NoustError) -> int:
    """
    Report a refused or failed operation, with its fix.

    Args:
        logger: Where to report it.
        exc: The error.

    Returns:
        The exit code, 1.
    """
    logger.error(str(exc))
    return 1


def _exit(code: int) -> None:
    """
    Leave the command with an exit status.

    Args:
        code: Process exit code.
    """
    click.get_current_context().exit(code)


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _confirm(question: str, *, force: bool) -> bool:
    """
    Ask before doing something that cannot be undone.

    Args:
        question: The question, naming the exact resource and consequence.
        force: Skip the question because the operator already said so.

    Returns:
        True when the operation may proceed.
    """
    from noust.cli.commands.db import _confirm as ask

    return ask(question, force=force)


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
        click.BadParameter: When a retention part is not a whole number.
    """
    name, _, rest = value.partition(":")
    count, _, days = rest.partition(":")
    entry: dict[str, Any] = {"name": name, "retention_count": None, "retention_days": None}
    for key, text in (("retention_count", count), ("retention_days", days)):
        if text:
            entry[key] = _limit(text, "--to")
    return entry


def _describe_destination(entry: dict[str, Any]) -> str:
    """
    Args:
        entry: A policy's destination.

    Returns:
        The destination as ``name (keeps last 7, 30 days)``.
    """
    limits = []
    if entry.get("retention_count"):
        limits.append(f"last {entry['retention_count']}")
    if entry.get("retention_days"):
        limits.append(f"{entry['retention_days']} days")
    return f"{entry['name']} (keeps {', '.join(limits)})" if limits else str(entry["name"])


def _print_policy(logger: Logger, view: PolicyView) -> None:
    """
    Print one policy the way ``backup-schedule show`` does.

    Args:
        logger: Where to print.
        view: The policy and its timer.
    """
    data = view.to_dict()
    click.echo(f"\n{view.engine}/{view.database}")
    if not view.policy:
        click.echo("  No backup policy: nothing backs this database up on a schedule.")
        return
    keep = []
    if data["retention_count"]:
        keep.append(f"last {data['retention_count']}")
    if data["retention_days"]:
        keep.append(f"{data['retention_days']} days")
    click.echo(f"  Schedule:     {data['schedule']}{'' if data['enabled'] else '  (disabled)'}")
    click.echo(f"  Keeps here:   {', '.join(keep) if keep else 'everything'}")
    destinations = [_describe_destination(entry) for entry in data["destinations"]]
    click.echo(f"  Destinations: {', '.join(destinations) if destinations else 'none'}")
    click.echo(f"  Restore test: {'each dump' if data['verify_restore'] else 'off'}")
    timer = data["timer"]
    if data["enabled"]:
        click.echo(f"  Next run:     {timer.get('next_run') or 'unknown'}")
        if not timer.get("installed", True):
            logger.warning("The timer is not installed: save the policy again to recreate it.")
    if data["last_status"]:
        click.echo(f"  Last run:     {data['last_status']} at {data['last_run_at']}")
        if data["last_error"]:
            click.echo(f"\n{data['last_error']}")


def _print_dump(view: DumpView) -> None:
    """
    Print what is known of one dump, the evidence verbatim.

    Args:
        view: The dump with its record.
    """
    data = view.to_dict()
    click.echo(f"\n{data['name']}")
    click.echo(f"  Size:     {data['size_human']}  ({data['format']}, {data['kind']})")
    click.echo(f"  Checked:  {data['verify_status']}  {data['verify_method'] or ''}".rstrip())
    if data["verify_detail"]:
        click.echo(f"\n{data['verify_detail']}\n")
    if data["restore_test_status"]:
        click.echo(f"  Restore test: {data['restore_test_status']}")
        if data["restore_test_detail"]:
            click.echo(f"\n{data['restore_test_detail']}\n")


# ------------------------------------------------------------ the commands


@click.group("backup-schedule", cls=NoustGroup)
def backup_schedule() -> None:
    """Set, show or remove a database's backup policy (schedule, retention, destinations)."""


@backup_schedule.command("set")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--schedule",
    default="daily",
    show_default=True,
    help="hourly, daily, weekly, monthly or a systemd OnCalendar expression.",
)
@click.option(
    "--keep",
    default="7",
    show_default=True,
    help="Scheduled dumps to keep here, or 'none'. Dumps taken by hand are never deleted.",
)
@click.option(
    "--keep-days",
    default="30",
    show_default=True,
    help="Days to keep a scheduled dump here, or 'none'.",
)
@click.option(
    "--to",
    "destinations",
    multiple=True,
    metavar="NAME[:COUNT[:DAYS]]",
    help="Send each dump to this backup destination, keeping COUNT and DAYS there. Repeatable.",
)
@click.option(
    "--format",
    "dump_format",
    type=click.Choice(["custom", "plain", "tar"]),
    help="PostgreSQL's dump format. custom (pg_dump -Fc) by default.",
)
@click.option(
    "--verify-restore",
    is_flag=True,
    help="Load each dump into a temporary database, and drop it, as proof it restores.",
)
@click.option("--disable", is_flag=True, help="Keep the settings but install no timer.")
@json_option("Print the policy as JSON.")
@pass_context
def set_schedule(
    ctx: Context,
    database: str,
    engine: str,
    schedule: str,
    keep: str,
    keep_days: str,
    destinations: tuple[str, ...],
    dump_format: str | None,
    verify_restore: bool,
    disable: bool,
) -> None:
    """
    Create or replace a database's backup policy, and its timer.

    Saving the policy again is how it is changed. The timer runs 'noust db
    backup-run', which dumps, checks the dump, sends it to each destination
    and deletes what retention no longer keeps.
    """
    try:
        with _quiet(ctx):
            view = _backups(ctx.logger).set_policy(
                engine,
                database,
                schedule=schedule,
                retention_count=_limit(keep, "--keep"),
                retention_days=_limit(keep_days, "--keep-days"),
                destinations=[_destination(text) for text in destinations],
                dump_format=dump_format,
                verify_restore=verify_restore,
                enabled=not disable,
            )
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if ctx.json_output:
        _echo_json(view.to_dict())
    else:
        ctx.logger.success(f"Backup policy saved for {view.engine}/{view.database}")
        _print_policy(ctx.logger, view)
    _exit(0)


@backup_schedule.command("show", read_only=True)
@click.argument("database", required=False)
@click.option("--engine", "-e", type=ENGINE, help="Engine the database is on.")
@json_option("Print the policies as JSON.")
@pass_context
def show_schedule(ctx: Context, database: str | None, engine: str | None) -> None:
    """Show one database's backup policy, or every policy."""
    backups = _backups(ctx.logger)
    try:
        with _quiet(ctx):
            if database:
                if not engine:
                    raise ValidationError("--engine is needed to name a database")
                views = [backups.get_policy(engine, database)]
            else:
                views = backups.list_policies(engine)
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if ctx.json_output:
        _echo_json([view.to_dict() for view in views])
    elif not views:
        ctx.logger.info("No database has a backup policy")
    else:
        for view in views:
            _print_policy(ctx.logger, view)
    _exit(0)


@backup_schedule.command("remove")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_schedule(ctx: Context, database: str, engine: str, force: bool) -> None:
    """Remove a database's backup policy and its timer. Its dumps stay."""
    if not _confirm(
        f"Remove the backup policy of {database}? No new dumps will be taken on a schedule",
        force=force,
    ):
        ctx.logger.info("Cancelled")
        _exit(0)
    try:
        removed = _backups(ctx.logger).remove_policy(engine, database)
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if removed:
        ctx.logger.success(f"Backup policy removed for {database}")
    else:
        ctx.logger.info(f"{database} had no backup policy")
    _exit(0)


@click.command("backup-run", cls=NoustCommand)
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@json_option("Print what the run did as JSON.")
@pass_context
def backup_run(ctx: Context, database: str, engine: str) -> None:
    """
    Run a database's backup policy now: dump, check, send, prune.

    This is what the policy's timer runs. A failure is announced through the
    notification channels, and the exit status is 1, so the timer's unit shows
    as failed.
    """
    try:
        with _quiet(ctx):
            result = _backups(ctx.logger).run_policy(
                engine, database, notify=True, on_step=lambda text: ctx.logger.info(text)
            )
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if ctx.json_output:
        _echo_json(result)
    else:
        dump = result["dump"]
        ctx.logger.success(f"Backup complete: {dump['name']} ({dump['size_human']})")
        for name, outcome in result["destinations"].items():
            ctx.logger.info(f"  Sent to {name}: verified by {outcome.get('verified_by')}")
        for name in result["local_deleted"]:
            ctx.logger.info(f"  Retention deleted {name}")
    _exit(0)


@click.command("backup-verify", cls=NoustCommand)
@click.argument("file")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the dump is of.")
@click.option(
    "--restore-test",
    is_flag=True,
    help="Also load it into a temporary database, dropped afterwards.",
)
@json_option("Print the dump and its evidence as JSON.")
@pass_context
def backup_verify(ctx: Context, file: str, engine: str, restore_test: bool) -> None:
    """
    Check a dump: size, digest and the engine's own reading of it.

    FILE is the dump's file name, as 'noust db backups' lists it. Exits 1 when
    the dump fails, and prints the tool's own words.
    """
    try:
        with _quiet(ctx):
            view = _backups(ctx.logger).verify(engine, file, restore=restore_test)
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    data = view.to_dict()
    if ctx.json_output:
        _echo_json(data)
    else:
        _print_dump(view)
    failed = data["verify_status"] == "failed" or data["restore_test_status"] == "failed"
    _exit(1 if failed else 0)


@click.command("backup-push", cls=NoustCommand)
@click.argument("file")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the dump is of.")
@click.option("--to", "destination", required=True, help="A backup destination.")
@pass_context
def backup_push(ctx: Context, file: str, engine: str, destination: str) -> None:
    """
    Send a dump to a backup destination, after checking it.

    A dump that fails its check is not sent. The destination keeps its own
    encryption.
    """
    try:
        summary = _backups(ctx.logger).push(engine, file, destination)
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    ctx.logger.success(f"{file} sent to {destination}, verified by {summary.get('verified_by')}")
    _exit(0)


@click.command("backup-delete", cls=NoustCommand)
@click.argument("file")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the dump is of.")
@click.option(
    "--from",
    "destination",
    help="Delete the copy on this destination instead of the local file.",
)
@click.option("--database", "-d", help="The database the dump is of; needed with --from.")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def backup_delete(
    ctx: Context,
    file: str,
    engine: str,
    destination: str | None,
    database: str | None,
    force: bool,
) -> None:
    """Delete a dump from this server, or its copy from a destination."""
    where = f"from {destination}" if destination else "from this server"
    if not _confirm(f"Delete {file} {where}? This cannot be undone", force=force):
        ctx.logger.info("Cancelled")
        _exit(0)
    backups = _backups(ctx.logger)
    try:
        if destination:
            if not database:
                raise ValidationError("--database is needed to delete a copy from a destination")
            backups.delete_remote(engine, database, file, destination)
        else:
            backups.delete_dump(engine, file)
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    ctx.logger.success(f"{file} deleted {where}")
    _exit(0)


@click.command("backup-remote", cls=NoustCommand, read_only=True)
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to list dumps of.")
@click.option("--from", "destination", required=True, help="A backup destination.")
@click.option("--database", "-d", help="List this database's dumps instead of the databases.")
@json_option("Print the listing as JSON.")
@pass_context
def backup_remote(ctx: Context, engine: str, destination: str, database: str | None) -> None:
    """List what a backup destination holds: the databases, or one database's dumps."""
    backups = _backups(ctx.logger)
    try:
        with _quiet(ctx):
            if database:
                dumps: list[Any] = backups.remote_dumps(engine, database, destination)
            else:
                dumps = backups.remote_databases(engine, destination)
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if ctx.json_output:
        _echo_json(dumps)
    elif not dumps:
        ctx.logger.info(
            f"{destination} holds no {engine} dumps" + (f" of {database}" if database else "")
        )
    elif database:
        for dump in dumps:
            mine = "this server" if dump["own"] else "another server"
            click.echo(f"  {dump['name']}  {dump['size_human'] or '?'}  ({mine})")
    else:
        for name in dumps:
            click.echo(f"  {name}")
    _exit(0)


@click.command("restore-remote", cls=NoustCommand)
@click.argument("database")
@click.argument("file")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--from", "destination", required=True, help="The backup destination it is on.")
@click.option("--as-new", "new_name", help="Restore into a new database of this name instead.")
@click.option(
    "--drop",
    is_flag=True,
    help="Drop the database before restoring it. A safety copy is taken first and put back "
    "if the restore fails.",
)
@click.option(
    "--no-safety-copy",
    is_flag=True,
    help="Do not dump the database first when nothing is dropped.",
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def restore_remote(
    ctx: Context,
    database: str,
    file: str,
    engine: str,
    destination: str,
    new_name: str | None,
    drop: bool,
    no_safety_copy: bool,
    force: bool,
) -> None:
    """
    Download a dump from a destination, verify it and restore it.

    DATABASE is the database the dump is of, which names its folder on the
    destination. Nothing is touched until the download matches its recorded
    digest and passes its check.
    """
    if new_name:
        question = f"Restore {file} from {destination} into a new database '{new_name}'?"
    elif drop:
        question = (
            f"Drop database '{database}' and restore it from {file} on {destination}? "
            "A safety copy is taken first and put back if the restore fails"
        )
    else:
        question = (
            f"Restore database '{database}' from {file} on {destination}? "
            "Existing rows may be overwritten"
            + ("; a safety copy is taken first" if not no_safety_copy else "")
        )
    if not _confirm(question, force=force):
        ctx.logger.info("Cancelled")
        _exit(0)
    try:
        outcome = _backups(ctx.logger).restore_remote(
            engine,
            database,
            destination,
            file,
            drop_existing=drop,
            safety_backup=not no_safety_copy,
            new_name=new_name,
        )
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    ctx.logger.success(f"Database {outcome.database} restored")
    if outcome.safety_copy:
        ctx.logger.info(f"  Safety copy: {outcome.safety_copy}")
    _exit(0)


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(backup_schedule)
    for command in (
        backup_run,
        backup_verify,
        backup_push,
        backup_delete,
        backup_remote,
        restore_remote,
    ):
        group.add_command(command)
