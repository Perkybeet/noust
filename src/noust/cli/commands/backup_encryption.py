# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust backup encryption``: the backups kept on this server's disk, sealed (checklist H-08).

The CLI of :mod:`noust.core.backup_files` and :mod:`noust.managers.backup_sealing`. ``enable``
makes the key new backups are sealed with and shows it once; ``show-key`` shows one again;
``rotate`` starts a new key without stranding the backups the old one sealed; ``disable`` stops
sealing and keeps every key; ``status`` says what is sealed and what is not; ``seal-existing``
seals the backups that were already on the disk, in clear, and writes their records again.

``noust backup`` loads it through :func:`register`.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, NoustGroup, json_option, pass_context
from noust.core.backup_files import BackupKeys
from noust.core.exceptions import BackupError, NoustError
from noust.managers.backup_sealing import SealExisting, SealItem, encryption_status


def _confirm(question: str, *, yes: bool) -> bool:
    """
    Ask before changing how backups are kept.

    Args:
        question: The question.
        yes: Skip it because the operator already said so.

    Returns:
        True when the change may go ahead.
    """
    from noust.cli.commands.db import _confirm as ask

    return ask(question, force=yes)


def _show(ctx: Context, key_id: str, passphrase: str) -> None:
    """
    Print a key, once, with what to do with it.

    Args:
        ctx: The command's context.
        key_id: The key's id.
        passphrase: Its value.
    """
    click.echo(f"Key {key_id}:")
    click.echo(f"  {passphrase}")
    ctx.logger.warning(
        "Keep this somewhere that is not this server. A backup sealed with it cannot be opened "
        "without it, and it is kept only in this server's secret store."
    )


@click.group("encryption", cls=NoustGroup)
def encryption() -> None:
    """Seal the backups kept on this server's disk, so a copy of the disk does not open them."""


@encryption.command("status", read_only=True)
@json_option("Print the status as JSON.")
@pass_context
def status(ctx: Context) -> None:
    """Say whether new backups are sealed, with which key, and how many are in clear."""
    try:
        view: dict[str, Any] = encryption_status()
    except NoustError as exc:
        ctx.logger.error(str(exc))
        click.get_current_context().exit(1)
        return
    if ctx.json_output:
        click.echo(json.dumps(view, indent=2, sort_keys=True))
        return
    if view["enabled"]:
        click.echo(f"New backups are sealed with key {view['current_key']}.")
    else:
        click.echo("New backups are kept in clear. Seal them with: noust backup encryption enable")
    click.echo(f"On the disk: {view['sealed']} sealed, {view['clear']} in clear.")
    if view["clear"] and view["enabled"]:
        click.echo(
            "The ones in clear were made before sealing was on: they stay as they are until "
            "their retention removes them, or until you seal them with: "
            "noust backup encryption seal-existing"
        )
    click.echo(f"Keys held: {', '.join(k['id'] for k in view['keys']) or 'none'}")


@encryption.command("enable")
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def enable(ctx: Context, yes: bool) -> None:
    """
    Seal every backup this server makes from now on, and show the key once.

    Applications' archives, their data, database dumps and the base backups of point-in-time
    recovery are written as encrypted, authenticated files under the same names; every restore,
    check and send goes on working. Backups made before stay as they are.
    """
    keys = BackupKeys()
    if keys.enabled():
        click.echo(f"New backups are already sealed with key {keys.current_id()}.")
        return
    if not _confirm("Seal the backups made from now on?", yes=yes):
        click.echo("Nothing was changed.")
        return
    key_id, _ = keys.enable()
    passphrase = keys.passphrase(key_id) or ""
    ctx.logger.success(f"New backups are sealed with key {key_id}")
    _show(ctx, key_id, passphrase)
    ctx.logger.info(
        "Backups already on this disk stay in clear; seal them with: "
        "noust backup encryption seal-existing"
    )


@encryption.command("show-key")
@click.argument("key_id", required=False)
@pass_context
def show_key(ctx: Context, key_id: str | None) -> None:
    """Show a key (the current one by default), to keep it somewhere else."""
    try:
        chosen, passphrase = BackupKeys().reveal(key_id)
    except BackupError as exc:
        ctx.logger.error(exc.message, exc.details or "")
        click.get_current_context().exit(1)
        return
    _show(ctx, chosen, passphrase)


@encryption.command("import-key")
@click.argument("key_id")
@click.option(
    "--passphrase-file",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    help="Read the key from this file instead of asking for it.",
)
@pass_context
def import_key(ctx: Context, key_id: str, passphrase_file: str | None) -> None:
    """
    Put back a key this server lost, from the copy you kept elsewhere.

    The key is read from a file or typed at a prompt that does not echo; it is never an argument,
    because everything on a command line is visible to every local user. A backup sealed with it
    opens again.
    """
    from pathlib import Path

    from noust.core.exceptions import BackupError

    if passphrase_file is not None:
        passphrase = Path(passphrase_file).read_text(encoding="utf-8").strip()
    else:
        passphrase = click.prompt("Key", hide_input=True).strip()
    try:
        BackupKeys().import_key(key_id, passphrase)
    except BackupError as exc:
        ctx.logger.error(str(exc))
        click.get_current_context().exit(1)
        return
    ctx.logger.success(f"Key {key_id} is back; the backups it sealed open again")


