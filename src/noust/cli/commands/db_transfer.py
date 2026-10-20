# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db transfer``: move a database to another server of the fleet (checklist C-21).

A client of the fleet engine (:mod:`noust.web.fleet_database_actions`) exactly as ``noust fleet
placement ship`` is: this machine is the central, it asks the source for a dump, carries it through
the two tunnels and has the target load it with its own import. This module parses arguments and
prints the plan; it dumps, reads and loads nothing (rule 3).

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.cli.commands.db_backups import ENGINE


@click.command("transfer", cls=NoustCommand, read_only=lambda params: bool(params.get("plan_only")))
@click.argument("database")
@click.option(
    "--engine",
    "-e",
    type=ENGINE,
    default="postgresql",
    show_default=True,
    help="The engine it is on at the source: postgresql, mysql (MariaDB too) or mongodb, or an "
    "instance such as postgresql@pg16.",
)
@click.option("--from", "from_node", required=True, help="The server that holds the database.")
@click.option("--to", "to", required=True, help="The server that receives it.")
@click.option(
    "--to-engine",
    "target_engine",
    type=ENGINE,
    default=None,
    help="The engine or instance on the target, when it is not the source's own.",
)
@click.option("--as", "new_name", default=None, help="Its name on the target; the same by default.")
@click.option(
    "--replace",
    is_flag=True,
    help="Replace a database of that name the target holds, after a safety copy of it taken there.",
)
@click.option(
    "--accounts",
    is_flag=True,
    help="Also make the accounts with access to it, with the password the source holds for each "
    "and the profile they had. The passwords pass through this machine and are kept nowhere.",
)
@click.option(
    "--drop-source",
    is_flag=True,
    help="Drop it from the source once it has moved, after a last dump kept there. Refused while "
    "an application uses it.",
)
@click.option("--plan", "plan_only", is_flag=True, help="Only show what would happen.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask first.")
@json_option("Print the plan, then the job's result, as JSON.")
@pass_context
def transfer_command(
    ctx: Context,
    database: str,
    engine: str,
    from_node: str,
    to: str,
    target_engine: str | None,
    new_name: str | None,
    replace: bool,
    accounts: bool,
    drop_source: bool,
    plan_only: bool,
    assume_yes: bool,
) -> None:
    """
    Move DATABASE from one server of the fleet to another.

    This machine is the central: the source dumps the database, the dump travels through the two
    tunnels in chunks that resume (this machine keeps none of it), and the target loads it with its
    own import, as the database's owner and never as the superuser. The source keeps the database
    untouched unless --drop-source says otherwise. The copy is the database at the moment of the
    dump: writes after it are not carried, so stop what writes first when that matters. The
    applications that use it are not pointed at the new server. The exit status is 1 when it
    failed.
    """
    from noust.cli.commands.fleet_views import _run
    from noust.web.fleet_database_actions import transfer_request

    fleet_request = transfer_request(
        database,
        to=to,
        engine=engine,
        from_node=from_node,
        target_engine=target_engine,
        new_name=new_name,
        replace=replace,
        accounts=accounts,
        drop_source=drop_source,
    )
    _run(ctx, fleet_request, assume_yes, plan_only)


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(transfer_command)
