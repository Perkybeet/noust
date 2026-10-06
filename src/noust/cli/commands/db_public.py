# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db public``: make a database reachable from the network, on purpose, and private again.

A database is private: its engine listens on the loopback and ``noust db
connect-info`` prints the SSH tunnel to reach it. ``open`` is the deliberate
alternative for a desktop client: a public port, an account of its own for
that database, TLS required where the engine can, and the firewall told. It
asks first, with every consequence, and ``close`` undoes all of it. The work is
:class:`noust.managers.database.public_access.PublicAccess`, which the console's
API calls too; this module parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls
:func:`register`, so the commands join its group without this module importing
that one at load time.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.database_exposures import Exposure
from noust.core.exceptions import ConfirmationRequired, NoustError
from noust.core.fs import is_rehearsal
from noust.managers.database.base import PROFILES
from noust.managers.database.public_access import PublicAccess, PublicPlan


class _Engine(click.ParamType):
    """
    An engine name, checked by ``noust db``'s own engine type, at call time.

    Deferred so this module never imports ``noust.cli.commands.db`` while that
    module is still loading.
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


ENGINE = _Engine()

#: The profiles as the command line spells them.
ACCESS = click.Choice([name.replace("_", "-") for name in PROFILES])


def _access(ctx: Context) -> PublicAccess:
    """
    Build the class every command here uses.

    Args:
        ctx: The invocation's context.

    Returns:
        The public access manager over ``noust db``'s service, so an engine
        resolves the way every ``noust db`` command resolves it.
    """
    from noust.cli.commands.db import _service

    return PublicAccess(_service(ctx.logger))


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _show_plan(ctx: Context, plan: PublicPlan) -> None:
    """
    Print what opening a database would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Database", f"{plan.engine}/{plan.database}")
    logger.key_value("Public port", f"{plan.bind_address}:{plan.port} -> {plan.target}")
    logger.key_value("Reachable from", ", ".join(plan.sources))
    logger.key_value("Account", f"{plan.remote_user} ({plan.profile})")
    logger.key_value("TLS", plan.tls)
    firewall = plan.firewall
    logger.key_value(
        "Firewall",
        f"{firewall['backend']} ({'active' if firewall['active'] else 'not active'})"
        if firewall["manages"]
        else "none that Noust changes",
    )
    for warning in plan.warnings:
        logger.warning(warning)


def _show_exposure(ctx: Context, exposure: Exposure, *, live: bool | None = None) -> None:
    """
    Print one database's exposure.

    Args:
        ctx: The invocation's context.
        exposure: The record.
        live: Whether the port is listening now, when known.
    """
    logger = ctx.logger
    logger.key_value("Database", f"{exposure.engine}/{exposure.db_name}")
    logger.key_value(
        "State",
        exposure.state + ("" if live is None else (" (listening)" if live else " (NOT listening)")),
    )
    logger.key_value("Public port", f"{exposure.bind_address}:{exposure.port}")
    logger.key_value("Reachable from", ", ".join(exposure.sources))
    logger.key_value("Account", f"{exposure.remote_user} ({exposure.profile})")
    logger.key_value("TLS", exposure.tls)
    logger.key_value(
        "Opened",
        f"{exposure.created_at[:19].replace('T', ' ')} by {exposure.created_by or 'unknown'}",
    )


@click.group("public", cls=NoustGroup)
def public() -> None:
    """Make a database reachable from the network on purpose, and private again."""


@public.command("open")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--port", type=int, help="The public port. A free one near the engine's is chosen.")
@click.option(
    "--bind",
    "bind_address",
    help="Address to listen on: 0.0.0.0 (every IPv4 address, the default), :: or one address.",
)
@click.option(
    "--from",
    "sources",
    multiple=True,
    metavar="ADDRESS",
    help="Let only this address or network (CIDR) in; repeat for several. Anyone when omitted.",
)
@click.option(
    "--allow-plaintext",
    is_flag=True,
    help="Do not require TLS. The password and the data then cross the network in clear.",
)
@click.option(
    "--access",
    type=ACCESS,
    default="owner",
    show_default=True,
    help="What the account made for it may do on this database.",
)
@click.option("--yes", "-y", is_flag=True, help="Accept every warning without asking.")
@global_flags
@json_option("Print the outcome (with the password, once) as JSON.")
@pass_context
def open_command(
    ctx: Context,
    database: str,
    engine: str,
    port: int | None,
    bind_address: str | None,
    sources: tuple[str, ...],
    allow_plaintext: bool,
    access: str,
    yes: bool,
) -> None:
    """
    Make DATABASE reachable from the network: a public port, an account of its own, the firewall.

    The engine is not reconfigured or restarted: a proxy on the port forwards
    to its loopback port, and 'noust db public close' removes it. The ENS
    profile refuses this. Everything it will do and everything you accept is
    printed first and asked about.
    """
    manager = _access(ctx)
    profile = access.replace("-", "_")
    tls = "optional" if allow_plaintext else None
    plan = manager.plan(
        engine,
        database,
        port=port,
        bind_address=bind_address,
        sources=list(sources),
        tls=tls,
        profile=profile,
    )
    if is_rehearsal():
        ctx.logger.info(f"Would make {engine}/{database} reachable on port {plan.port}")
        _show_plan(ctx, plan)
        return
    if not yes:
        _show_plan(ctx, plan)
        if not click.confirm("Make it reachable from the network?", default=False):
            raise click.Abort()
    try:
        outcome = manager.expose(
            engine,
            database,
            port=port,
            bind_address=bind_address,
            sources=list(sources),
            tls=tls,
            profile=profile,
            confirm=True,
        )
    except ConfirmationRequired as exc:  # pragma: no cover - confirm=True never raises it
        raise NoustError("Not confirmed", details="; ".join(exc.warnings)) from exc
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    logger = ctx.logger
    logger.success(f"{engine}/{database} is reachable on port {outcome.exposure.port}")
    connection = outcome.connection
    logger.key_value("Host", f"{connection['host']}:{connection['port']}")
    logger.key_value("Database", str(connection["database"]))
    logger.key_value("User", str(connection["username"]))
    logger.key_value("Password", str(connection["password"]))
    logger.key_value("URL", str(connection["url"]))
    logger.info(
        "The password is shown here once; 'noust db public show "
        f"{database} -e {engine} --reveal' prints it again."
    )
    if outcome.exposure.tls == "required":
        logger.info("The account must use TLS: connect with " + _tls_hint(engine))


