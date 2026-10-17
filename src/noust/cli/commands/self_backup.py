# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust self-backup``: Noust's own state in one encrypted file.

The CLI of :class:`~noust.managers.self_backup.SelfBackupManager` and
:class:`~noust.managers.self_restore.SelfRestore`; ``/api/self-backup`` is the
other client of the first. The passphrase a bundle is sealed under is never an
argument: it is asked for without echo, read from a file only root can read
(``--passphrase-file``), from ``NOUST_SELF_BACKUP_PASSPHRASE``, or from one line
of standard input, so it reaches no shell history and no process list.

The timer a schedule installs runs ``noust self-backup run``: it reads the
stored passphrase, takes a bundle, proves it restorable, sends it to every
destination, prunes what retention no longer keeps and, on a failure, tells the
operator through the notification channels they configured.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.retention_options import KEEP_GB_HELP, add_size, gigabytes
from noust.core import paths
from noust.core.exceptions import NoustError, ValidationError
from noust.managers.database.base import format_size
from noust.managers.self_backup import SelfBackupManager
from noust.managers.self_restore import SelfRestore

#: The environment variable a script may give the passphrase in.
PASSPHRASE_ENV = "SELF_BACKUP_PASSPHRASE"  # noqa: S105 - the variable's name, not a secret


def _manager(ctx: Context) -> SelfBackupManager:
    """
    Args:
        ctx: The invocation's context.

    Returns:
        The manager, reporting through the command's logger.
    """
    return SelfBackupManager(logger=ctx.logger)


def _fail(ctx: Context, exc: NoustError) -> None:
    """
    Report a refused or failed operation, with its fix, and leave with status 1.

    Args:
        ctx: The invocation's context.
        exc: The error.
    """
    ctx.logger.error(str(exc))
    click.get_current_context().exit(1)


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def read_passphrase(*, confirm: bool, file: Path | None = None) -> str:
    """
    Read the passphrase of a bundle, from anywhere but a command line.

    In order: a file only its owner can read, ``NOUST_SELF_BACKUP_PASSPHRASE``,
    a prompt at a terminal (twice when choosing one), one line of standard input.

    Args:
        confirm: Ask twice, for a passphrase being chosen.
        file: A file holding it on one line.

    Returns:
        The passphrase, without the line ending.

    Raises:
        ValidationError: When the file is readable by others, is not a regular
            file, or nothing was given.
    """
    if file is not None:
        try:
            info = file.lstat()
        except OSError as exc:
            raise ValidationError(f"Cannot read {file}", details=str(exc)) from exc
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
            raise ValidationError(
                f"{file} must be a regular file only its owner can read",
                details=f"Run: chmod 600 {file}",
            )
        return file.read_text(encoding="utf-8").splitlines()[0] if info.st_size else ""
    from_env = paths.getenv(PASSPHRASE_ENV)
    if from_env:
        return from_env
    if sys.stdin.isatty():
        return str(click.prompt("Passphrase", hide_input=True, confirmation_prompt=confirm))
    line = sys.stdin.readline().rstrip("\r\n")
    if not line:
        raise ValidationError(
            "No passphrase was given",
            details="Type it at a terminal, pipe it in on one line, give --passphrase-file, "
            f"or set NOUST_{PASSPHRASE_ENV}.",
        )
    return line


PASSPHRASE_FILE = click.option(
    "--passphrase-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Read the passphrase from this file (mode 600), not from a prompt.",
)


@click.group("self-backup", cls=NoustGroup)
def cli() -> None:
    """Back up Noust's own state (store, configuration, secrets, keys, certificates, units)."""


@cli.command("create")
@PASSPHRASE_FILE
@json_option("Print the bundle as JSON.")
@pass_context
def create_command(ctx: Context, passphrase_file: Path | None) -> None:
    """
    Take a bundle of Noust's own state into one encrypted file.

    It holds what a replacement server needs to be this one: the store, the
    configuration, the secrets and the keys that open them, the certificates and
    the units Noust wrote. Nothing stores the passphrase in it: keep it, because
    without it the bundle cannot be opened by anyone, Noust included.
    """
    try:
        bundle = _manager(ctx).create(read_passphrase(confirm=True, file=passphrase_file))
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(bundle.to_dict())
        return
    data = bundle.to_dict()
    ctx.logger.success(f"Backed up Noust's own state: {bundle.path}")
    ctx.logger.info(f"  Size:     {format_size(bundle.size)}  (SHA-256 {bundle.sha256})")
    ctx.logger.info(
        f"  Holds:    {data['files']} files, {data['links']} links, {data['units']} units"
    )
    ctx.logger.warning(
        "Keep the passphrase: nothing stores it in the bundle, and without it no one can open "
        f"it. Prove it restorable any time with: noust self-backup verify {bundle.path.name}"
    )
    ctx.logger.info("A copy on this disk is as safe as this disk: send one away with 'push --to'.")


