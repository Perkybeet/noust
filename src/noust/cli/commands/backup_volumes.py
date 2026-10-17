# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup volumes``: an application's data, backed up on its own.

The CLI of :class:`~noust.managers.volume_backups.VolumeBackupManager`, which
``/api/volume-backups`` is the other client of: the same targets, the same
consistency modes, the same retention and destinations, the same restore that
keeps a safety copy and passes the application's health gate. This module
parses arguments and prints.

``noust backup`` loads it through :func:`register`, so the commands join its
group without this module being imported before that one exists.

The timer a policy installs runs ``noust backup volumes run DOMAIN``: it reads
everything else from the store, backs up, checks, sends, prunes and, on a
failure, tells the operator through the notification channels they configured.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.retention_options import KEEP_GB_HELP, add_size, gigabytes
from noust.core.exceptions import NoustError, ValidationError
from noust.managers.database.base import format_size
from noust.managers.volume_backups import VolumeBackupManager, VolumeBackupMetadata

CONSISTENCY = click.Choice(["live", "pause", "stop"])


def _manager(ctx: Context) -> VolumeBackupManager:
    """
    Args:
        ctx: The invocation's context.

    Returns:
        The manager, reporting through the command's logger.
    """
    return VolumeBackupManager(logger=ctx.logger)


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


def _describe(entry: VolumeBackupMetadata) -> str:
    """
    Args:
        entry: A backup.

    Returns:
        One line: id, target, size and when.
    """
    verified = {True: "verified", False: "FAILED its check", None: "unverified"}[entry.verified_ok]
    kept = ", ".join(entry.tags) or "manual"
    where = (
        f", on {', '.join(c['destination'] for c in entry.remote_copies)}"
        if entry.remote_copies
        else ""
    )
    return (
        f"{entry.id}  {entry.target}  {format_size(entry.size_bytes)}  "
        f"{entry.created_at}  ({kept}; {verified}{where})"
    )


@click.group("volumes", cls=NoustGroup)
def volumes() -> None:
    """Back up an application's persistent data and Docker volumes on their own."""


@volumes.command("targets", read_only=True)
@click.argument("domain")
@click.option("--path", "paths", multiple=True, help="Also consider this data directory.")
@json_option("Print the targets as JSON.")
@pass_context
def targets_command(ctx: Context, domain: str, paths: tuple[str, ...]) -> None:
    """List the data an application declares: its persistent paths and Compose volumes."""
    try:
        found = _manager(ctx).targets(domain, extra_paths=paths)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json([target.to_dict() for target in found])
        return
    if not found:
        ctx.logger.info(
            f"{domain} declares no data. Deploy it with --persist PATH, or add a directory "
            "with --path."
        )
        return
    for target in found:
        note = "" if target.exists else "  (does not exist yet)"
        click.echo(f"{target.key}  {target.declared_by}{note}")


@volumes.command("create")
@click.argument("domain")
@click.option(
    "--target",
    "targets",
    multiple=True,
    help="Back up only this target (see 'targets'). Repeatable.",
)
@click.option("--path", "paths", multiple=True, help="Also back up this data directory.")
@click.option(
    "--consistency",
    type=CONSISTENCY,
    default="live",
    show_default=True,
    help="live copies while it runs (SQLite files are still taken whole), pause freezes the "
    "application for the copy, stop stops it.",
)
@click.option("-m", "--description", default="", help="A note to keep with the backups.")
@json_option("Print the backups as JSON.")
@pass_context
def create_command(
    ctx: Context,
    domain: str,
    targets: tuple[str, ...],
    paths: tuple[str, ...],
    consistency: str,
    description: str,
) -> None:
    """Back up an application's data now, one archive per target."""
    try:
        taken = _manager(ctx).create(
            domain,
            targets=targets,
            extra_paths=paths,
            consistency=consistency,
            description=description,
        )
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json([entry.to_dict() for entry in taken])
        return
    for entry in taken:
        ctx.logger.success(
            f"Backed up {entry.target}: {entry.id} ({format_size(entry.size_bytes)})"
        )
        for item in entry.skipped:
            ctx.logger.warning(f"  Left out {item['path']}: {item['reason']}")


@volumes.command("list", read_only=True)
@click.argument("domain", required=False)
@click.option("--target", help="Only this target.")
@json_option("Print the backups as JSON.")
@pass_context
def list_command(ctx: Context, domain: str | None, target: str | None) -> None:
    """List the data backups on this server, newest first."""
    try:
        found = _manager(ctx).list_backups(domain, target)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json([entry.to_dict() for entry in found])
    elif not found:
        ctx.logger.info("No data backups on this server")
    else:
        for entry in found:
            click.echo(_describe(entry))


