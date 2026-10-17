# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db sqlite``: the SQLite databases an application keeps.

``list`` finds them (one application's, or every application's), ``check`` asks SQLite whether
one is sound, and ``copy`` makes a consistent copy of one, in use or not, in a file of its own.
The work is :mod:`noust.managers.sqlite_files`, which the console's API calls too; this module
parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.fs import is_rehearsal
from noust.core.store import App, get_store
from noust.managers import sqlite_files


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _app(domain: str) -> App:
    """
    Args:
        domain: What the operator typed.

    Returns:
        The application's row.

    Raises:
        ValidationError: When nothing is deployed at that domain.
    """
    found = get_store().get_app(domain)
    if found is None:
        raise ValidationError(
            f"No application is deployed at {domain}",
            details="See what is: noust list",
        )
    return found


def _size(value: int) -> str:
    """
    Args:
        value: Bytes.

    Returns:
        The size in the largest unit that keeps it above one.
    """
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


@click.group("sqlite", cls=NoustGroup)
def sqlite() -> None:
    """The SQLite databases an application keeps: list, check and copy them."""


@sqlite.command("list", read_only=True)
@click.argument("domain", required=False)
@global_flags
@json_option("Print the databases as JSON.")
@pass_context
def list_command(ctx: Context, domain: str | None) -> None:
    """
    List the SQLite databases applications keep, in their data directories.

    Without DOMAIN, every application's. The size is the file's; WAL is what is committed and
    still waiting in its write-ahead log. An application backup copies each of these
    consistently, while the application runs.
    """
    apps = [_app(domain)] if domain else list(get_store().list_apps())
    listings = [sqlite_files.find(app) for app in apps]
    if ctx.json_output:
        _echo_json([listing.to_dict() for listing in listings])
        return
    rows = [
        [
            listing.domain,
            item.path,
            _size(item.size),
            f"{item.journal_mode}, {_size(item.wal_bytes)} waiting"
            if item.wal_bytes
            else item.journal_mode,
            datetime.fromtimestamp(item.modified, tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
        ]
        for listing in listings
        for item in listing.files
    ]
    if not rows:
        ctx.logger.info(
            f"No SQLite database found in {domain}'s data"
            if domain
            else "No application keeps a SQLite database"
        )
        return
    ctx.logger.table(["Application", "File", "Size", "Journal", "Changed (UTC)"], rows)
    if any(listing.truncated for listing in listings):
        ctx.logger.warning("The search stopped at its limit: there may be more.")


@sqlite.command("check", read_only=True)
@click.argument("domain")
@click.argument("path")
@click.option(
    "--full",
    is_flag=True,
    help="Run integrity_check, which also checks every index against its table; slower.",
)
@click.option(
    "--budget",
    type=click.FloatRange(min=1, max=86400),
    default=sqlite_files.DEFAULT_BUDGET,
    show_default=True,
    help="Seconds after which a check stops where it is and says so.",
)
@global_flags
@json_option("Print the result as JSON.")
@pass_context
def check_command(ctx: Context, domain: str, path: str, full: bool, budget: float) -> None:
    """
    Ask SQLite whether a database is sound. Changes nothing.

    PATH is relative to the application's directory, as 'noust db sqlite list' shows it. The
    database stays open for the application the whole time; a database in the rollback journal
    mode accepts no write while SQLite reads it.
    """
    result = sqlite_files.check(_app(domain), path, full=full, budget=budget)
    if ctx.json_output:
        _echo_json(result.to_dict())
    elif result.ok and result.complete:
        ctx.logger.success(f"{result.path}: nothing wrong found ({result.seconds} s)")
    elif result.ok:
        ctx.logger.warning(
            f"{result.path}: nothing wrong found up to where the {budget:g} s budget ended"
        )
    else:
        ctx.logger.error(f"{result.path}: SQLite found problems")
        for problem in result.problems:
            ctx.logger.substep(problem)
    if not result.ok:
        raise SystemExit(1)


@sqlite.command("copy")
@click.argument("domain")
@click.argument("path")
@click.option(
    "--output",
    "-o",
    required=True,
    type=click.Path(path_type=Path, dir_okay=False),
    help="The file to write. It must not exist; it is created private (0600).",
)
@global_flags
@json_option("Print the copy as JSON.")
@pass_context
def copy_command(ctx: Context, domain: str, path: str, output: Path) -> None:
    """
    Copy a database, consistently, while the application runs.

    The copy is SQLite's backup API: every committed transaction, the ones still in the
    write-ahead log included, and none half-written. It is the same copy an application backup
    takes. Take it before a risky change; keep it somewhere private, it holds the application's
    data.
    """
    app = _app(domain)
    if is_rehearsal():
        sqlite_files.locate(app, path)
        ctx.logger.info(f"Would copy {path} of {domain} to {output}")
        return
    written = sqlite_files.copy_to(app, path, output)
    if ctx.json_output:
        _echo_json(
            {"domain": domain, "path": path, "output": str(written), "size": written.stat().st_size}
        )
        return
    ctx.logger.success(f"{path} of {domain} copied to {written} ({_size(written.stat().st_size)})")


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(sqlite)
