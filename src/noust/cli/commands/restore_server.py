# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust restore-server``: from a bundle of Noust's own state to answering applications.

The CLI of :class:`~noust.managers.restore_server.RestoreServer`: on a new
machine with Noust installed, one command puts the state back, says what the
machine still lacks (and with ``--install`` installs the web server, Docker and the
database engines), creates the PostgreSQL roles, brings back the databases no
application owns, brings each application back from its newest backup, renders its
site, starts it behind the health gate and prints a final report. It can be run
again with ``--skip-state`` for the applications that did not come back.

The bundle is a path or a name on this server, or fetched first from a
destination (``--from-destination``) when the old server's disk is gone with its
bundles. The passphrase is never an argument (see ``noust self-backup``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.cli.commands.self_backup import read_passphrase
from noust.core.exceptions import NoustError
from noust.managers.restore_server import RestoreServer, ServerRestoreReport
from noust.managers.self_backup import SelfBackupManager

#: What each outcome is called in the report.
_LABELS = {
    "restored": "back",
    "no_backup": "NO BACKUP",
    "failed": "FAILED",
    "skipped": "left out",
}


def _fail(ctx: Context, exc: NoustError) -> None:
    """
    Report a refused or failed operation, with its fix, and leave with status 1.

    Args:
        ctx: The invocation's context.
        exc: The error.
    """
    ctx.logger.error(str(exc))
    click.get_current_context().exit(1)


def _print_requirement(ctx: Context, requirement: dict[str, Any]) -> None:
    """
    Print what was found or done about one piece of software.

    Args:
        ctx: The invocation's context.
        requirement: One entry of the report's ``requirements``.
    """
    name, state = requirement["name"], requirement["state"]
    if state == "installed":
        ctx.logger.success(f"{name} was installed")
    elif state == "failed":
        ctx.logger.warning(f"{name} could not be installed: {requirement['detail']}")
    elif state == "missing":
        how = f" Run: {requirement['command']}" if requirement["command"] else ""
        ctx.logger.warning(f"{name} is not installed here.{how} (or run again with --install)")


def _print_roles(ctx: Context, roles: dict[str, Any]) -> None:
    """
    Print what putting a PostgreSQL server's roles back did.

    Args:
        ctx: The invocation's context.
        roles: One entry of the report's ``roles``.
    """
    engine, state = roles["engine"], roles["state"]
    if state == "created":
        made = ", ".join(roles["created"]) or "none were missing"
        ctx.logger.success(f"The roles of {engine}: created {made}")
        if roles["detail"]:
            ctx.logger.info(f"  {roles['detail']}")
    else:
        ctx.logger.warning(f"The roles of {engine} are {state}: {roles['detail']}")


def _print_database(database: Any) -> None:
    """
    Print one database no application owns, and what became of it.

    Args:
        database: A :class:`~noust.managers.restore_databases.DatabaseOutcome`.
    """
    label = {
        "restored": "back",
        "present": "already here",
        "no_dump": "NO DUMP",
        "skipped": "left for you",
        "failed": "FAILED",
    }.get(database.status, database.status)
    source = f" from {database.source}: {database.dump}" if database.dump else ""
    click.echo(f"  {database.engine + '/' + database.name:<40} {label}{source}")
    for note in database.notes:
        click.echo(f"    {note}")
    if database.error:
        click.echo(f"    {database.error}")


def _print_report(ctx: Context, report: ServerRestoreReport) -> None:
    """
    Print the final report, one line per application and what to do next.

    Args:
        ctx: The invocation's context.
        report: What the flow did.
    """
    if report.state is not None:
        ctx.logger.success(
            f"Noust's state is back: {report.state.files} files, {report.state.links} links"
        )
        if report.state.safety_copy:
            ctx.logger.info(f"  What was here is kept: {report.state.safety_copy}")
    for requirement in report.requirements:
        _print_requirement(ctx, requirement)
    for engine in report.engines_missing:
        ctx.logger.warning(f"{engine['engine']} is not installed here. Run: {engine['command']}")
    for roles in report.roles:
        _print_roles(ctx, roles)
    if report.databases:
        click.echo("\nDatabases no application owns")
        for database in report.databases:
            _print_database(database)
    click.echo("\nApplications")
    for outcome in report.apps:
        label = _LABELS.get(outcome.status, outcome.status)
        gate = f", health gate {outcome.gate}" if outcome.gate else ""
        source = f" from {outcome.source}: {outcome.backup_id}" if outcome.backup_id else ""
        click.echo(f"  {outcome.domain:<40} {label}{source}{gate}")
        if outcome.volumes:
            click.echo(f"    data restored on top: {', '.join(outcome.volumes)}")
        for note in outcome.notes:
            click.echo(f"    {note}")
        if outcome.error:
            click.echo(f"    {outcome.error}")
        if outcome.evidence:
            click.echo("\n" + "\n".join(f"      {line}" for line in outcome.evidence.splitlines()))
    counts = report.counts()
    click.echo(
        "\n"
        + ", ".join(
            f"{count} {_LABELS.get(status, status)}" for status, count in sorted(counts.items())
        )
    )
    for note in report.notes:
        ctx.logger.info(note)
    if report.ok:
        ctx.logger.success("The server is back")
    else:
        ctx.logger.warning(
            "Some applications are not back. Fix what the report says and run the same command "
            "with --skip-state to try them again."
        )


@click.command(
    "restore-server", cls=NoustCommand, read_only=lambda params: bool(params.get("plan"))
)
@click.option("--from", "bundle", help="The bundle: a path, or its name on this server.")
@click.option(
    "--from-destination",
    "destination",
    help="Fetch the bundle from this backup destination first (with --name).",
)
@click.option("--name", help="With --from-destination: the bundle's name there.")
@click.option("--host", help="With --from-destination: the old server's folder there.")
@click.option("--app", "only", multiple=True, help="Restore only this application. Repeatable.")
@click.option("--skip", "skipped", multiple=True, help="Leave this application out. Repeatable.")
@click.option(
    "--skip-state",
    is_flag=True,
    help="Noust's state is already back (a run that stopped): only bring the applications up.",
)
@click.option("--force", is_flag=True, help="Restore over a server that already holds state.")
@click.option(
    "--install",
    is_flag=True,
    help="Install what this machine lacks: the web server, Docker (when an application or an "
    "instance needs it) and the database engines. Without it nothing is installed.",
)
@click.option(
    "--no-databases",
    is_flag=True,
    help="Do not bring back the databases no application owns.",
)
@click.option(
    "--accept-schema-changes",
    is_flag=True,
    help="Restore an application's files even past a schema change nothing restores the "
    "database for.",
)
@click.option(
    "--plan", is_flag=True, help="Say what the bundle would bring back, and stop. Changes nothing."
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@click.option(
    "--passphrase-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Read the bundle's passphrase from this file (mode 600).",
)
@json_option("Print the report as JSON.")
@pass_context
def cli(
    ctx: Context,
    bundle: str | None,
    destination: str | None,
    name: str | None,
    host: str | None,
    only: tuple[str, ...],
    skipped: tuple[str, ...],
    skip_state: bool,
    force: bool,
    install: bool,
    no_databases: bool,
    accept_schema_changes: bool,
    plan: bool,
    yes: bool,
    passphrase_file: Path | None,
) -> None:
    """
    Bring a whole server back from a bundle of Noust's own state.

    On a new machine with Noust installed: puts the store, the secrets, the
    keys, the certificates, the units and the web server's configuration back;
    names (and with --install installs) the web server, Docker and the database
    engines the machine still lacks; creates the PostgreSQL roles and brings back
    the databases no application owns; restores each application from its newest backup
    (here or on the destinations it sends to), with the data backups newer than
    it; renders its site; starts it through the health gate; and prints what
    came back and what did not. Nothing is rolled back: there is nothing to go
    back to on a machine that was empty.
    """
    if bool(bundle) == bool(destination):
        ctx.logger.error("Give the bundle with --from, or fetch it with --from-destination")
        click.get_current_context().exit(1)
    flow = RestoreServer(logger=ctx.logger)
    try:
        if destination:
            if not name:
                ctx.logger.error("--from-destination needs --name: see 'noust self-backup remote'")
                click.get_current_context().exit(1)
            manager = SelfBackupManager(logger=ctx.logger)
            chosen_host = host or manager.host_slug()
            ctx.logger.info(f"Fetching {name} from {destination}")
            bundle = str(manager.download(destination, str(name), chosen_host, manager.directory))
        passphrase = read_passphrase(confirm=False, file=passphrase_file)
        if plan:
            answer = flow.plan(str(bundle), passphrase)
            if ctx.json_output:
                click.echo(json.dumps(answer, indent=2, sort_keys=True, default=str))
            else:
                _print_plan(ctx, answer)
            return
        if not yes and not click.confirm(
            "Restore this server from the bundle? Noust's services stop while its state is put "
            "back, then each application is restored and started"
        ):
            ctx.logger.info("Cancelled")
            return
        report = flow.run(
            str(bundle),
            passphrase,
            force=force,
            skip_state=skip_state,
            only=only,
            skip=skipped,
            accept_schema_changes=accept_schema_changes,
            install=install,
            databases=not no_databases,
            from_destination=destination,
        )
    except NoustError as exc:
        _fail(ctx, exc)
        return
    if ctx.json_output:
        click.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True, default=str))
    else:
        _print_report(ctx, report)
    if not report.ok:
        click.get_current_context().exit(1)


def _print_plan(ctx: Context, answer: dict[str, Any]) -> None:
    """
    Print what a bundle would bring back.

    Args:
        ctx: The invocation's context.
        answer: What :meth:`~noust.managers.restore_server.RestoreServer.plan` says.
    """
    ctx.logger.info(
        f"{answer['bundle']}: taken {answer['created_at']} on {answer['host']} by Noust "
        f"{answer['noust_version']}"
    )
    click.echo("\nApplications it would bring back")
    for app in answer["apps"]:
        sends = ", ".join(app["destinations"]) or "no destination"
        data = ", data backups" if app["data_policy"] else ""
        click.echo(f"  {app['domain']:<40} {app['type']}, backed up to {sends}{data}")
    if not answer["apps"]:
        click.echo("  none")
    if answer["engines"]:
        click.echo(f"\nDatabase engines they use: {', '.join(answer['engines'])}")
    if answer.get("databases"):
        click.echo("\nDatabases no application owns it would bring back")
        for name in answer["databases"]:
            click.echo(f"  {name}")
    if answer.get("pg_globals"):
        click.echo(
            f"\nThe roles of {', '.join(answer['pg_globals'])} are in the bundle and would be created"
        )
