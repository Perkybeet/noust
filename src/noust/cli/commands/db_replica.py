# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db replica``: read replicas of the host's PostgreSQL, over TLS (checklist C-14).

The client of :class:`~noust.managers.database.replication.ReplicationManager`: the same plan,
the same refusals, in the same words. This module parses arguments, asks before restarting
PostgreSQL or replacing a server's data, and prints. The join code holds the replication
account's password, so it is printed once, or written to a file that only root reads.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError, ValidationError
from noust.core.fs import get_fs
from noust.core.logger import Logger
from noust.managers.database.mysql import MySQLManager
from noust.managers.database.postgres import PostgresManager
from noust.managers.database.replication import ReplicationManager
from noust.managers.database.replication_mysql import MySqlReplication
from noust.managers.database.replication_token import parse as parse_code

#: The engines a replica is offered for: PostgreSQL, and MySQL or MariaDB, whichever is installed.
ENGINES = ("postgresql", "mysql")


def _replication(
    logger: Logger, engine: str = "postgresql"
) -> ReplicationManager | MySqlReplication:
    """
    Build the manager every command here uses.

    Args:
        logger: Where progress is reported.
        engine: ``postgresql``, or ``mysql`` for MySQL and MariaDB.

    Returns:
        The manager over the server's engine, resolved the way every ``noust db`` command
        resolves one.

    Raises:
        ValidationError: When the engine resolved is not the manager that was asked for.
    """
    from noust.cli.commands.db import _service

    manager = _service(logger).manager(engine)
    if engine == "mysql":
        if not isinstance(manager, MySQLManager):
            raise ValidationError(
                "Replicas of this engine are for MySQL and MariaDB", field="engine"
            )
        return MySqlReplication(manager)
    if not isinstance(manager, PostgresManager):
        raise ValidationError("Replicas of this engine are for PostgreSQL", field="engine")
    return ReplicationManager(manager)


#: The option every command that is for one engine takes.
engine_option = click.option(
    "--engine",
    "-e",
    type=click.Choice(ENGINES),
    default="postgresql",
    show_default=True,
    help="The engine: postgresql, or mysql for MySQL and MariaDB.",
)


@contextlib.contextmanager
def _quiet(ctx: Context) -> Iterator[None]:
    """
    Keep the managers' progress lines out of a ``--json`` answer.

    Args:
        ctx: The invocation's context.

    Yields:
        Nothing; standard output is a discard while the block runs, in JSON mode.
    """
    if not ctx.json_output:
        yield
        return
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _fail(logger: Logger, exc: NoustError) -> None:
    """
    Report a refused or failed operation, with its fix, and leave with status 1.

    Args:
        logger: Where to report it.
        exc: The error.
    """
    logger.error(str(exc))
    click.get_current_context().exit(1)


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def engine_name(engine: str) -> str:
    """
    Args:
        engine: The engine's key.

    Returns:
        How a message calls it.
    """
    return "PostgreSQL" if engine == "postgresql" else "MySQL or MariaDB"


def _size(count: int | None) -> str:
    """
    Args:
        count: A number of bytes, or None.

    Returns:
        It as a short human size.
    """
    if count is None:
        return "-"
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{count} B"  # pragma: no cover - the loop returns


def _print_plan(plan: dict[str, Any]) -> None:
    """
    Print what a plan would do and what stops it.

    Args:
        plan: A plan's ``to_dict``.
    """
    for step in plan.get("steps", []):
        click.echo(f"  - {step}")
    for problem in plan.get("problems", []):
        click.echo(f"  ! {problem}")
    for warning in plan.get("warnings", []):
        click.echo(f"  * {warning}")


def _deliver_token(ctx: Context, token: str, token_file: Path | None, name: str) -> None:
    """
    Hand the join code to the operator: in a file only root reads, or on standard output.

    Args:
        ctx: The invocation's context.
        token: The code (a secret).
        token_file: A file to write it to; standard output when None.
        name: The replica's name, for the instructions.
    """
    if token_file is not None:
        fs = get_fs()
        fs.write_text(token_file, token + "\n", mode=0o600)
        ctx.logger.success(f"The join code is in {token_file} (only root reads it)")
        click.echo(
            "Copy it to the replica server and run there: noust db replica join --token-file "
            "<file> --replace-data"
        )
        return
    click.echo("")
    click.echo(token)
    click.echo("")
    click.echo(
        f"That is the join code of {name}: it holds the replication password, so carry it "
        "over a channel you trust. On the replica server run:"
    )
    click.echo("  noust db replica join '<the code>' --replace-data")


@click.group("replica", cls=NoustGroup)
def replica() -> None:
    """Run read replicas of this server's PostgreSQL, over TLS, with an account of their own."""


