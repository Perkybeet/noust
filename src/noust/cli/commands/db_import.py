# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db import``: bring a dump from another provider into a database.

The second client of :class:`~noust.managers.database.imports.DatabaseImports`
after ``/api/databases/imports``: the same detection of what the file is, the
same options, the same safety copy that is put back when the load fails. The
CLI is root on the machine, so it may name any file; the API may not.

``noust db`` loads this through :func:`register`, from
:mod:`noust.cli.commands.db_backups`, which keeps the order the modules are
imported in from mattering.
"""

from __future__ import annotations

from pathlib import Path

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.cli.commands.db_backups import (
    ENGINE,
    _backups,
    _confirm,
    _echo_json,
    _exit,
    _fail,
    _quiet,
)
from noust.core.exceptions import NoustError
from noust.managers.database.backups import SUPERUSER_WARNING
from noust.managers.database.cluster_import import ClusterImporter, ClusterPlan
from noust.managers.database.imports import DatabaseImports, resolve_server_path


@click.command(
    "import",
    cls=NoustCommand,
    read_only=lambda params: bool(params.get("inspect_only")),
)
@click.argument("database")
@click.argument("file", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--replace",
    is_flag=True,
    help="Drop the database and load the dump into a fresh one. A safety copy is taken first "
    "and put back if the import fails.",
)
@click.option(
    "--keep-owners",
    is_flag=True,
    help="PostgreSQL: keep the owners and privileges the dump names. By default they are left "
    "out, because they are the roles of the server it came from, and everything belongs to "
    "the database's owner.",
)
@click.option(
    "--no-safety-copy",
    is_flag=True,
    help="Do not dump the database first when nothing is dropped.",
)
@click.option(
    "--owner",
    default=None,
    help="PostgreSQL: the account a database this import makes belongs to (it must exist), "
    "instead of the default. A database that is loaded over keeps its owner.",
)
@click.option(
    "--as-superuser",
    is_flag=True,
    help="PostgreSQL: load the dump as the server's superuser. By default it is loaded as the "
    "database's owner, so what a dump asks beyond that (COPY ... FROM PROGRAM, extensions that "
    "need a superuser) is refused. Use it only for a dump you made or have read. Audited.",
)
@click.option(
    "--inspect",
    "inspect_only",
    is_flag=True,
    help="Say what the file is and whether the engine can load it, and stop.",
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print what the import did as JSON.")
@pass_context
def import_dump(
    ctx: Context,
    database: str,
    file: Path,
    engine: str,
    replace: bool,
    keep_owners: bool,
    no_safety_copy: bool,
    owner: str | None,
    as_superuser: bool,
    inspect_only: bool,
    force: bool,
) -> None:
    """
    Import a dump into DATABASE, creating it when it does not exist.

    FILE is a dump on this server: SQL, a pg_dump archive, a mysqldump or a
    mongodump archive, plain or compressed with gzip, bzip2, xz or zip. What
    it is is read from its bytes, not its name, and nothing is touched when it
    is not a dump the engine can load. To bring a file from your computer, use
    the console's import, which uploads it in chunks.

    The database is dumped first and put back if the load fails; a database
    the import created is dropped again when it fails.
    """
    try:
        source = resolve_server_path(str(file.expanduser().absolute()), restrict=False)
        backups = _backups(ctx.logger)
        importer = DatabaseImports(backups.service, backups=backups)
        if inspect_only:
            with _quiet(ctx):
                answer = importer.inspect(source, engine=engine)
            if ctx.json_output:
                _echo_json(answer)
            else:
                ctx.logger.info(f"{source.name}: {answer['format']['description']}")
                ctx.logger.info(f"  Size: {answer['size_human']}")
                if answer.get("loadable") is False:
                    ctx.logger.warning(answer["reason"])
                elif answer.get("loadable"):
                    ctx.logger.success(f"{engine} can load it")
            _exit(0)
            return
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return

    if replace:
        question = (
            f"Drop database '{database}' and load {source.name} into a new one? "
            "A safety copy is taken first and put back if the import fails"
        )
    else:
        question = (
            f"Import {source.name} into '{database}'? Rows it already holds may be "
            "overwritten" + ("" if no_safety_copy else "; a safety copy is taken first")
        )
    if not _confirm(question, force=force):
        ctx.logger.info("Cancelled")
        _exit(0)
        return
    if as_superuser and not _confirm(f"{SUPERUSER_WARNING} Load it as the superuser?", force=force):
        ctx.logger.info("Cancelled")
        _exit(0)
        return
    try:
        with _quiet(ctx):
            outcome = importer.run(
                engine,
                database,
                source,
                replace=replace,
                keep_owners=keep_owners,
                safety_backup=not no_safety_copy,
                on_step=lambda text: ctx.logger.info(text),
                as_superuser=as_superuser,
                confirm_superuser=as_superuser,
                create_owner=owner,
            )
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if ctx.json_output:
        _echo_json(outcome.to_dict())
    else:
        verb = "created" if outcome.created else "replaced" if outcome.replaced else "loaded"
        ctx.logger.success(f"Database {outcome.database} {verb} from {source.name}")
        ctx.logger.info(f"  It was {outcome.format.description}")
        if outcome.safety_copy:
            ctx.logger.info(f"  Safety copy: {outcome.safety_copy}")
    _exit(0)


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db_backups` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(import_dump)
    group.add_command(import_all)


def _show_plan(ctx: Context, plan: ClusterPlan) -> None:
    """
    Print what an import of all databases would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    ctx.logger.info(f"{plan.format.description}")
    if plan.roles:
        made = plan.to_create
        ctx.logger.info(f"Accounts: {len(made)} to create, {len(plan.roles) - len(made)} left out")
        for role in plan.roles:
            note = f" ({role.reason})" if role.reason else ""
            cut = f", without {', '.join(role.dropped)}" if role.dropped else ""
            ctx.logger.info(f"  {role.action:<7} {role.name}{cut}{note}")
    ctx.logger.info("Databases:")
    for database in plan.databases:
        owner = f", owned by {database.owner}" if database.owner else ""
        note = f" ({database.reason})" if database.reason else ""
        ctx.logger.info(f"  {database.action:<7} {database.name}{owner}{note}")
    for line in plan.ignored:
        ctx.logger.info(f"  not carried: {line}")


