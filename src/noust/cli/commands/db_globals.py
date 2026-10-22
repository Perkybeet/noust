# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db globals``: the roles of a PostgreSQL server, which no database dump carries.

The client of :meth:`~noust.managers.database.postgres.PostgresManager.backup_globals` and
:meth:`~noust.managers.database.postgres.PostgresManager.restore_globals`. An application backup
and the bundle of Noust's state already carry them; this is for a dump you restore by hand on a
new server: take the roles on the old one, create the missing ones on the new one, then restore the
dump.

``noust db`` (:mod:`noust.cli.commands.db`) loads :mod:`noust.cli.commands.db_backups`, which calls
:func:`register`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError, ValidationError
from noust.managers.database.postgres import PostgresManager


def _manager(ctx: Context) -> PostgresManager:
    """
    Build the manager every command here uses.

    Args:
        ctx: The invocation's context.

    Returns:
        The manager over the server's PostgreSQL.

    Raises:
        ValidationError: When the engine resolved is not PostgreSQL's manager.
    """
    from noust.cli.commands.db import _service

    manager = _service(ctx.logger).manager("postgresql")
    if not isinstance(manager, PostgresManager):
        raise ValidationError("Roles of their own are PostgreSQL's", field="engine")
    return manager


@click.group("globals", cls=NoustGroup)
def globals_group() -> None:
    """The roles of a PostgreSQL server, which no database dump carries."""


@globals_group.command("dump")
@click.option(
    "--output",
    "-o",
    "output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the file here instead of the backup directory.",
)
@json_option("Print the result as JSON.")
@pass_context
def dump(ctx: Context, output: Path | None) -> None:
    """
    Dump the roles of the server with pg_dumpall --globals-only.

    The file holds the roles' password hashes: it is written readable by root alone.
    """
    try:
        info = _manager(ctx).backup_globals(output_path=output, compress=output is None)
    except NoustError as exc:
        ctx.logger.error(str(exc))
        click.get_current_context().exit(1)
        return
    if ctx.json_output:
        click.echo(json.dumps({"path": str(info.path), "size": info.size}, indent=2))
        return
    ctx.logger.success(f"Roles dumped: {info.path} ({info.size} bytes)")
    ctx.logger.info("It holds password hashes. Keep it as private as the dumps.")


@globals_group.command("list", read_only=True)
@json_option("Print the files as JSON.")
@pass_context
def list_files(ctx: Context) -> None:
    """List the files of roles this server has dumped, newest first."""
    try:
        files = _manager(ctx).list_globals()
    except NoustError as exc:
        ctx.logger.error(str(exc))
        click.get_current_context().exit(1)
        return
    if ctx.json_output:
        click.echo(json.dumps([str(path) for path in files], indent=2))
        return
    if not files:
        click.echo("No file of roles yet. Take one with: noust db globals dump")
    for path in files:
        click.echo(str(path))


@globals_group.command("restore")
@click.argument("source", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the result as JSON.")
@pass_context
def restore(ctx: Context, source: Path, yes: bool) -> None:
    """
    Create the roles of a file that this server does not have.

    Only what is missing is created: no role that exists is altered, and the superuser's
    password is never touched. The file is read statement by statement and only role statements
    are run. Do this before restoring a dump by hand on a new server.
    """
    from noust.cli.commands.db import _confirm

    if not _confirm(
        f"Create on this server the roles of {source.name} that it does not have?", force=yes
    ):
        ctx.logger.info("Cancelled")
        return
    try:
        result = _manager(ctx).restore_globals(source)
    except NoustError as exc:
        ctx.logger.error(str(exc))
        click.get_current_context().exit(1)
        return
    data: dict[str, Any] = {
        "created": list(result.created),
        "present": list(result.present),
        "left_out": list(result.left_out),
        "errors": list(result.errors),
    }
    if ctx.json_output:
        click.echo(json.dumps(data, indent=2))
    else:
        if result.created:
            ctx.logger.success(f"Created: {', '.join(result.created)}")
        else:
            ctx.logger.info("Every role in the file is already here; nothing was created.")
        for item in result.left_out:
            ctx.logger.info(f"Left out: {item}")
        for item in result.errors:
            ctx.logger.warning(f"The server refused: {item}")
    if result.errors:
        click.get_current_context().exit(1)


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db_backups` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(globals_group)