def _tls_hint(engine: str) -> str:
    """
    Say how a client asks for TLS.

    Args:
        engine: The engine.

    Returns:
        A short phrase.
    """
    return {
        "postgresql": "sslmode=require (psql, TablePlus: SSL mode Require).",
        "mysql": "--ssl-mode=REQUIRED (mysql, TablePlus: SSL mode Required).",
    }.get(engine, "TLS on.")


@public.command("close")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@global_flags
@json_option("Print what was removed as JSON.")
@pass_context
def close_command(ctx: Context, database: str, engine: str, yes: bool) -> None:
    """
    Make DATABASE private again: remove the port, the account, the TLS line and the firewall rule.

    Only what making it public created is removed. Connections that are open
    end with the proxy.
    """
    manager = _access(ctx)
    record = manager.get(engine, database)
    if record is None:
        if ctx.json_output:
            _echo_json({"engine": engine, "database": database, "already_private": True})
        else:
            ctx.logger.info(f"{engine}/{database} is not reachable from the network")
        return
    if not yes and not ctx.dry_run:
        click.confirm(
            f"Close port {record.port} and drop the account {record.remote_user}? Clients "
            "connected through it are cut off",
            abort=True,
        )
    if is_rehearsal():
        ctx.logger.info(f"Would make {engine}/{database} private: close port {record.port}")
        return
    outcome = manager.make_private(engine, database)
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    ctx.logger.success(f"{engine}/{database} is private again")
    if outcome.removed:
        ctx.logger.info("Removed: " + ", ".join(outcome.removed))


@public.command("list", read_only=True)
@global_flags
@json_option("Print the databases as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the databases that are reachable from the network."""
    manager = _access(ctx)
    exposures = manager.listing()
    listening = manager.listening()
    if ctx.json_output:
        _echo_json(
            {
                "exposures": [
                    entry.to_dict() | {"listening": entry.port in listening} for entry in exposures
                ]
            }
        )
        return
    if not exposures:
        ctx.logger.info("Every database is private.")
        return
    ctx.logger.table(
        ["Database", "Port", "From", "Account", "TLS", "State"],
        [
            [
                f"{entry.engine}/{entry.db_name}",
                f"{entry.bind_address}:{entry.port}",
                ", ".join(entry.sources),
                f"{entry.remote_user} ({entry.profile})",
                entry.tls,
                entry.state + ("" if entry.port in listening else " (not listening)"),
            ]
            for entry in exposures
        ],
    )


@public.command("show", read_only=True)
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--reveal", is_flag=True, help="Print the account's password too, in the connection details."
)
@global_flags
@json_option("Print the exposure (and, with --reveal, the connection) as JSON.")
@pass_context
def show_command(ctx: Context, database: str, engine: str, reveal: bool) -> None:
    """Show how DATABASE is reachable, and whether it listens now."""
    manager = _access(ctx)
    record = manager.get(engine, database)
    if record is None:
        if ctx.json_output:
            _echo_json({"engine": engine, "database": database, "public": False})
        else:
            ctx.logger.info(f"{engine}/{database} is private: not reachable from the network")
        return
    live = record.port in manager.listening()
    connection = manager.reveal(engine, database) if reveal else manager.connection(record, None)
    if ctx.json_output:
        _echo_json(
            {"public": True, "exposure": record.to_dict(), "listening": live}
            | {"connection": connection}
        )
        return
    _show_exposure(ctx, record, live=live)
    ctx.logger.key_value("URL", str(connection["url"]))
    if reveal:
        ctx.logger.key_value("Password", str(connection["password"]))
    else:
        ctx.logger.info("The password: 'noust db public show ... --reveal'.")


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(public)