@click.command(
    "import-all",
    cls=NoustCommand,
    read_only=lambda params: bool(params.get("plan_only")),
)
@click.argument("file", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to import into.")
@click.option(
    "--users",
    is_flag=True,
    help="Also create the accounts of the dump (PostgreSQL: its roles, with the password hash it "
    "holds). Without superuser, role-creating, replication or row-security-bypassing power, and "
    "never over an account that exists. The databases then keep the owners and privileges the "
    "dump names for those accounts.",
)
@click.option(
    "--accounts-file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="MySQL and MariaDB: a file of CREATE USER and GRANT statements, one per line (SHOW CREATE "
    "USER and SHOW GRANTS print them, so does pt-show-grants); mysqldump does not write the "
    "accounts. Used with --users.",
)
@click.option(
    "--database",
    "-d",
    "databases",
    multiple=True,
    help="Load only this database (repeat for several). The server's own ones (postgres) are "
    "loaded only when named.",
)
@click.option(
    "--replace",
    is_flag=True,
    help="Drop each database that exists and load the dump into a fresh one. A safety copy is "
    "taken first and put back if its load fails.",
)
@click.option(
    "--no-safety-copy",
    is_flag=True,
    help="Do not dump a database first when it is loaded over rather than replaced.",
)
@click.option(
    "--as-superuser",
    is_flag=True,
    help="Load every database as the server's superuser instead of as its owner. By default a "
    "database is loaded as its owner, so what a dump asks beyond that (COPY ... FROM PROGRAM, "
    "extensions that need a superuser) is refused. Use it only for a file you made or have read. "
    "Audited.",
)
@click.option(
    "--plan",
    "plan_only",
    is_flag=True,
    help="Say what would be created, loaded over, replaced or left out, and stop.",
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the plan or what was done as JSON.")
@pass_context
def import_all(
    ctx: Context,
    file: Path,
    engine: str,
    users: bool,
    accounts_file: Path | None,
    databases: tuple[str, ...],
    replace: bool,
    no_safety_copy: bool,
    as_superuser: bool,
    plan_only: bool,
    force: bool,
) -> None:
    """
    Import a dump of every database of an engine, and its accounts when asked.

    FILE is what pg_dumpall writes (plain or compressed): the roles, then each
    database. Each database is loaded like one imported with 'noust db import',
    as its owner, with its own safety copy; one that fails does not stop the
    others, and a database this created is dropped again when it fails.
    """
    try:
        source = resolve_server_path(str(file.expanduser().absolute()), restrict=False)
        backups = _backups(ctx.logger)
        importer = ClusterImporter(DatabaseImports(backups.service, backups=backups))
        accounts = (
            resolve_server_path(str(accounts_file.expanduser().absolute()), restrict=False)
            if accounts_file is not None
            else None
        )
        with _quiet(ctx):
            plan = importer.plan(
                engine,
                source,
                restore_users=users,
                only=databases,
                replace=replace,
                accounts=accounts,
            )
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if plan_only:
        if ctx.json_output:
            _echo_json(plan.to_dict())
        else:
            _show_plan(ctx, plan)
        _exit(0)
        return
    if not ctx.json_output:
        _show_plan(ctx, plan)
    loading = [d for d in plan.databases if d.action != "skip"]
    if not _confirm(
        f"Import {len(loading)} database(s) from {source.name}"
        + ("" if no_safety_copy else "? Those that exist are dumped first"),
        force=force,
    ):
        ctx.logger.info("Cancelled")
        _exit(0)
        return
    if as_superuser and not _confirm(
        f"{SUPERUSER_WARNING} Load every database as the superuser?", force=force
    ):
        ctx.logger.info("Cancelled")
        _exit(0)
        return
    accepted = False
    if users and plan.warnings:
        for warning in plan.warnings:
            ctx.logger.warning(warning)
        accepted = _confirm("Create these accounts?", force=force)
        if not accepted:
            ctx.logger.info("Cancelled")
            _exit(0)
            return
    try:
        with _quiet(ctx):
            outcome = importer.run(
                engine,
                source,
                restore_users=users,
                confirm_users=accepted or (users and not plan.warnings),
                only=databases,
                replace=replace,
                safety_backup=not no_safety_copy,
                on_step=lambda text: ctx.logger.info(text),
                as_superuser=as_superuser,
                confirm_superuser=as_superuser,
                accounts=accounts,
            )
    except NoustError as exc:
        _exit(_fail(ctx.logger, exc))
        return
    if ctx.json_output:
        _echo_json(outcome.to_dict())
    else:
        if outcome.accounts_created:
            ctx.logger.success(f"Accounts created: {', '.join(outcome.accounts_created)}")
        for result in outcome.databases:
            if result.status == "skipped":
                continue
            if result.status == "failed":
                ctx.logger.error(f"{result.name} was not imported:\n{result.detail}")
            else:
                ctx.logger.success(f"Database {result.name} {result.status}")
                if result.safety_copy:
                    ctx.logger.info(f"  Safety copy: {result.safety_copy}")
    _exit(1 if outcome.failed else 0)
