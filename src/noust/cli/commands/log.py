# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust log`` command group: searching the logs of every application, and how long they are kept.

    noust log search timeout "connection refused" --not healthcheck --since 2h
    noust log search 'level:err app:shop.example.com' --since 1d --json
    noust log retention set --days 30 --max-use 2048
    noust log retention show

A thin front end over :class:`~noust.managers.log_search.LogSearch` and
:class:`~noust.managers.log_retention.LogRetention`, which ``/api/logs`` uses too.
(``noust logs`` with an `s` reads one application's log; this searches them all.)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.store import App, get_store
from noust.managers.log_retention import MAX_DAYS, MAX_MB, MIN_DAYS, MIN_MB, LogRetention
from noust.managers.log_search import (
    DEFAULT_LIMIT,
    LEVEL_NAMES,
    MAX_LIMIT,
    LogSearch,
    process_names,
)


@click.group("log", cls=NoustGroup)
def cli() -> None:
    """Search the logs of every application, and set how long the journal keeps them."""


@cli.command("search", read_only=True)
@click.argument("words", nargs=-1)
@click.option(
    "--app", "apps", multiple=True, metavar="DOMAIN", help="Only this application (repeat)."
)
@click.option(
    "--not",
    "excluded",
    multiple=True,
    metavar="WORD",
    help="Leave out lines with this word (repeat).",
)
@click.option(
    "--since",
    default=None,
    metavar="WHEN",
    help="Start: 15m, 2h, 7d ago, a date or epoch seconds (1h).",
)
@click.option("--until", default=None, metavar="WHEN", help="End, in the same forms (now).")
@click.option("--level", type=click.Choice(LEVEL_NAMES), default=None, help="The worst level kept.")
@click.option(
    "--limit",
    type=click.IntRange(1, MAX_LIMIT),
    default=DEFAULT_LIMIT,
    show_default=True,
    help="Most lines to print.",
)
@global_flags
@json_option("Print the result as JSON.")
@pass_context
def search_command(
    ctx: Context,
    words: tuple[str, ...],
    apps: tuple[str, ...],
    excluded: tuple[str, ...],
    since: str | None,
    until: str | None,
    level: str | None,
    limit: int,
) -> None:
    """
    Search the logs of the applications, newest first.

    Words must all appear (case does not matter): "quoted phrase" for a phrase,
    `a OR b` for either; leave a word out with --not WORD. Fields: app:DOMAIN,
    unit:NAME (a system unit), level:err.

    \b
      noust log search timeout "connection refused" --not healthcheck --since 2h
      noust log search 'level:err' --app shop.example.com --since 1d
    """
    visible: list[App] = [a for a in get_store().list_apps() if isinstance(a, App)]
    if apps:
        wanted = {name.lower() for name in apps}
        visible = [a for a in visible if a.domain.lower() in wanted]
    result = LogSearch().search(
        " ".join([*(_quote(word) for word in words), *(f"-{_quote(word)}" for word in excluded)]),
        visible,
        since=since,
        until=until,
        level=level,
        limit=limit,
        processes=process_names(visible),
        allow_system_units=True,
    )
    if ctx.json_output:
        click.echo(json.dumps({**result.to_dict(), "apps": [a.domain for a in visible]}))
        return
    for note in result.notes:
        ctx.logger.warning(note)
    if not result.lines:
        ctx.logger.info(f"Nothing matched in {result.scanned} entries examined.")
        return
    for line in result.lines:
        stamp = datetime.fromtimestamp(line.ts, tz=timezone.utc).strftime("%m-%d %H:%M:%S")
        where = line.app or line.unit
        suffix = f" {line.process}" if line.process and line.process != "web" else ""
        click.echo(f"{stamp} {line.level or '-':7} {where}{suffix}: {line.message}")
    more = result.matched - len(result.lines)
    footer = f"{len(result.lines)} lines shown"
    if more > 0:
        footer += f" of {result.matched}; narrow the search or raise --limit"
    if result.truncated:
        footer += "; the scan stopped at its limit, older matches may exist"
    ctx.logger.info(footer)


def _quote(word: str) -> str:
    """
    Args:
        word: One word as the shell gave it.

    Returns:
        It quoted so that a phrase that has a space stays one word of the query.
    """
    return json.dumps(word) if " " in word and not word.startswith(('"', "'")) else word


@cli.group("retention", cls=NoustGroup)
def retention_group() -> None:
    """How long the journal keeps the logs."""


@retention_group.command("show", read_only=True)
@global_flags
@json_option("Print the retention as JSON.")
@pass_context
def retention_show(ctx: Context) -> None:
    """Show how long the logs are kept and how much disk they take."""
    status = LogRetention().status()
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    ctx.logger.key_value(
        "Entries older than", f"{status.days} days go" if status.days else "journald's default"
    )
    ctx.logger.key_value(
        "Disk limit", f"{status.max_use_mb} MB" if status.max_use_mb else "journald's default"
    )
    ctx.logger.key_value("Kept across reboots", "yes" if status.journal_on_disk else "no")
    if status.disk_usage:
        ctx.logger.key_value("Taking now", status.disk_usage)
    for note in status.notes:
        ctx.logger.warning(note)


@retention_group.command("set")
@click.option(
    "--days",
    type=click.IntRange(MIN_DAYS, MAX_DAYS),
    help="Drop entries older than this many days.",
)
@click.option(
    "--max-use",
    "max_use",
    type=click.IntRange(MIN_MB, MAX_MB),
    metavar="MB",
    help="Use at most this many megabytes of disk.",
)
@click.option(
    "--persistent/--volatile",
    "persistent",
    default=None,
    help="Keep the journal across reboots (or only in memory).",
)
@pass_context
def retention_set(
    ctx: Context, days: int | None, max_use: int | None, persistent: bool | None
) -> None:
    """Keep the logs this long and/or within this much disk; journald is restarted (it keeps what it has)."""
    status = LogRetention().set(days=days, max_use_mb=max_use, persistent=persistent)
    ctx.logger.success("Retention saved and journald restarted")
    for note in status.notes:
        ctx.logger.warning(note)


@retention_group.command("clear")
@pass_context
def retention_clear(ctx: Context) -> None:
    """Remove Noust's retention: journald goes back to its own defaults."""
    LogRetention().clear()
    ctx.logger.success("Retention removed; journald uses its defaults")
