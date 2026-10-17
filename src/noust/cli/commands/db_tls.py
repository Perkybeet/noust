# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db tls``: a host engine that serves a certificate clients can verify (checklist C-27).

The client of :class:`~noust.managers.database.host_tls.HostTls`: the same checks, the same
proof that the engine presents the certificate it was given, in the same words. This module
parses arguments, asks before restarting an engine, and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import contextlib
import io
import json
from collections.abc import Iterator
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.managers.database.host_tls import HostTls


def _tls(logger: Logger, engine: str) -> HostTls:
    """
    Build the manager every command here uses.

    Args:
        logger: Where progress is reported.
        engine: The engine's name.

    Returns:
        The manager over the server's engine.
    """
    from noust.cli.commands.db import _service

    return HostTls(_service(logger).manager(engine))


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


def _engines() -> tuple[str, ...]:
    """
    Returns:
        The engines this can serve.
    """
    from noust.managers.database.host_tls import ENGINES

    return tuple(ENGINES)


@click.group("tls", cls=NoustGroup)
def tls() -> None:
    """Give a host engine a certificate clients can verify, and keep it current."""


@tls.command("status", read_only=True)
@json_option("Print the status as JSON.")
@pass_context
def status(ctx: Context) -> None:
    """Show which engines serve a certificate of Noust's, and whether it is current."""
    views = []
    try:
        with _quiet(ctx):
            for engine in _engines():
                try:
                    views.append(_tls(ctx.logger, engine).status())
                except NoustError as exc:
                    views.append({"engine": engine, "enabled": False, "warnings": [str(exc)]})
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(views)
        return
    if not any(view["enabled"] for view in views):
        click.echo("No engine serves a certificate of Noust's. Give one with: noust db tls enable")
        return
    for view in views:
        if not view["enabled"]:
            continue
        record = view["record"]
        click.echo(f"{view['engine']}: {record['hostname']} ({record['source']})")
        click.echo(f"  Fingerprint:   {record['fingerprint']}")
        click.echo(f"  Expires:       {record['not_after']} ({view.get('days_left', '?')} days)")
        click.echo(f"  Presented now: {view.get('served') or 'nothing'}")
        for warning in view["warnings"]:
            ctx.logger.warning(warning)


@tls.command("enable")
@click.option("--engine", "-e", type=click.Choice(_engines()), required=True, help="The engine.")
@click.option("--host", "hostname", required=True, help="The name clients reach the engine by.")
@click.option(
    "--source",
    type=click.Choice(["acme", "lineage", "custom"]),
    default="acme",
    show_default=True,
    help="acme: the Let's Encrypt lineage named after the host. lineage: another lineage. "
    "custom: a certificate you uploaded (noust cert upload).",
)
@click.option("--certificate", help="The lineage's or the upload's name, for lineage and custom.")
@click.option("--plan", is_flag=True, help="Check the certificate and say what would be done.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting the engine.")
@json_option("Print the result as JSON.")
@pass_context
def enable(
    ctx: Context,
    engine: str,
    hostname: str,
    source: str,
    certificate: str | None,
    plan: bool,
    yes: bool,
) -> None:
    """
    Give the engine the certificate of HOST, and prove it presents it.

    The certificate and its key are checked (the key is its own, it covers the name, it has not
    expired), copied for the engine's account, and the engine is told to use the copy. PostgreSQL
    reloads; MySQL and MariaDB restart once. Then the engine is asked what it presents.
    """
    from noust.cli.commands.db import _confirm

    try:
        with _quiet(ctx):
            manager = _tls(ctx.logger, engine)
            planned = manager.plan(hostname, source=source, certificate=certificate)
        if plan:
            if ctx.json_output:
                _echo_json(planned)
            else:
                click.echo(f"Certificate:  {planned['fullchain']}")
                click.echo(f"Fingerprint:  {planned['fingerprint']}")
                click.echo(f"Expires:      {planned['not_after']}")
                click.echo(f"Copy:         {planned['directory']}")
                click.echo(f"Restart:      {'yes, once' if planned['restart'] else 'no'}")
            return
        if planned["restart"] and not _confirm(f"Restart {engine} now?", force=yes):
            click.echo("Nothing was changed.")
            return
        with _quiet(ctx):
            result = manager.enable(
                hostname, confirmed=True, source=source, certificate=certificate
            )
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(result)
        return
    ctx.logger.success(f"{engine} presents the certificate of {hostname}")
    if engine == "postgresql":
        how = f"sslmode=verify-full host={hostname}"
    elif getattr(manager.manager, "is_mariadb", False):
        how = f"--ssl --ssl-verify-server-cert -h {hostname}"
    else:
        how = f"--ssl-mode=VERIFY_IDENTITY -h {hostname}"
    click.echo(f"Clients verify it with: {how}")


@tls.command("refresh")
@json_option("Print what was done as JSON.")
@pass_context
def refresh(ctx: Context) -> None:
    """
    Bring each engine's copy up to its certificate, load it, and prove what is presented.

    certbot runs this after every renewal that succeeds; run it by hand after uploading a new
    certificate.
    """
    report: dict[str, Any] = {}
    try:
        with _quiet(ctx):
            for engine in _engines():
                report.update(_tls(ctx.logger, engine).refresh())
    except NoustError as exc:
        _fail(ctx.logger, exc)
        return
    if ctx.json_output:
        _echo_json(report)
        return
    if not report:
        click.echo("No engine serves a certificate of Noust's.")
    for engine, outcome in report.items():
        if "skipped" in outcome:
            ctx.logger.warning(f"{engine}: skipped, {outcome['skipped']}")
        elif outcome["refreshed"]:
            ctx.logger.success(f"{engine}: now presents the renewed certificate")
        else:
            ctx.logger.info(f"{engine}: the certificate it presents is current")


@tls.command("disable")
@click.option("--engine", "-e", type=click.Choice(_engines()), required=True, help="The engine.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting the engine.")
@pass_context
def disable(ctx: Context, engine: str, yes: bool) -> None:
    """Take the certificate off the engine: its settings go back as they were."""
    from noust.cli.commands.db import _confirm

    try:
        manager = _tls(ctx.logger, engine)
        if manager.needs_restart() and not _confirm(f"Restart {engine} now?", force=yes):
            click.echo("Nothing was changed.")
            return
        result = manager.disable(confirmed=True)
    except (NoustError, ValidationError) as exc:
        _fail(ctx.logger, exc)
        return
    if result["disabled"]:
        ctx.logger.success(f"{engine} no longer serves a certificate of Noust's")
    else:
        click.echo(result.get("note", "Nothing was changed."))


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(tls)