@replica.command("status", read_only=True)
@click.option(
    "--engine",
    "-e",
    type=click.Choice(ENGINES),
    help="Only this engine's. Without it, every engine installed.",
)
@json_option("Print the status as JSON.")
@pass_context
def status(ctx: Context, engine: str | None) -> None:
    """Show the replicas prepared here, or the primary this server follows."""
    views: list[dict[str, Any]] = []
    try:
        with _quiet(ctx):
            for key in (engine,) if engine else ENGINES:
                try:
                    views.append(_replication(ctx.logger, key).status())
                except NoustError:
                    # An engine that is not installed has no replicas; asked for by name it says so.
                    if engine:
                        raise
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(views[0] if engine else views)
        return
    if all(view["role"] == "standalone" for view in views):
        click.echo("This server has no replicas and follows no primary.")
        click.echo("On the primary: noust db replica prepare. On a replica: noust db replica join.")
    for view in views:
        _print_status(ctx, view)


def _print_status(ctx: Context, view: dict[str, Any]) -> None:
    """
    Print one engine's status.

    Args:
        ctx: The invocation's context.
        view: What the manager's ``status`` gave.
    """
    for entry in view["replicas"]:
        tls = {True: "TLS " + (entry["tls_version"] or ""), False: "NO TLS", None: "-"}[
            entry["tls"]
        ]
        click.echo(
            f"{entry['name']}: {entry['state']}  from {entry['source']}  {tls.strip()}  "
            f"behind {_size(entry['lag_bytes'])}  log held {_size(entry['retained_bytes'])}"
        )
    follows = view["follows"]
    if follows:
        click.echo(f"Follows {follows['primary']} as {follows['name'] or '?'}: {follows['state']}")
        click.echo(f"  Replay delay:  {follows['replay_delay_seconds']} s")
        click.echo(f"  Not replayed:  {_size(follows['unreplayed_bytes'])}")
        click.echo(f"  sslmode:       {follows['sslmode']}")
        if follows["saved_as"]:
            click.echo(f"  Previous data: {follows['saved_as']} (delete it when you are sure)")
    for warning in view["warnings"]:
        ctx.logger.warning(warning)


@replica.command("prepare")
@engine_option
@click.argument("name")
@click.option(
    "--from",
    "source",
    required=True,
    help="Where the replica connects from: its address or network (203.0.113.7, 10.0.0.0/24).",
)
@click.option(
    "--host",
    required=True,
    help="The name the replica connects to. PostgreSQL's certificate must cover it.",
)
@click.option(
    "--listen",
    help="Make PostgreSQL listen here too (an address of this server, or '*'). Needed when it "
    "listens on the loopback only; restarts it once.",
)
@click.option(
    "--ca-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="The authority that signed PostgreSQL's certificate, when the replica does not trust "
    "it already (a private authority).",
)
@click.option("--open-firewall", is_flag=True, help="Allow the port from the replica's address.")
@click.option(
    "--token-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the join code to this file (0600) instead of printing it.",
)
@click.option("--plan", is_flag=True, help="Check everything and say what would be done.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting PostgreSQL.")
@json_option("Print the result as JSON.")
@pass_context
def prepare(
    ctx: Context,
    engine: str,
    name: str,
    source: str,
    host: str,
    listen: str | None,
    ca_file: Path | None,
    open_firewall: bool,
    token_file: Path | None,
    plan: bool,
    yes: bool,
) -> None:
    """
    Make room for a replica called NAME on this server, and print its join code.

    This creates an account that can only replicate, a slot that keeps the log the replica has
    not read, and one line in pg_hba.conf that lets that account in from --from over TLS. Before
    anything is changed the certificate PostgreSQL presents is checked the way the replica will
    check it, for the name in --host.
    """
    from noust.cli.commands.db import _confirm

    try:
        ca_pem = ca_file.read_text(encoding="utf-8") if ca_file else ""
        manager = _replication(ctx.logger, engine)
        with _quiet(ctx):
            planned = manager.plan_prepare(
                name,
                source=source,
                host=host,
                listen=listen,
                ca_pem=ca_pem,
                open_firewall=open_firewall,
            )
        if plan or planned.problems:
            if ctx.json_output:
                _echo_json(planned.to_dict())
            else:
                click.echo(f"Replica {planned.name}:")
                _print_plan(planned.to_dict())
            if planned.problems:
                click.get_current_context().exit(1)
            return
        if planned.restart and not _confirm(f"Restart {engine_name(engine)} now?", force=yes):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = manager.prepare(
                name,
                source=source,
                host=host,
                listen=listen,
                ca_pem=ca_pem,
                open_firewall=open_firewall,
                confirmed=True,
            )
    except OSError as exc:
        ctx.logger.error(f"Could not read {ca_file}: {exc}")
        click.get_current_context().exit(1)
        return
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"Replica {name} prepared: account {planned.account}, slot {planned.slot}")
    _deliver_token(ctx, result["token"], token_file, name)


