# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db servers``: where databases run, and the default of each engine (4.1, item 103).

A database server is an engine in a version running somewhere on this server: the system's own,
or a container Noust runs beside it. ``noust db servers`` lists them all in one table, with the
default of each engine (the server a database made with just the engine's name goes to) and when
each version's support ends. ``noust db servers default KEY`` chooses the default and ``--clear``
takes the choice back. The work is
:class:`noust.managers.database.servers.DatabaseServers`, which the console's API calls too; this
module parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from noust.cli.app import (
    Context,
    NoustGroup,
    global_flags,
    json_option,
    pass_context,
)
from noust.core.logger import Logger
from noust.managers.database.servers import DatabaseServers

#: Where a server runs, as a person reads it.
_RUNS_IN = {
    "system": "system",
    "noust_container": "container (Noust)",
    "app_container": "container (an application's)",
}


def _servers(ctx: Context) -> DatabaseServers:
    """
    Args:
        ctx: The invocation's context.

    Returns:
        The servers over ``noust db``'s service, so an engine resolves the way every ``noust db``
        command resolves it. With ``--json`` its progress goes to stderr, so stdout is the JSON.
    """
    from noust.cli.commands.db import _service

    logger = ctx.logger
    if ctx.json_output:
        logger = Logger(verbose=ctx.logger.verbose, stream=sys.stderr)
    return DatabaseServers(_service(logger))


def _row(server: dict[str, Any]) -> list[str]:
    """
    Args:
        server: A server, as the listing describes it.

    Returns:
        The cells of its line.
    """
    ends = server["end_of_life"] or "-"
    if server["support"] == "ended":
        ends += " (ended)"
    count = "-" if server["databases"] is None else str(server["databases"])
    chosen = ""
    if server["default"]:
        chosen = "default" if server["default_source"] != "chosen" else "default (chosen)"
    return [
        server["key"],
        f"{server['display_name']} {server['version'] or ''}".strip(),
        _RUNS_IN[server["runs_in"]],
        server["state"],
        ends,
        count,
        chosen,
    ]


@click.group("servers", cls=NoustGroup, invoke_without_command=True)
@click.option(
    "--engine",
    "-e",
    metavar="ENGINE",
    help="Only this engine: mysql is MySQL and MariaDB, mariadb only MariaDB.",
)
@global_flags
@json_option("Print the servers as JSON.")
@click.pass_context
def servers_group(click_ctx: click.Context, engine: str | None) -> None:
    """The database servers: where databases run, and the default of each engine."""
    if click_ctx.invoked_subcommand is None:
        click_ctx.invoke(list_command, engine=engine)


@servers_group.command("list", read_only=True)
@click.option(
    "--engine",
    "-e",
    metavar="ENGINE",
    help="Only this engine: mysql is MySQL and MariaDB, mariadb only MariaDB.",
)
@global_flags
@json_option("Print the servers as JSON.")
@pass_context
def list_command(ctx: Context, engine: str | None) -> None:
    """List the database servers: the system's engines and the containers, in one table."""
    listing = _servers(ctx).all(engine=engine)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "servers": listing.servers,
                    "problems": [problem.to_dict() for problem in listing.problems],
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
        )
        return
    logger = ctx.logger
    if not listing.servers:
        logger.info(
            "No database servers. Install an engine with: noust db install ENGINE, or run one in a "
            "container with: noust db instance create NAME --engine ENGINE --memory 512m"
        )
    else:
        logger.table(
            ["Key", "Engine", "Runs in", "State", "Support ends", "Databases", "Default"],
            [_row(server) for server in listing.servers],
        )
    for problem in listing.problems:
        logger.warning(f"{problem.display_name}: {problem.message}")
        if problem.hint:
            logger.info(problem.hint)


@servers_group.command("default")
@click.argument("key")
@click.option(
    "--clear",
    is_flag=True,
    help="Take the choice back: the engine's default is the one Noust works out (the system's "
    "engine, else the only one, else the oldest).",
)
@global_flags
@json_option("Print the server as JSON.")
@pass_context
def default_command(ctx: Context, key: str, clear: bool) -> None:
    """
    Make a server the one a new database of its engine is made on.

    KEY is a key of 'noust db servers': the system's engine (mysql) or a container Noust runs
    (mysql@legacy). A database made with just the engine's name (noust db create --engine mysql,
    or the database a new application asks for) goes to it.
    """
    servers = _servers(ctx)
    server = servers.clear_default(key) if clear else servers.set_default(key)
    if ctx.json_output:
        click.echo(json.dumps(server, indent=2, sort_keys=True, default=str))
        return
    if clear:
        ctx.logger.success(
            f"{server['key']} is no longer the chosen default of {server['display_name']}"
        )
    else:
        ctx.logger.success(
            f"New {server['display_name']} databases are made on {server['key']} unless another "
            "server is named"
        )


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(servers_group)