@volumes.command("verify")
@click.argument("backup_id")
@click.option("--deep", is_flag=True, help="Also extract it as a restore would.")
@json_option("Print the verdict as JSON.")
@pass_context
def verify_command(ctx: Context, backup_id: str, deep: bool) -> None:
    """Prove a data backup intact: its digest, its signature and that it reads through."""
    try:
        verdict = _manager(ctx).verify(backup_id, deep=deep)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(verdict)
    elif verdict["valid"]:
        ctx.logger.success(f"{backup_id} is intact" + (" and extracts" if deep else ""))
        for warning in verdict["warnings"]:
            ctx.logger.warning(warning)
    else:
        for error in verdict["errors"]:
            ctx.logger.error(error)
    if not verdict["valid"]:
        click.get_current_context().exit(1)


@volumes.command("delete")
@click.argument("backup_id")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def delete_command(ctx: Context, backup_id: str, force: bool) -> None:
    """Delete a data backup from this server. Copies on destinations stay."""
    if not force and not click.confirm(f"Delete the data backup {backup_id}?"):
        ctx.logger.info("Cancelled")
        return
    try:
        _manager(ctx).delete(backup_id)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    ctx.logger.success(f"Deleted {backup_id}")


@volumes.command("push")
@click.argument("backup_id")
@click.option("--to", "destination", required=True, help="A backup destination.")
@pass_context
def push_command(ctx: Context, backup_id: str, destination: str) -> None:
    """Send a data backup to a destination, after checking it."""
    try:
        summary = _manager(ctx).push(backup_id, destination)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    ctx.logger.success(f"Sent {backup_id} to {destination} (verified by {summary['verified_by']})")


@volumes.command("remote", read_only=True)
@click.argument("domain")
@click.option("--from", "destination", required=True, help="A backup destination.")
@json_option("Print the listing as JSON.")
@pass_context
def remote_command(ctx: Context, domain: str, destination: str) -> None:
    """List what a destination holds of an application's data, by target."""
    try:
        listing = _manager(ctx).remote_backups(destination, domain)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(listing)
        return
    if not listing:
        ctx.logger.info(f"{destination} holds no data of {domain}")
    for slug, files in listing.items():
        click.echo(f"\n{slug}")
        for entry in files:
            size = format_size(entry["size"]) if entry["size"] is not None else "?"
            click.echo(f"  {entry['name']}  {size}  {entry['modified']}")


@volumes.command("restore")
@click.argument("backup_id", required=False)
@click.option("--from", "destination", help="Restore from a backup on this destination.")
@click.option("--domain", help="With --from: the application.")
@click.option("--slug", help="With --from: the target's folder there (see 'remote').")
@click.option("--file", "file_name", help="With --from: the archive's name there.")
@click.option(
    "--no-safety-copy",
    is_flag=True,
    help="Do not archive the current data first. Nothing is then left to go back to.",
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print what the restore did as JSON.")
@pass_context
def restore_command(
    ctx: Context,
    backup_id: str | None,
    destination: str | None,
    domain: str | None,
    slug: str | None,
    file_name: str | None,
    no_safety_copy: bool,
    force: bool,
) -> None:
    """
    Put an application's data back as a backup held it.

    The data is replaced, not merged: the application is stopped, a safety copy
    of what is there is taken, the backup is put in its place, and the
    application is started and must pass its health gate. When it does not,
    what was there is put back.
    """
    if destination:
        if not (domain and slug and file_name):
            ctx.logger.error("--from needs --domain, --slug and --file")
            click.get_current_context().exit(1)
        what = f"{file_name} from {destination}"
    elif backup_id:
        what = backup_id
    else:
        ctx.logger.error("Name a backup, or restore from a destination with --from")
        click.get_current_context().exit(1)
        return
    if not force and not click.confirm(
        f"Replace the current data with {what}? A safety copy is taken first"
        if not no_safety_copy
        else f"Replace the current data with {what}, with no safety copy?"
    ):
        ctx.logger.info("Cancelled")
        return
    manager = _manager(ctx)
    try:
        if destination:
            result = manager.restore_remote(
                destination,
                str(domain),
                str(slug),
                str(file_name),
                safety_backup=not no_safety_copy,
            )
        else:
            result = manager.restore(str(backup_id), safety_backup=not no_safety_copy)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"Restored {result['target']} from {result['id']}")
    if result["safety_backup"]:
        ctx.logger.info(f"  Safety copy: {result['safety_backup']}")
    if result["gate"]:
        ctx.logger.info(f"  Health gate: {result['gate']}")


@volumes.command("run")
@click.argument("domain")
@json_option("Print what the run did as JSON.")
@pass_context
def run_command(ctx: Context, domain: str) -> None:
    """
    Run an application's data backup policy now: back up, check, send, prune.

    This is what the policy's timer runs. A failure is announced through the
    notification channels, and the exit status is 1, so the timer's unit shows
    as failed.
    """
    try:
        result = _manager(ctx).run_policy(
            domain, notify=True, on_step=lambda text: ctx.logger.info(text)
        )
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"Backed up {len(result['backups'])} target(s) of {domain}")
    for name in result["destinations"]:
        ctx.logger.info(f"  Sent to {name}")
    for backup_id in result["local_deleted"]:
        ctx.logger.info(f"  Retention deleted {backup_id}")