@encryption.command("rotate")
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def rotate(ctx: Context, yes: bool) -> None:
    """
    Make a new key for the backups made from now on. The old key stays, and opens what it sealed.
    """
    keys = BackupKeys()
    if not keys.enabled():
        click.echo("Backups are not sealed: nothing to rotate. Use: noust backup encryption enable")
        return
    if not _confirm("Make a new key for the backups made from now on?", yes=yes):
        click.echo("Nothing was changed.")
        return
    key_id = keys.rotate()
    ctx.logger.success(f"New backups are sealed with key {key_id}")
    _show(ctx, key_id, keys.passphrase(key_id) or "")


@encryption.command("disable", cls=NoustCommand)
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def disable(ctx: Context, yes: bool) -> None:
    """Stop sealing new backups. Every key is kept, so what was sealed still opens."""
    keys = BackupKeys()
    if not keys.enabled():
        click.echo("Backups are not sealed.")
        return
    if not _confirm("Keep new backups in clear?", yes=yes):
        click.echo("Nothing was changed.")
        return
    keys.stop_sealing()
    ctx.logger.success("New backups are kept in clear; the keys are kept")


def _progress(ctx: Context, item: SealItem) -> None:
    """
    Print one backup's outcome as it is done.

    Args:
        ctx: The command's context.
        item: What became of it.
    """
    line = f"  {item.kind} {item.name}: {item.state.replace('_', ' ')}"
    if item.detail:
        ctx.logger.warning(f"{line} - {item.detail}")
    else:
        ctx.logger.info(line)


@encryption.command("seal-existing")
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@json_option("Print the report as JSON (needs --yes: nothing is asked).")
@pass_context
def seal_existing(ctx: Context, yes: bool) -> None:
    """
    Seal the backups already on this disk that were made in clear.

    Applications' archives, their data, database dumps and the base backups of point-in-time
    recovery keep their names and become encrypted, authenticated files, and their records (size,
    checksum, the sidecar's MAC) are written again, so every check, restore and send goes on
    working. A backup that does not match its record is left in clear and said. It can be stopped
    and run again. It needs room for one more copy of the largest backup for a moment. Not covered:
    the archived write-ahead log, files no record describes and what a destination already holds.
    """
    if ctx.json_output and not yes:
        raise click.UsageError("--json asks nothing, so it needs --yes.")
    sealer = SealExisting()
    try:
        plan = sealer.plan()
    except NoustError as exc:
        ctx.logger.error(exc.message, exc.details or "")
        click.get_current_context().exit(1)
        return
    if not plan["sealing"]:
        ctx.logger.error(
            "Backups are not sealed on this server",
            "Turn sealing on first: noust backup encryption enable",
        )
        click.get_current_context().exit(1)
        return
    if plan["total"] == 0:
        if ctx.json_output:
            click.echo(json.dumps({"ok": True, "counts": {}, "clear_left": 0, "items": []}))
        else:
            click.echo("Nothing is in clear that a record describes.")
        return
    if not ctx.json_output:
        click.echo(
            f"In clear: {plan['applications']} application archive(s), {plan['data']} data "
            f"backup(s), {plan['databases']} database dump(s), {plan['pitr']} base backup(s)."
        )
        if not _confirm(f"Seal these {plan['total']} backup(s) now?", yes=yes):
            click.echo("Nothing was changed.")
            return
    try:
        report = sealer.run(on_item=None if ctx.json_output else lambda item: _progress(ctx, item))
    except NoustError as exc:
        ctx.logger.error(exc.message, exc.details or "")
        click.get_current_context().exit(1)
        return
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    else:
        counts = report.counts()
        ctx.logger.success(
            f"Sealed {report.sealed}; already sealed {counts.get('already', 0)}; "
            f"left as they were {len(report.items) - report.sealed - counts.get('already', 0)}"
        )
        if report.clear_left:
            ctx.logger.info(
                f"{report.clear_left} file(s) on the disk are still in clear: the ones left as "
                "they were above, and files no record describes (a restore's safety copy, a dump "
                "put there by hand)."
            )
    if not report.ok:
        click.get_current_context().exit(1)


def register(group: click.Group) -> None:
    """
    Join ``noust backup``: what :mod:`noust.cli.commands.backup` calls.

    Args:
        group: The ``backup`` group.
    """
    group.add_command(encryption)