@replica.command("token")
@engine_option
@click.argument("name")
@click.option(
    "--token-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the join code to this file (0600) instead of printing it.",
)
@pass_context
def token(ctx: Context, engine: str, name: str, token_file: Path | None) -> None:
    """Print the join code of a replica prepared here again."""
    try:
        code = _replication(ctx.logger, engine).token(name)
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    _deliver_token(ctx, code, token_file, name)


@replica.command("join")
@click.argument("code", required=False)
@click.option(
    "--token-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read the join code from this file.",
)
@click.option(
    "--replace-data",
    is_flag=True,
    help="Accept that this server's PostgreSQL data is replaced by the primary's.",
)
@click.option(
    "--discard-previous",
    is_flag=True,
    help="Delete this server's old data directory once the replica streams (it is kept otherwise).",
)
@click.option("--plan", is_flag=True, help="Check everything and say what would be done.")
@json_option("Print the result as JSON.")
@pass_context
def join(
    ctx: Context,
    code: str | None,
    token_file: Path | None,
    replace_data: bool,
    discard_previous: bool,
    plan: bool,
) -> None:
    """
    Make this server a replica of the primary CODE names.

    The primary's cluster is copied beside this server's own, which keeps running; then
    PostgreSQL stops once, the data directory is set aside and the copy takes its place. If the
    replica does not start streaming, the previous data is put back.
    """
    from noust.cli.commands.db import _confirm

    try:
        text = token_file.read_text(encoding="utf-8").strip() if token_file else (code or "")
        if not text:
            raise ValidationError(
                "Give the join code", details="As an argument, or --token-file.", field="code"
            )
        manager = _replication(ctx.logger, parse_code(text).engine)
        with _quiet(ctx):
            planned = manager.plan_join(text)
        view = planned.to_dict()
        if plan or planned.problems:
            if ctx.json_output:
                _echo_json(view)
            else:
                click.echo(f"Following {view['primary']} as {view['name']}:")
                click.echo(f"  Data directory: {view['data_directory']} -> {view['saved_as']}")
                click.echo(
                    f"  Space:          {_size(view['free_bytes'])} free, "
                    f"{_size(view['needed_bytes'])} needed"
                )
                _print_plan(view)
            if planned.problems:
                click.get_current_context().exit(1)
            return
        if not _confirm(
            f"Replace the PostgreSQL data of this server with a copy of {view['primary']}?",
            force=replace_data,
        ):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = manager.join(text, confirmed=True, discard_previous=discard_previous)
    except OSError as exc:
        ctx.logger.error(f"Could not read the join code: {exc}")
        click.get_current_context().exit(1)
        return
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"This server follows {result['primary']} as {result['name']}")
    if result["saved_as"]:
        click.echo(f"Its previous data is at {result['saved_as']}; delete it when you are sure.")


@replica.command("promote")
@engine_option
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@json_option("Print the result as JSON.")
@pass_context
def promote(ctx: Context, engine: str, yes: bool) -> None:
    """Stop following the primary: this server becomes a primary and accepts writes."""
    from noust.cli.commands.db import _confirm

    try:
        if not _confirm("Promote this replica? It stops following its primary.", force=yes):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = _replication(ctx.logger, engine).promote(confirmed=True)
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success("This server accepts writes now")
    ctx.logger.warning(result["note"])


@replica.command("remove")
@engine_option
@click.argument("name")
@click.option(
    "--restore-listen",
    is_flag=True,
    help="Put the address the engine listens on back as it was before the replica was prepared "
    "(restarts the engine; for MySQL and MariaDB it takes Noust's settings file off).",
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@json_option("Print the result as JSON.")
@pass_context
def remove(ctx: Context, engine: str, name: str, restore_listen: bool, yes: bool) -> None:
    """Take a replica away: its account, its slot and its pg_hba.conf line go."""
    from noust.cli.commands.db import _confirm

    try:
        if not _confirm(f"Remove the replica {name}? It stops streaming for good.", force=yes):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = _replication(ctx.logger, engine).remove(
                name, confirmed=True, restore_listen=restore_listen
            )
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    if not result["removed"]:
        click.echo(result.get("note", "Nothing was removed."))
        return
    ctx.logger.success(f"Replica {name} removed")
    for step in result["steps"]:
        click.echo(f"  - {step}")


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(replica)