@cli.command("list", read_only=True)
@json_option("Print the bundles as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the bundles on this server, newest first."""
    entries = _manager(ctx).entries()
    if ctx.json_output:
        _echo_json([entry.to_dict() for entry in entries])
    elif not entries:
        ctx.logger.info("No bundle of Noust's own state on this server")
    else:
        for entry in entries:
            where = (
                f", on {', '.join(c['destination'] for c in entry.remote_copies)}"
                if entry.remote_copies
                else ""
            )
            click.echo(
                f"{entry.name}  {format_size(entry.size)}  {entry.created_at}  "
                f"({entry.kind}{where})"
            )


@cli.command("verify")
@click.argument("bundle")
@PASSPHRASE_FILE
@json_option("Print the verdict as JSON.")
@pass_context
def verify_command(ctx: Context, bundle: str, passphrase_file: Path | None) -> None:
    """
    Prove a bundle restorable without restoring it.

    Its envelope, every file against its digest, the store's own integrity
    check and that this Noust can open its schema. Nothing is changed.
    """
    try:
        report = _manager(ctx).verify(bundle, read_passphrase(confirm=False, file=passphrase_file))
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(report)
    elif report["ok"]:
        ctx.logger.success(
            f"{report['name']} is intact: {report['files']} files, taken {report['created_at']} "
            f"on {report['host']} by Noust {report['noust_version']}"
        )
        for warning in report["warnings"]:
            ctx.logger.warning(warning)
    else:
        for problem in report["problems"]:
            ctx.logger.error(problem)
    if not report["ok"]:
        click.get_current_context().exit(1)


@cli.command("restore")
@click.argument("bundle")
@click.option("--force", is_flag=True, help="Restore over a server that already holds state.")
@click.option(
    "--no-safety-copy",
    is_flag=True,
    help="Do not take a bundle of what is here first. Nothing is then left to undo it with.",
)
@click.option(
    "--no-restart",
    is_flag=True,
    help="Do not start the services and timers again afterwards.",
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@PASSPHRASE_FILE
@json_option("Print what the restore did as JSON.")
@pass_context
def restore_command(
    ctx: Context,
    bundle: str,
    force: bool,
    no_safety_copy: bool,
    no_restart: bool,
    yes: bool,
    passphrase_file: Path | None,
) -> None:
    """
    Put Noust's own state back from a bundle, on this server.

    The bundle is proven first and nothing changes if it does not verify. The
    console, the monitor and the timers are stopped, the files are put back in
    this server's own places, the store is brought to this version's schema, the
    units are enabled as they were and what was running is started again.
    Applications are not deployed: noust restore-server does that.
    """
    if not yes and not click.confirm(
        "Replace Noust's state on this server with what the bundle holds? Its services are "
        "stopped while the files are put back"
    ):
        ctx.logger.info("Cancelled")
        return
    try:
        manager = _manager(ctx)
        report = SelfRestore(manager, restart=not no_restart).restore(
            bundle,
            read_passphrase(confirm=False, file=passphrase_file),
            force=force,
            safety_copy=not no_safety_copy,
        )
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(report.to_dict())
        return
    ctx.logger.success(f"Restored Noust's state: {report.files} files, {report.links} links")
    if report.safety_copy:
        ctx.logger.info(f"  What was here is kept: {report.safety_copy}")
    if report.enabled:
        ctx.logger.info(f"  Enabled {len(report.enabled)} units")
    for item in report.skipped:
        ctx.logger.warning(f"  Left out {item['path']}: {item['reason']}")
    for note in report.notes:
        ctx.logger.info(f"  {note}")


@cli.command("push")
@click.argument("bundle")
@click.option("--to", "destination", required=True, help="A backup destination.")
@pass_context
def push_command(ctx: Context, bundle: str, destination: str) -> None:
    """Send a bundle to a destination. It is encrypted by a passphrase that is not sent."""
    try:
        summary = _manager(ctx).push(bundle, destination)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    ctx.logger.success(f"Sent {bundle} to {destination} (verified by {summary['verified_by']})")


@cli.command("remote", read_only=True)
@click.option("--from", "destination", required=True, help="A backup destination.")
@click.option("--host", help="List the bundles of another server (its folder name there).")
@json_option("Print the listing as JSON.")
@pass_context
def remote_command(ctx: Context, destination: str, host: str | None) -> None:
    """List the bundles a destination holds, for this server or another one."""
    manager = _manager(ctx)
    try:
        hosts = manager.remote_hosts(destination)
        entries = manager.remote_entries(destination, host) if (host or hosts) else []
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json({"hosts": hosts, "bundles": entries})
        return
    click.echo(f"Servers with bundles on {destination}: {', '.join(hosts) or 'none'}")
    for entry in entries:
        size = format_size(entry["size"]) if entry["size"] is not None else "?"
        click.echo(f"  {entry['name']}  {size}  {entry['modified']}")


@cli.command("fetch")
@click.argument("name")
@click.option("--from", "destination", required=True, help="A backup destination.")
@click.option("--host", help="The server's folder there; this server's by default.")
@pass_context
def fetch_command(ctx: Context, name: str, destination: str, host: str | None) -> None:
    """Bring a bundle from a destination to this server, checked against its digest."""
    manager = _manager(ctx)
    try:
        path = manager.download(destination, name, host or manager.host_slug(), manager.directory)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    os.chmod(path, 0o600)
    ctx.logger.success(f"Fetched {path}")
    ctx.logger.info(f"Restore it with: noust self-backup restore {path.name}")


@cli.command("delete")
@click.argument("bundle")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def delete_command(ctx: Context, bundle: str, force: bool) -> None:
    """Delete a bundle from this server. Copies on destinations stay."""
    if not force and not click.confirm(f"Delete the bundle {bundle}?"):
        ctx.logger.info("Cancelled")
        return
    try:
        _manager(ctx).delete(bundle)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    ctx.logger.success(f"Deleted {bundle}")


@cli.command("run")
@json_option("Print what the run did as JSON.")
@pass_context
def run_command(ctx: Context) -> None:
    """
    Run the schedule now: back up, prove the bundle, send, prune.

    This is what the schedule's timer runs. A failure is announced through the
    notification channels, and the exit status is 1, so the timer's unit shows
    as failed.
    """
    try:
        result = _manager(ctx).run_schedule(notify=True, on_step=lambda text: ctx.logger.info(text))
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"Backed up Noust's own state: {result['file']}")
    for name in result["destinations"]:
        ctx.logger.info(f"  Sent to {name}")
    for name in result["local_deleted"]:
        ctx.logger.info(f"  Retention deleted {name}")
    for name in result["removed_after_sending"]:
        ctx.logger.info(f"  Removed from this server, every destination holds it: {name}")


@cli.group("schedule", cls=NoustGroup)
def schedule() -> None:
    """Set, show or remove the schedule of Noust's own state backup."""


def _limit(value: str, option: str) -> int | None:
    """
    Read a retention option: a whole number, or ``none`` for no limit.

    Args:
        value: What was typed.
        option: The option's name, for the error.

    Returns:
        The number, or None.

    Raises:
        ValidationError: When it is neither.
    """
    if value.strip().lower() in ("none", "off", "0", ""):
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError(
            f"{option} must be a whole number or 'none'", details=f"Got {value!r}."
        ) from exc


def _destination(text: str) -> dict[str, Any]:
    """
    Read ``NAME[:COUNT[:DAYS[:GB]]]``.

    Args:
        text: What was typed.

    Returns:
        ``{"name", "retention_count", "retention_days", "retention_gb"}``.

    Raises:
        ValidationError: When the numbers are not numbers.
    """
    name, _, rest = text.partition(":")
    count, _, rest = rest.partition(":")
    days, _, size = rest.partition(":")
    return add_size(
        {
            "name": name,
            "retention_count": _limit(count, "the count of --to") if count else None,
            "retention_days": _limit(days, "the days of --to") if days else None,
        },
        size,
    )


@schedule.command("set")
@click.option(
    "--schedule",
    "calendar",
    default="daily",
    show_default=True,
    help="hourly, daily, weekly, monthly or a systemd OnCalendar expression.",
)
@click.option(
    "--keep", default="7", show_default=True, help="Scheduled bundles to keep here, or 'none'."
)
@click.option(
    "--keep-days", default="30", show_default=True, help="Days to keep one here, or 'none'."
)
@click.option("--keep-gb", default="none", show_default=True, help=KEEP_GB_HELP)
@click.option(
    "--to",
    "destinations",
    multiple=True,
    metavar="NAME[:COUNT[:DAYS[:GB]]]",
    help="Send each bundle to this destination, keeping COUNT, DAYS and GB there. Repeatable.",
)
@click.option(
    "--remote-only",
    is_flag=True,
    help="Keep nothing on this server: delete each bundle here once every destination holds it "
    "and its upload was checked. For a small disk; needs a --to.",
)
@click.option(
    "--keep-passphrase",
    is_flag=True,
    help="Keep the passphrase already stored instead of asking for one.",
)
@click.option("--disable", is_flag=True, help="Keep the settings but install no timer.")
@PASSPHRASE_FILE
@json_option("Print the schedule as JSON.")
@pass_context
def set_command(
    ctx: Context,
    calendar: str,
    keep: str,
    keep_days: str,
    keep_gb: str,
    destinations: tuple[str, ...],
    remote_only: bool,
    keep_passphrase: bool,
    disable: bool,
    passphrase_file: Path | None,
) -> None:
    """
    Create or replace the schedule, and its timer.

    The passphrase is kept in Noust's secret store so the timer can seal bundles
    unattended. Bundles sent to a destination are protected by what is not sent
    with them; the ones that stay on this disk are as safe as the disk.
    """
    manager = _manager(ctx)
    try:
        passphrase = (
            None
            if keep_passphrase
            else read_passphrase(confirm=passphrase_file is None, file=passphrase_file)
        )
        view = manager.set_schedule(
            schedule=calendar,
            retention_count=_limit(keep, "--keep"),
            retention_days=_limit(keep_days, "--keep-days"),
            retention_gb=gigabytes(keep_gb, "--keep-gb"),
            remote_only=remote_only,
            destinations=[_destination(text) for text in destinations],
            passphrase=passphrase,
            enabled=not disable,
        )
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(view)
        return
    ctx.logger.success("Backup of Noust's own state scheduled")
    _print_schedule(view)


def _print_schedule(data: dict[str, Any]) -> None:
    """
    Print the schedule the way ``schedule show`` does.

    Args:
        data: :meth:`~noust.managers.self_backup.SelfBackupManager.get_schedule`.
    """
    if not data["configured"]:
        click.echo("Noust's own state is not backed up on a schedule.")
        return
    keep = []
    if data["retention_count"]:
        keep.append(f"last {data['retention_count']}")
    if data["retention_days"]:
        keep.append(f"{data['retention_days']} days")
    if data.get("retention_gb"):
        keep.append(f"{data['retention_gb']:g} GB")
    click.echo(f"  Schedule:     {data['schedule']}{'' if data['enabled'] else '  (disabled)'}")
    click.echo(
        "  Keeps here:   nothing (each bundle goes once every destination holds it)"
        if data.get("remote_only")
        else f"  Keeps here:   {', '.join(keep) if keep else 'everything'}"
    )
    names = [entry["name"] for entry in data["destinations"]]
    click.echo(f"  Destinations: {', '.join(names) if names else 'none'}")
    click.echo(f"  Passphrase:   {'stored' if data['passphrase_stored'] else 'NOT stored'}")
    if data["enabled"]:
        click.echo(f"  Next run:     {data['timer'].get('next_run') or 'unknown'}")
    if data["last_status"]:
        click.echo(f"  Last run:     {data['last_status']} at {data['last_run_at']}")
        if data["last_error"]:
            click.echo(f"\n{data['last_error']}")


@schedule.command("show", read_only=True)
@json_option("Print the schedule as JSON.")
@pass_context
def show_command(ctx: Context) -> None:
    """Show the schedule of Noust's own state backup."""
    try:
        data = _manager(ctx).get_schedule()
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(data)
    else:
        _print_schedule(data)


@schedule.command("remove")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, force: bool) -> None:
    """Remove the schedule and its timer. The bundles and the stored passphrase stay."""
    if not force and not click.confirm(
        "Remove the schedule? Nothing will back Noust's own state up on a schedule"
    ):
        ctx.logger.info("Cancelled")
        return
    try:
        removed = _manager(ctx).remove_schedule()
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if removed:
        ctx.logger.success("Schedule removed")
    else:
        ctx.logger.info("There was no schedule")