@volumes.group("schedule", cls=NoustGroup)
def schedule() -> None:
    """Set, show or remove an application's data backup policy."""


@schedule.command("set")
@click.argument("domain")
@click.option(
    "--schedule",
    "calendar",
    default="daily",
    show_default=True,
    help="hourly, daily, weekly, monthly or a systemd OnCalendar expression.",
)
@click.option("--target", "targets", multiple=True, help="Back up only this target. Repeatable.")
@click.option(
    "--path", "paths", multiple=True, help="Also back up this data directory. Repeatable."
)
@click.option(
    "--keep",
    default="7",
    show_default=True,
    help="Scheduled backups to keep per target, or 'none'.",
)
@click.option("--keep-days", default="30", show_default=True, help="Days to keep one, or 'none'.")
@click.option("--keep-gb", default="none", show_default=True, help=KEEP_GB_HELP)
@click.option(
    "--to",
    "destinations",
    multiple=True,
    metavar="NAME[:COUNT[:DAYS[:GB]]]",
    help="Send each backup to this destination, keeping COUNT, DAYS and GB there. Repeatable.",
)
@click.option(
    "--consistency",
    type=CONSISTENCY,
    default="live",
    show_default=True,
    help="live, pause (freeze the application for the copy) or stop.",
)
@click.option("--disable", is_flag=True, help="Keep the settings but install no timer.")
@json_option("Print the policy as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    calendar: str,
    targets: tuple[str, ...],
    paths: tuple[str, ...],
    keep: str,
    keep_days: str,
    keep_gb: str,
    destinations: tuple[str, ...],
    consistency: str,
    disable: bool,
) -> None:
    """Create or replace an application's data backup policy, and its timer."""
    try:
        view = _manager(ctx).set_policy(
            domain,
            schedule=calendar,
            targets=targets,
            extra_paths=paths,
            retention_count=_limit(keep, "--keep"),
            retention_days=_limit(keep_days, "--keep-days"),
            retention_gb=gigabytes(keep_gb, "--keep-gb"),
            destinations=[_destination(text) for text in destinations],
            consistency=consistency,
            enabled=not disable,
        )
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json(view.to_dict())
    else:
        ctx.logger.success(f"Data backup policy saved for {domain}")
        _print_policy(ctx, view.to_dict())


def _print_policy(ctx: Context, data: dict[str, Any]) -> None:
    """
    Print one policy the way ``schedule show`` does.

    Args:
        ctx: The invocation's context.
        data: :meth:`~noust.managers.volume_backups.VolumePolicyView.to_dict`.
    """
    click.echo(f"\n{data['domain']}")
    if not data["configured"]:
        click.echo("  No policy: its data is not backed up on a schedule.")
        return
    click.echo(f"  Schedule:     {data['schedule']}{'' if data['enabled'] else '  (disabled)'}")
    click.echo(f"  Targets:      {', '.join(data['targets']) or 'every one it declares'}")
    if data["extra_paths"]:
        click.echo(f"  Added paths:  {', '.join(data['extra_paths'])}")
    click.echo(f"  Consistency:  {data['consistency']}")
    keep = []
    if data["retention_count"]:
        keep.append(f"last {data['retention_count']}")
    if data["retention_days"]:
        keep.append(f"{data['retention_days']} days")
    if data.get("retention_gb"):
        keep.append(f"{data['retention_gb']:g} GB")
    click.echo(f"  Keeps here:   {', '.join(keep) if keep else 'everything'}")
    names = [entry["name"] for entry in data["destinations"]]
    click.echo(f"  Destinations: {', '.join(names) if names else 'none'}")
    if data["enabled"]:
        click.echo(f"  Next run:     {data['timer'].get('next_run') or 'unknown'}")
    if data["last_status"]:
        click.echo(f"  Last run:     {data['last_status']} at {data['last_run_at']}")
        if data["last_error"]:
            click.echo(f"\n{data['last_error']}")


@schedule.command("show", read_only=True)
@click.argument("domain", required=False)
@json_option("Print the policies as JSON.")
@pass_context
def show_command(ctx: Context, domain: str | None) -> None:
    """Show one application's data backup policy, or every policy."""
    manager = _manager(ctx)
    try:
        views = [manager.get_policy(domain)] if domain else manager.list_policies()
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        _echo_json([view.to_dict() for view in views])
    elif not views:
        ctx.logger.info("No application has a data backup policy")
    else:
        for view in views:
            _print_policy(ctx, view.to_dict())


@schedule.command("remove")
@click.argument("domain")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, domain: str, force: bool) -> None:
    """Remove an application's data backup policy and its timer. Its backups stay."""
    if not force and not click.confirm(
        f"Remove the data backup policy of {domain}? Nothing will back its data up on a schedule"
    ):
        ctx.logger.info("Cancelled")
        return
    try:
        removed = _manager(ctx).remove_policy(domain)
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if removed:
        ctx.logger.success(f"Data backup policy removed for {domain}")
    else:
        ctx.logger.info(f"{domain} had no data backup policy")


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(volumes)
