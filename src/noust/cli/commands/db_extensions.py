# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db extension``: the PostgreSQL extensions of a database (PostGIS, pgvector, ...).

An extension is loaded into one database. Which ones can be is what the server has on disk: the
host's packages, or the image of an instance. ``list`` shows the server's own list and what is
loaded, ``add`` loads one (``CREATE EXTENSION``, audited), ``remove`` unloads it. The work is
:class:`noust.managers.database.extensions.Extensions`, which the console's API calls too; this
module parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.managers.database.extensions import Extensions


class _Engine(click.ParamType):
    """An engine name, checked by ``noust db``'s own type at call time."""

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


ENGINE = _Engine()


def _extensions(ctx: Context) -> Extensions:
    """
    Build the class every command here uses.

    Args:
        ctx: The invocation's context.

    Returns:
        The manager over ``noust db``'s service; with ``--json`` its progress goes to stderr.
    """
    from noust.cli.commands.db import _service

    logger = ctx.logger
    if ctx.json_output:
        logger = Logger(verbose=ctx.logger.verbose, stream=sys.stderr)
    return Extensions(_service(logger))


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


@click.group("extension", cls=NoustGroup)
def extension() -> None:
    """Load and unload the PostgreSQL extensions of a database."""


@extension.command("list", read_only=True)
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="The PostgreSQL it is on.")
@click.option("--all", "show_all", is_flag=True, help="Also the extensions that are not loaded.")
@global_flags
@json_option("Print the extensions as JSON.")
@pass_context
def list_command(ctx: Context, database: str, engine: str, show_all: bool) -> None:
    """
    List the extensions of a database: the loaded ones, or with --all everything the server offers.

    The list is the server's own (what its packages or its image installed), so an extension that
    is not in it needs its package first.
    """
    found = _extensions(ctx).available(engine, database)
    if ctx.json_output:
        _echo_json(found.to_dict())
        return
    rows = [item for item in found.extensions if show_all or item.installed]
    if not rows:
        ctx.logger.info(
            f"{database} has no extension loaded. See what the server offers: "
            f"noust db extension list {database} -e {engine} --all"
        )
        return
    ctx.logger.table(
        ["Extension", "Loaded", "Offered", "Trusted", "What it is"],
        [
            [
                item.name,
                item.installed_version or "-",
                item.default_version,
                "yes" if item.trusted else "no",
                item.comment,
            ]
            for item in rows
        ],
    )
    if show_all:
        ctx.logger.info(found.source)


@extension.command("add")
@click.argument("database")
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="The PostgreSQL it is on.")
@click.option(
    "--cascade",
    is_flag=True,
    help="Also load the extensions it needs (CREATE EXTENSION ... CASCADE).",
)
@global_flags
@json_option("Print the extension as JSON.")
@pass_context
def add_command(ctx: Context, database: str, name: str, engine: str, cascade: bool) -> None:
    """
    Load an extension into a database (CREATE EXTENSION).

    NAME must be one the server offers ('noust db extension list --all'). The statement runs as
    the cluster's superuser and is recorded in the audit trail.
    """
    if is_rehearsal():
        ctx.logger.info(f"Would load {name} into {database}")
        return
    info = _extensions(ctx).add(engine, database, name, cascade=cascade)
    if ctx.json_output:
        _echo_json(info.to_dict())
        return
    ctx.logger.success(f"{info.name} {info.installed_version} is loaded in {database}")


@extension.command("remove")
@click.argument("database")
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="The PostgreSQL it is on.")
@click.option(
    "--cascade", is_flag=True, help="Also drop what depends on it. Without it PostgreSQL refuses."
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask before unloading it.")
@global_flags
@json_option("Print the extension as JSON.")
@pass_context
def remove_command(
    ctx: Context, database: str, name: str, engine: str, cascade: bool, yes: bool
) -> None:
    """
    Unload an extension from a database (DROP EXTENSION).

    PostgreSQL refuses while anything depends on it, and says what; --cascade drops that too,
    which can mean columns of the extension's types.
    """
    if is_rehearsal():
        ctx.logger.info(f"Would unload {name} from {database}")
        return
    extensions = _extensions(ctx)
    info = extensions.get(engine, database, name)
    if not yes and not ctx.json_output:
        click.confirm(
            f"Unload {info.name} from {database}"
            f"{' and drop whatever depends on it' if cascade else ''}?",
            default=False,
            abort=True,
        )
    info = extensions.remove(engine, database, name, cascade=cascade)
    if ctx.json_output:
        _echo_json(info.to_dict())
        return
    ctx.logger.success(f"{info.name} is no longer loaded in {database}")


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(extension)
