# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust db`` command group.

Engines, databases, users and access profiles, dumps and restores, links to
applications, the query console. Every command is a thin shell around
:class:`~noust.managers.database.service.DatabaseService`, the same service
the console's API calls: this module parses, confirms and prints, and never
decides anything itself. That is what keeps ``noust db drop`` and the
console's drop from leaving the store in two different states, which is how
an application's backups used to break.

Three things are deliberate here:

- **The work lives in module-level functions, not in the Click callbacks.**
  ``handle_db`` still routes the argparse tree that is being retired, and both
  paths call the same functions, so the two front ends cannot drift apart
  before the old one is deleted.
- **Nothing spawns a process.** The one exception is :func:`_open_client`,
  which hands the terminal to ``psql`` or ``mysql`` and is documented where it
  is defined.
- **Read-only means one statement.** ``noust db query`` defaults to the
  engine's read-only session, and
  :func:`~noust.managers.database.service.console_request` - the guard the
  API applies too - is what stops a request from carrying a second statement
  that closes it: ``SELECT 1; COMMIT; DROP TABLE users``.
"""

from __future__ import annotations

import json
import os
from argparse import Namespace
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.panel_links import open_in_panel
from noust.core.exceptions import (
    ConfirmationRequired,
    DatabaseError,
    DatabaseQueryError,
    NoustError,
    ValidationError,
)
from noust.core.logger import Logger
from noust.managers.database import (
    PROFILES,
    BaseDatabaseManager,
    DatabaseRegistry,
    get_db_manager,
)
from noust.managers.database.backups import DatabaseBackups
from noust.managers.database.instances import is_instance_key, parse_instance_key
from noust.managers.database.service import (
    MAX_QUERY_LENGTH,
    DatabaseService,
    console_request,
)

__all__ = ["MAX_QUERY_LENGTH", "PASSWORD_PLACEHOLDER", "cli", "handle_db"]

#: Placeholder printed in a connection string when the operator gave no
#: password. It is a blank to fill in, not a credential.
PASSWORD_PLACEHOLDER = "<PASSWORD>"  # noqa: S105

#: Stands for "ask for it" when an option that takes a secret is given alone.
_ASK = "\x00ask"


class EngineParamType(click.ParamType):
    """
    A database engine name, checked against the registry as it is parsed.

    Resolution is the registry's, so every spelling it accepts keeps working
    (``pg`` and ``postgres`` for PostgreSQL, ``mariadb`` for MySQL, ``valkey``
    for Redis). Rejecting an unknown engine here rather than three calls later
    means a typo costs a usage error instead of a half-finished operation.
    """

    name = "engine"

    def convert(
        self,
        value: Any,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> str:
        """
        Check that the registry knows the engine.

        Args:
            value: The name the operator typed.
            param: The parameter being converted.
            ctx: The Click context.

        An instance key (``postgresql@project.service``) is checked for its
        form and its engine here; whether a container answers to it is the
        service's to say, with the containers it found.

        Returns:
            The name, unchanged, for the manager to resolve again.
        """
        if is_instance_key(str(value)):
            try:
                parts = parse_instance_key(str(value))
            except ValidationError as exc:
                self.fail(exc.message, param, ctx)
            if DatabaseRegistry.canonical(parts.engine) is None:
                self.fail(
                    f"unknown database engine {parts.engine!r} in {value!r}. "
                    f"Available: {', '.join(sorted(DatabaseRegistry.list_engines()))}",
                    param,
                    ctx,
                )
            return str(value)
        if get_db_manager(str(value)) is None:
            self.fail(
                f"unknown database engine {value!r}. "
                f"Available: {', '.join(sorted(DatabaseRegistry.list_engines()))}",
                param,
                ctx,
            )
        return str(value)


#: The type every ``--engine`` option and every engine argument uses.
ENGINE = EngineParamType()

#: The type every ``--profile`` option uses.
PROFILE = click.Choice(list(PROFILES))


def _exit(code: int) -> NoReturn:
    """
    Leave the command with an exit status.

    Args:
        code: Process exit code.

    Raises:
        click.exceptions.Exit: Always. Click turns it into the exit status.
    """
    click.get_current_context().exit(code)


def _service(logger: Logger) -> DatabaseService:
    """
    Build the service a command uses.

    Engines are resolved through this module's :func:`get_db_manager`, looked
    up at call time, so every command resolves them the one way.

    Args:
        logger: Where progress is reported.

    Returns:
        The service.
    """
    return DatabaseService(
        logger=logger,
        resolve=lambda engine: get_db_manager(engine, verbose=logger.verbose),
    )


def _get_manager(engine: str | None, logger: Logger) -> BaseDatabaseManager | None:
    """
    Resolve an engine name to its manager.

    Args:
        engine: Engine name or alias.
        logger: Logger for the error message.

    Returns:
        The manager, or None when the name is missing or unknown.
    """
    if not engine:
        logger.error("Database engine is required")
        logger.info("Available engines: " + ", ".join(DatabaseRegistry.list_engines()))
        return None

    if is_instance_key(engine):
        # A container is found by the service, which asks Docker.
        try:
            return _service(logger).manager(engine)
        except DatabaseError as exc:
            _fail(logger, exc)
            return None

    manager = get_db_manager(engine, verbose=logger.verbose)
    if not manager:
        logger.error(f"Unknown database engine: {engine}")
        logger.info("Available engines: " + ", ".join(DatabaseRegistry.list_engines()))
        return None

    return manager


def _fail(logger: Logger, exc: NoustError) -> int:
    """
    Report a refused or failed operation, with its fix.

    Args:
        logger: Where to report it.
        exc: The error.

    Returns:
        The exit code, 1.
    """
    logger.error(str(exc))
    return 1


def _confirm(question: str, *, force: bool) -> bool:
    """
    Ask before doing something that cannot be undone.

    Args:
        question: The question, naming the exact resource and consequence.
        force: Skip the question because the operator already said so.

    Returns:
        True when the operation may proceed.
    """
    if force:
        return True
    try:
        return click.confirm(question, default=False)
    except click.Abort:
        # A closed stdin or a Ctrl-C is a refusal, not a failure.
        return False


def _privilege_list(privileges: str | None) -> list[str] | None:
    """
    Split the comma-separated privilege option into entries.

    Only the list syntax is undone here. Which keywords are acceptable is the
    manager's whitelist to decide, and duplicating it in the CLI is how the two
    lists end up disagreeing.

    Args:
        privileges: The raw option value, or None for the engine's default.

    Returns:
        The entries, or None when the operator gave none.
    """
    if not privileges:
        return None
    return [part.strip() for part in privileges.split(",") if part.strip()]


def _open_client(argv: Sequence[str]) -> NoReturn:
    """
    Replace this process with an interactive database client.

    This is the only execution in the CLI that does not go through the
    CommandRunner, and it has to be. The runner captures output and returns
    when the process is done, which turns a ``psql`` session into a hang with
    no prompt and no way to type into it. A client that owns the terminal is
    the whole point of ``noust db connect``, so this process steps aside for it.

    Args:
        argv: Program and arguments, as the manager built them.

    Raises:
        DatabaseError: When the client is not on PATH or cannot be executed.
    """
    try:
        os.execvp(argv[0], list(argv))  # noqa: S606
    except OSError as exc:
        raise DatabaseError(
            f"Could not start the database client: {argv[0]}",
            details=f"{exc}. Install the engine's client package and try again.",
        ) from exc


def _echo_json(data: Any) -> None:
    """
    Print data as indented JSON.

    Args:
        data: JSON-serialisable data.
    """
    click.echo(json.dumps(data, indent=2, default=str))


# ==================== Engine management ====================


def _install(engine: str, *, logger: Logger, version: str | None = None) -> int:
    """
    Install a database engine, in the flavour its name says and the version asked.

    Args:
        engine: Engine name or alias; ``mariadb`` and ``valkey`` name a flavour.
        logger: Logger for progress and errors.
        version: The version to install; the distribution's when None.

    Returns:
        Process exit code.
    """
    if _get_manager(engine, logger) is None:
        return 1
    service = _service(logger)
    try:
        decided = service.plan_engine_install(engine, version=version)
        if decided.already_installed:
            installed = decided.manager.get_version()
            logger.info(f"{decided.display_name} is already installed (v{installed})")
            return 0

        logger.step(1, 2, f"Installing {decided.describe()}...")
        outcome = service.install_engine(engine, version=version)

        logger.step(2, 2, "Installation complete")
        logger.success(f"{outcome.display_name} v{outcome.version} installed successfully")
        for warning in outcome.warnings:
            logger.warning(warning)
        return 0
    except NoustError as e:
        return _fail(logger, e)


def _catalog(*, json_output: bool, logger: Logger) -> int:
    """
    Show what can be installed on this server, and in which versions.

    Args:
        json_output: Print the catalog as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        catalog = _service(logger).install_catalog()
    except NoustError as e:
        return _fail(logger, e)
    if json_output:
        _echo_json(catalog)
        return 0
    click.echo(f"\nWhat Noust can install on {catalog['distribution']['name']}:")
    click.echo("-" * 70)
    for entry in catalog["flavours"]:
        versions = ", ".join(
            f"{choice['version']}{'*' if choice['default'] else ''}"
            f"{' (upstream)' if choice['source'] == 'upstream' else ''}"
            for choice in entry["versions"]
        )
        state = "installable" if entry["installable"] else entry["blocked"]
        click.echo(f"  {entry['display_name']:<12} {state:<14} {versions}")
        if entry["reason"] and not entry["installable"] and entry["blocked"] != "installed":
            click.echo(f"  {'':<12} {entry['reason']}")
    click.echo("\n  * the version installed when none is asked for")
    click.echo("  noust db install <flavour> [--version <version>]\n")
    return 0


def _parse_assignments(assignments: Sequence[str]) -> dict[str, str]:
    """
    Split ``KEY=VALUE`` arguments.

    Only the syntax is undone here; which keys exist and which values are
    acceptable is the settings module's to decide.

    Args:
        assignments: The arguments.

    Returns:
        Values by key.

    Raises:
        click.BadParameter: For an argument without ``=``.
    """
    values: dict[str, str] = {}
    for assignment in assignments:
        key, sep, value = assignment.partition("=")
        if not sep or not key.strip():
            raise click.BadParameter(
                f"{assignment!r} is not KEY=VALUE", param_hint="'KEY=VALUE...'"
            )
        values[key.strip()] = value
    return values


def _settings(
    engine: str,
    assignments: Sequence[str],
    *,
    confirm: bool,
    json_output: bool,
    logger: Logger,
) -> int:
    """
    Show an engine's settings, or change some of them.

    Args:
        engine: Engine name or alias.
        assignments: ``KEY=VALUE`` arguments; none to show the settings.
        confirm: Accept what the change costs (listening beyond loopback,
            writes refused or keys dropped, persistence off).
        json_output: Print the result as JSON.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    service = _service(logger)
    try:
        if not assignments:
            report = service.engine_settings(engine).to_dict()
            if json_output:
                _echo_json(report)
                return 0
            _print_settings(report)
            return 0
        values = _parse_assignments(assignments)
        outcome = service.change_engine_settings(engine, values, confirm=confirm)
    except ConfirmationRequired as e:
        for warning in e.warnings:
            logger.warning(warning)
        logger.info("Nothing was changed. Run it again with --yes to go ahead.")
        return 1
    except NoustError as e:
        logger.error(str(e))
        output = getattr(e, "output", None)
        if output:
            click.echo(output)
        return 1
    if json_output:
        _echo_json(outcome.to_dict())
        return 0
    if not outcome.changed:
        logger.info(f"{outcome.display_name} already has those settings; nothing changed")
        return 0
    how = {
        "restart": "restarted",
        "reload": "reloaded",
        "runtime": "applied to the running server",
    }.get(outcome.action, outcome.action)
    logger.success(
        f"{outcome.display_name}: {', '.join(outcome.changed)} changed in {outcome.file} ({how})"
    )
    for warning in outcome.warnings:
        logger.warning(warning)
    return 0


def _print_settings(report: dict[str, Any]) -> None:
    """
    Print an engine's settings as a table.

    Args:
        report: The report, as plain data.
    """
    memory = report["memory_bytes"] / 1024**3
    click.echo(
        f"\n{report['display_name']} settings ({report['file']}); recommendations for "
        f"{memory:.1f} GB and {report['cpus']} CPUs"
    )
    if not report["running"]:
        click.echo("The engine did not answer, so its current values are unknown.")
    click.echo("-" * 96)
    click.echo(f"  {'Setting':<44} {'Current':<16} {'Noust':<14} {'Recommended':<14} Restart")
    for item in report["settings"]:
        unit = f" {item['unit']}" if item["unit"] and item["kind"] != "size" else ""
        current = f"{item['current']}{unit}" if item["current"] is not None else "-"
        configured = item["configured"] if item["configured"] is not None else "-"
        recommended = item["recommended"] if item["recommended"] is not None else "-"
        restart = "yes" if item["restart"] else "no"
        locked = "  (not changed by Noust)" if not item["editable"] else ""
        click.echo(
            f"  {item['key']:<44} {current:<16} {configured:<14} {recommended:<14} "
            f"{restart}{locked}"
        )
    click.echo(
        "\n  noust db settings <engine> KEY=VALUE ...   (KEY=default removes Noust's value)\n"
    )


def _uninstall(engine: str, *, purge: bool, force: bool, logger: Logger) -> int:
    """
    Remove a database engine.

    Args:
        engine: Engine name or alias.
        purge: Also delete the data directory and the configuration.
        force: Do not ask for confirmation.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    try:
        if not manager.is_installed():
            logger.info(f"{manager.DISPLAY_NAME} is not installed")
            return 0

        question = f"Uninstall {manager.DISPLAY_NAME} from this server?"
        if purge:
            question = (
                f"Uninstall {manager.DISPLAY_NAME} and delete every database it holds, "
                "along with its configuration? This cannot be undone"
            )
        if not _confirm(question, force=force):
            logger.info("Cancelled")
            return 0

        logger.step(1, 2, f"Uninstalling {manager.DISPLAY_NAME}...")
        manager.uninstall(purge=purge)

        logger.step(2, 2, "Uninstallation complete")
        logger.success(f"{manager.DISPLAY_NAME} uninstalled")

        return 0
    except DatabaseError as e:
        return _fail(logger, e)


def _status(engine: str | None, *, json_output: bool, logger: Logger) -> int:
    """
    Report whether engines are installed and running, and their support.

    Args:
        engine: Engine name or alias. All engines when omitted.
        json_output: Print the statuses as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    if engine:
        manager = _get_manager(engine, logger)
        if manager is None:
            return 1
        managers = [manager]
    else:
        managers = DatabaseRegistry.get_all_managers(verbose=logger.verbose)

    statuses = [manager.get_status() for manager in managers]

    if json_output:
        _echo_json(statuses)
        return 0

    for status in statuses:
        installed = "yes" if status["installed"] else "no"
        version = status.get("version", "N/A")

        click.echo(f"\n{status['display_name']}")
        click.echo(f"  Installed: {installed}")
        if status["installed"]:
            click.echo(f"  Version:   {version}")
            click.echo(f"  Status:    {'running' if status.get('running') else 'stopped'}")
            click.echo(f"  Port:      {status['port']}")
            click.echo(f"  Service:   {status['service']}")
            support = status.get("support")
            if isinstance(support, dict) and support.get("status") in ("ending_soon", "ended"):
                click.echo(f"  Support:   {support['message']}")
        for warning in status.get("warnings") or []:
            click.echo(f"  Warning:   {warning}")

    return 0


def _start(engine: str, *, logger: Logger) -> int:
    """
    Start an engine's service.

    Args:
        engine: Engine name or alias.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    try:
        if not manager.is_installed():
            logger.error(f"{manager.DISPLAY_NAME} is not installed")
            logger.info(f"Install with: noust db install {manager.ENGINE_NAME}")
            return 1

        if manager.is_running():
            logger.info(f"{manager.DISPLAY_NAME} is already running")
            return 0

        manager.start()
        logger.success(f"{manager.DISPLAY_NAME} started")
        return 0
    except DatabaseError as e:
        return _fail(logger, e)


def _stop(engine: str, *, logger: Logger) -> int:
    """
    Stop an engine's service.

    Args:
        engine: Engine name or alias.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    try:
        if not manager.is_running():
            logger.info(f"{manager.DISPLAY_NAME} is not running")
            return 0

        manager.stop()
        logger.success(f"{manager.DISPLAY_NAME} stopped")
        return 0
    except DatabaseError as e:
        return _fail(logger, e)


def _restart(engine: str, *, logger: Logger) -> int:
    """
    Restart an engine's service.

    Args:
        engine: Engine name or alias.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    try:
        if not manager.is_installed():
            logger.error(f"{manager.DISPLAY_NAME} is not installed")
            return 1

        manager.restart()
        logger.success(f"{manager.DISPLAY_NAME} restarted")
        return 0
    except DatabaseError as e:
        return _fail(logger, e)


def _engines(*, json_output: bool, logger: Logger) -> int:
    """
    List the engines Noust knows how to manage.

    Args:
        json_output: Print the list as JSON.
        logger: Logger, used for its verbosity setting.

    Returns:
        Process exit code.
    """
    engines = []
    for engine in DatabaseRegistry.list_engines():
        manager = get_db_manager(engine, verbose=logger.verbose)
        if manager:
            installed = manager.is_installed()
            engines.append(
                {
                    "name": manager.ENGINE_NAME,
                    "display_name": manager.DISPLAY_NAME,
                    "installed": installed,
                    "version": manager.get_version() if installed else None,
                    "port": manager.server_port() if installed else manager.DEFAULT_PORT,
                    "capabilities": sorted(manager.CAPABILITIES),
                    "kind": "host",
                }
            )

    service = _service(logger)
    containers: list[dict[str, Any]] = []
    try:
        bound = service.instance_managers()
    except DatabaseError as exc:
        logger.warning(f"Could not list the database containers: {exc}")
        bound = []
    for manager in bound:
        if manager.instance is None:
            continue
        status = manager.get_status()
        containers.append(
            {
                "name": manager.ENGINE_NAME,
                "display_name": manager.DISPLAY_NAME,
                "installed": True,
                "running": status["running"],
                "version": status["version"],
                "port": status["port"],
                "capabilities": status["capabilities"],
                **manager.instance.to_dict(),
                "kind": "container",
            }
        )

    if json_output:
        _echo_json([*engines, *containers])
        return 0

    click.echo("\nAvailable Database Engines:")
    click.echo("-" * 50)

    for eng in engines:
        marker = "*" if eng["installed"] else " "
        version = f"v{eng['version']}" if eng["version"] else "not installed"
        click.echo(f"  [{marker}] {eng['display_name']:<20} {version:<15} (port {eng['port']})")

    if containers:
        click.echo("\nDatabase containers (use the name as --engine):")
        click.echo("-" * 50)
        for item in containers:
            marker = "*" if item["running"] else " "
            owner = f", {item['app']}" if item["app"] else ""
            limited = ", limited access" if item["access"] == "limited" else ""
            click.echo(
                f"  [{marker}] {item['name']:<40} {item['display_name']} "
                f"(container {item['container']}, port {item['port']}{owner}{limited})"
            )

    click.echo("")
    return 0


# ==================== Database management ====================


def _create(
    name: str,
    *,
    engine: str,
    owner: str | None,
    encoding: str | None,
    logger: Logger,
    app: str | None = None,
) -> int:
    """
    Create a database and record it in the store.

    Args:
        name: Database name.
        engine: Engine name or alias.
        owner: User that will own the database.
        encoding: Character encoding.
        logger: Logger for progress and errors.
        app: Application the database belongs to, so its backups include it.

    Returns:
        Process exit code.
    """
    try:
        view = _service(logger).create(engine, name, owner=owner, encoding=encoding, domain=app)
    except NoustError as e:
        return _fail(logger, e)

    logger.success(f"Created database: {view.name}")
    if view.size:
        logger.info(f"  Size: {view.size}")
    if view.encoding:
        logger.info(f"  Encoding: {view.encoding}")
    if app:
        logger.info(f"  Belongs to: {app}")
    return 0


def _drop(
    name: str,
    *,
    engine: str,
    force: bool,
    logger: Logger,
    keep_backup: bool = True,
    unlink: bool = False,
) -> int:
    """
    Delete a database, after its last dump, and forget it in the store.

    Args:
        name: Database name.
        engine: Engine name or alias.
        force: Do not ask for confirmation, and disconnect open sessions.
        logger: Logger for progress and errors.
        keep_backup: Dump it before dropping it.
        unlink: Remove its variables from the applications that use it.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    question = (
        f"Drop database '{name}' from {manager.DISPLAY_NAME}, "
        "deleting every table and row in it? This cannot be undone"
        + (" (a last dump is taken first)" if keep_backup else "")
    )
    if not _confirm(question, force=force):
        logger.info("Cancelled")
        return 0

    try:
        outcome = _service(logger).drop(
            engine, name, force=force, keep_backup=keep_backup, unlink=unlink
        )
    except NoustError as e:
        return _fail(logger, e)

    logger.success(f"Dropped database: {name}")
    if outcome.safety_copy:
        logger.info(f"  Last dump: {outcome.safety_copy}")
    if outcome.unlinked:
        logger.info(f"  Unlinked from: {', '.join(outcome.unlinked)}")
    return 0


def _list(*, engine: str | None, json_output: bool, logger: Logger) -> int:
    """
    List the databases of every running engine, joined with the store.

    Every problem is reported. The exit status is 1 only when an engine
    that exists could not be read: then the list is incomplete and a script
    must not take it for the whole truth. Docker failing to list its
    containers is reported as a warning and does not fail the command,
    since every engine Noust knows to exist was read; a server whose Docker
    daemon is down, or that has none, still lists its own databases.

    Args:
        engine: Engine name or alias. Every engine when omitted.
        json_output: Print the list as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        listing = _service(logger).listing(engine)
    except NoustError as e:
        return _fail(logger, e)
    views = listing.databases
    failed = 1 if any(problem.kind != "docker" for problem in listing.problems) else 0

    entries = []
    for view in views:
        data = view.to_dict()
        # The 2.x JSON called the owning application linked_app; kept for scripts.
        data["linked_app"] = view.app
        entries.append(data)

    if json_output:
        _echo_json(entries)
        for problem in listing.problems:
            click.echo(f"{problem.message}: {problem.output}", err=True)
        return failed

    for problem in listing.problems:
        if problem.kind == "docker":
            logger.warning(problem.message)
        else:
            logger.error(problem.message)
        if problem.output:
            click.echo(f"  {problem.output}")
        if problem.hint:
            logger.info(f"  {problem.hint}")

    if not entries:
        if not listing.problems:
            logger.info("No databases found")
        return failed

    by_engine: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        by_engine.setdefault(entry.get("engine", "unknown"), []).append(entry)

    for eng, rows in by_engine.items():
        click.echo(f"\n{eng.upper()}")
        click.echo("-" * 50)
        for entry in rows:
            size = entry.get("size", "")
            tables = entry.get("tables")
            keys = entry.get("keys")
            tracked = "*" if entry.get("tracked") else " "
            apps = entry.get("apps") or []
            linked = f" -> {', '.join(apps)}" if apps else ""
            missing = " (missing from the engine)" if entry.get("missing") else ""
            if entry.get("unverified"):
                missing = " (the engine could not be read)"

            size_str = f" ({size})" if size else ""
            tables_str = f" - {tables} tables" if tables is not None else ""
            if keys is not None:
                tables_str = f" - {keys} keys"

            click.echo(f"  [{tracked}] {entry['name']}{size_str}{tables_str}{linked}{missing}")

    click.echo("")
    click.echo("  [*] = tracked by Noust")
    return failed


def _info(name: str, *, engine: str, json_output: bool, logger: Logger) -> int:
    """
    Show what the engine and Noust know about one database.

    Args:
        name: Database name.
        engine: Engine name or alias.
        json_output: Print the details as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        overview = _service(logger).overview(engine, name)
    except NoustError as e:
        return _fail(logger, e)

    if json_output:
        _echo_json(overview)
        return 0

    view = overview["database"]
    click.echo(f"\nDatabase: {view['name']}")
    click.echo(f"Engine:   {overview['display_name']} {view.get('engine_version') or ''}".rstrip())
    for label, key in (
        ("Size", "size"),
        ("Tables", "tables"),
        ("Owner", "owner"),
        ("Encoding", "encoding"),
        ("App", "app"),
        ("Backup", "last_backup"),
    ):
        if view.get(key):
            click.echo(f"{label + ':':<10}{view[key]}")
    support = overview["support"]
    if support.get("status") in ("ending_soon", "ended"):
        click.echo(f"Support:  {support['message']}")
    for entry in overview["access"]:
        marker = " (internal)" if entry["internal"] else ""
        click.echo(f"  {entry['username']}: {entry['profile']}{marker}")
    return 0


def _adopt(*, engine: str | None, logger: Logger) -> int:
    """
    Record the databases the engines hold and Noust does not track.

    Args:
        engine: Only this engine.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        service = _service(logger)
        adopted = service.adopt(engine)
    except NoustError as e:
        return _fail(logger, e)
    if not adopted:
        logger.info("Every database is already tracked")
    for name in adopted:
        logger.success(f"Now tracked: {name}")
    return 0


def _forget(name: str, *, engine: str, logger: Logger) -> int:
    """
    Forget a tracked database the engine no longer has.

    Args:
        name: Database name.
        engine: Engine name or alias.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        removed = _service(logger).forget(engine, name)
    except NoustError as e:
        return _fail(logger, e)
    if removed:
        logger.success(f"Forgot {name}")
    else:
        logger.info(f"Noust did not track {name}")
    return 0


def _fix_owner(
    name: str,
    *,
    engine: str,
    owner: str | None,
    apply: bool,
    force: bool,
    json_output: bool,
    logger: Logger,
) -> int:
    """
    Show, or apply, giving a PostgreSQL database to its application's role.

    Args:
        name: Database name.
        engine: Engine name or alias.
        owner: The role; the provisioned one by default.
        apply: Run the statements.
        force: Do not ask before applying.
        json_output: Print the plan as JSON.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    service = _service(logger)
    try:
        plan = service.fix_owner(engine, name, owner=owner)
        if apply and plan.current_owner != plan.new_owner:
            question = (
                f"Give '{name}' and {len(plan.objects)} object(s) owned by "
                f"{plan.current_owner} to {plan.new_owner}?"
            )
            if not _confirm(question, force=force):
                logger.info("Cancelled")
                return 0
            plan = service.fix_owner(engine, name, owner=owner, apply=True)
    except NoustError as e:
        return _fail(logger, e)

    if json_output:
        _echo_json(plan.to_dict())
        return 0
    click.echo(f"\nDatabase:      {plan.database}")
    click.echo(f"Current owner: {plan.current_owner}")
    click.echo(f"New owner:     {plan.new_owner}")
    if plan.current_owner == plan.new_owner:
        logger.info(f"{name} already belongs to {plan.new_owner}")
        return 0
    click.echo("\nStatements:")
    for statement in plan.statements:
        click.echo(f"  {statement}")
    if plan.applied:
        logger.success(f"{name} now belongs to {plan.new_owner}")
        logger.info(
            f"Put it back with: noust db fix-owner {name} -e {engine} "
            f"--owner {plan.current_owner} --apply"
        )
    else:
        logger.info("Nothing changed. Run again with --apply to make the change.")
    return 0


# ==================== User management ====================


def _user_create(
    username: str,
    *,
    engine: str,
    password: str | None,
    database: str | None,
    host: str,
    logger: Logger,
    profile: str | None = None,
) -> int:
    """
    Create a database user, generating a password when none is given.

    Args:
        username: User name.
        engine: Engine name or alias.
        password: Password. Generated and printed once when omitted.
        database: Database to give the new user access to.
        host: Host the user may connect from.
        logger: Logger for progress and errors.
        profile: Its access profile on that database.

    Returns:
        Process exit code.
    """
    try:
        user, secret = _service(logger).create_user(
            engine, username, password=password, host=host, database=database, profile=profile
        )
    except NoustError as e:
        return _fail(logger, e)

    logger.success(f"Created user: {user.username}")
    if not password:
        logger.info(f"  Password: {secret}")
        logger.warning("  Save this password; 'noust db user-password' rotates it later.")
    if database:
        logger.info(f"  Access to {database}: {profile or 'the engine default'}")
    return 0


def _user_delete(username: str, *, engine: str, host: str, force: bool, logger: Logger) -> int:
    """
    Delete a database user.

    Args:
        username: User name.
        engine: Engine name or alias.
        host: Host restriction the user was created with.
        force: Do not ask for confirmation.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    question = (
        f"Delete user '{username}'@'{host}' from {manager.DISPLAY_NAME}? "
        "Anything connecting as this user will stop working"
    )
    if not _confirm(question, force=force):
        logger.info("Cancelled")
        return 0

    try:
        _service(logger).drop_user(engine, username, host=host)
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"Deleted user: {username}")
    return 0


def _user_list(*, engine: str, json_output: bool, logger: Logger) -> int:
    """
    List an engine's users, internal ones marked.

    Args:
        engine: Engine name or alias.
        json_output: Print the list as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        users = _service(logger).list_users(engine)
    except NoustError as e:
        return _fail(logger, e)

    if json_output:
        _echo_json([u.to_dict() for u in users])
        return 0

    if not users:
        logger.info("No users found")
        return 0

    click.echo(f"\n{engine} users:")
    click.echo("-" * 50)
    for user in users:
        host_str = f"@{user.host}" if user.host != "localhost" else ""
        internal = " (internal)" if user.extra.get("internal") else ""
        privs = ", ".join(user.privileges[:3]) if user.privileges else ""
        if len(user.privileges) > 3:
            privs += f" (+{len(user.privileges) - 3} more)"

        click.echo(f"  {user.username}{host_str}{internal}")
        if privs:
            click.echo(f"    Privileges: {privs}")

    click.echo("")
    return 0


def _grant(
    username: str,
    database: str,
    *,
    engine: str,
    privileges: str | None,
    host: str,
    logger: Logger,
) -> int:
    """
    Grant a user privileges on a database.

    Args:
        username: User name.
        database: Database name.
        engine: Engine name or alias.
        privileges: Comma-separated privileges, or None for the engine default.
        host: Host the grant applies to.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        _service(logger).grant(
            engine, username, database, privileges=_privilege_list(privileges), host=host
        )
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"Granted privileges on {database} to {username}")
    return 0


def _revoke(
    username: str,
    database: str,
    *,
    engine: str,
    privileges: str | None,
    host: str,
    logger: Logger,
) -> int:
    """
    Take privileges away from a user.

    Args:
        username: User name.
        database: Database name.
        engine: Engine name or alias.
        privileges: Comma-separated privileges, or None for the engine default.
        host: Host the grant applies to.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        _service(logger).revoke(
            engine, username, database, privileges=_privilege_list(privileges), host=host
        )
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"Revoked privileges on {database} from {username}")
    return 0


def _access(
    database: str,
    *,
    engine: str,
    username: str | None,
    profile: str | None,
    host: str,
    json_output: bool,
    logger: Logger,
) -> int:
    """
    List who can reach a database, or give one account a profile on it.

    Args:
        database: Database name.
        engine: Engine name or alias.
        username: The account to change; list when None.
        profile: Its new profile.
        host: Its host restriction.
        json_output: Print the list as JSON.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    service = _service(logger)
    try:
        if username or profile:
            if not (username and profile):
                logger.error("Give both --user and --profile to change an account's access")
                return 1
            service.set_profile(engine, database, username, profile, host=host)
            logger.success(f"{username} now has the {profile} profile on {database}")
            return 0
        entries = service.access(engine, database)
    except NoustError as e:
        return _fail(logger, e)

    if json_output:
        _echo_json([entry.to_dict() for entry in entries])
        return 0
    if not entries:
        logger.info(f"No account holds anything on {database}")
        return 0
    for entry in entries:
        marker = " (internal)" if entry.internal else ""
        apps = f" -> {', '.join(entry.apps)}" if entry.apps else ""
        click.echo(f"  {entry.username}@{entry.host}: {entry.profile}{marker}{apps}")
    return 0


def _user_password(
    username: str,
    *,
    engine: str,
    host: str,
    propagate: bool,
    force: bool,
    logger: Logger,
    first_password: bool = False,
) -> int:
    """
    Rotate an account's password and give it to the applications that use it.

    Args:
        username: The account; ``default`` for Redis's ``requirepass``.
        engine: Engine name or alias.
        host: Its host restriction.
        propagate: Rewrite and restart the applications that use it.
        force: Do not ask for confirmation.
        logger: Logger for progress and errors.
        first_password: Give a Redis instance with no password its first one.

    Returns:
        Process exit code.
    """
    question = f"Give '{username}' a new password" + (
        " and restart every application that signs in as it (each behind its "
        "health gate; a failure undoes everything)?"
        if propagate
        else "? Applications that sign in as it will stop connecting"
    )
    if not _confirm(question, force=force):
        logger.info("Cancelled")
        return 0
    try:
        outcome = _service(logger).rotate_password(
            engine, username, host=host, propagate=propagate, first_password=first_password
        )
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"New password for {username}: {outcome.password}")
    if outcome.apps:
        logger.info(f"  Given to: {', '.join(outcome.apps)}")
    return 0


# ==================== Links ====================


def _link(
    domain: str,
    database: str,
    *,
    engine: str,
    username: str | None,
    env_var: str | None,
    extra_vars: bool,
    restart: bool,
    logger: Logger,
) -> int:
    """
    Give an application a database's connection string.

    Args:
        domain: The application.
        database: The database.
        engine: Engine name or alias.
        username: The account to sign in as.
        env_var: The variable to write.
        extra_vars: Also write the ``DB_*`` variables.
        restart: Restart the application behind its gate.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        outcome = _service(logger).link(
            domain,
            engine,
            database,
            username=username,
            env_var=env_var,
            extra_vars=extra_vars,
            restart=restart,
        )
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"{database} linked to {domain} as {', '.join(outcome.env_vars)}")
    if not outcome.restarted and restart:
        logger.info("  The application runs nothing to restart.")
    return 0


def _unlink(
    domain: str,
    database: str,
    *,
    engine: str,
    drop: bool,
    restart: bool,
    force: bool,
    logger: Logger,
) -> int:
    """
    Take a database away from an application, and drop it if asked.

    Args:
        domain: The application.
        database: The database.
        engine: Engine name or alias.
        drop: Drop it too, after its last dump.
        restart: Restart the application behind its gate.
        force: Do not ask for confirmation.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    question = f"Remove {database}'s variables from {domain}" + (
        " and drop the database (a last dump is taken first)?" if drop else "?"
    )
    if not _confirm(question, force=force):
        logger.info("Cancelled")
        return 0
    try:
        dropped = _service(logger).unlink(domain, engine, database, drop=drop, restart=restart)
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"{database} unlinked from {domain}")
    if dropped and dropped.safety_copy:
        logger.info(f"  Dropped; last dump: {dropped.safety_copy}")
    return 0


def _links(domain: str, *, json_output: bool, logger: Logger) -> int:
    """
    List the databases an application uses.

    Args:
        domain: The application.
        json_output: Print the list as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        views = _service(logger).app_databases(domain)
    except NoustError as e:
        return _fail(logger, e)
    if json_output:
        _echo_json([view.to_dict() for view in views])
        return 0
    if not views:
        logger.info(f"{domain} uses no database")
        return 0
    for view in views:
        variable = view.env_var or "(variable not recorded)"
        click.echo(f"  {view.engine}/{view.database}  {variable}  {view.url or ''}".rstrip())
    return 0


def _provision(
    domain: str,
    *,
    engine: str,
    name: str | None,
    env_var: str | None,
    extra_vars: bool,
    restart: bool,
    logger: Logger,
) -> int:
    """
    Create a database and an account for an application, and link them.

    Args:
        domain: The application.
        engine: Engine name or alias.
        name: The database; derived from the application when omitted.
        env_var: The variable to write.
        extra_vars: Also write the ``DB_*`` variables.
        restart: Restart the application behind its gate.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        outcome = _service(logger).provision_for_app(
            domain, engine, name=name, env_var=env_var, extra_vars=extra_vars, restart=restart
        )
    except NoustError as e:
        return _fail(logger, e)
    verb = "Created and linked" if outcome.created_database else "Linked"
    logger.success(f"{verb} {outcome.database} for {domain} as {', '.join(outcome.env_vars)}")
    return 0


# ==================== Backup and restore ====================


def _backup(
    database: str,
    *,
    engine: str,
    output: Path | None,
    compress: bool,
    logger: Logger,
    dump_format: str | None = None,
) -> int:
    """
    Write a database to a backup file.

    Args:
        database: Database name.
        engine: Engine name or alias.
        output: Where to write the backup. The engine's backup directory when
            omitted.
        compress: Compress the backup with gzip.
        logger: Logger for progress and errors.
        dump_format: PostgreSQL's dump format.

    Returns:
        Process exit code.
    """
    logger.step(1, 2, f"Creating backup of {database}...")
    service = _service(logger)
    checked = ""
    try:
        if output is None:
            # A dump in the engine's own directory is hashed, checked and recorded;
            # one written to a path of the operator's choosing is only written.
            view = DatabaseBackups(service).dump(
                engine, database, compress=compress, dump_format=dump_format
            )
            backup_info = view.info
            checked = (view.record.verify_detail or "") if view.record else ""
        else:
            backup_info = service.dump(
                engine, database, compress=compress, dump_format=dump_format, output=output
            )
    except NoustError as e:
        return _fail(logger, e)

    logger.step(2, 2, "Backup complete")
    logger.success(f"Backup created: {backup_info.path}")
    logger.info(f"  Size: {backup_info.to_dict()['size_human']}")
    if checked:
        logger.info(f"  Checked: {checked}")
    return 0


def _restore(
    database: str,
    backup_file: Path,
    *,
    engine: str,
    drop_existing: bool,
    force: bool,
    logger: Logger,
    new_name: str | None = None,
    safety_backup: bool = True,
) -> int:
    """
    Load a backup into a database, or into a new one beside it.

    Args:
        database: The database the backup is of, and the target unless
            ``new_name`` is given.
        backup_file: Backup file to read.
        drop_existing: Drop the target database before restoring. A safety
            copy is taken first, and put back if the restore fails.
        force: Do not ask for confirmation.
        logger: Logger for progress and errors.
        new_name: Restore into a new database instead.
        safety_backup: Dump the target first even when nothing is dropped.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    if new_name:
        question = f"Restore {backup_file} into a new database '{new_name}'?"
    elif drop_existing:
        question = (
            f"Drop database '{database}' and restore it from {backup_file}? "
            "A safety copy is taken first and put back if the restore fails"
        )
    else:
        question = (
            f"Restore database '{database}' from {backup_file}? "
            "Existing rows may be overwritten"
            + ("; a safety copy is taken first" if safety_backup else "")
        )
    if not _confirm(question, force=force):
        logger.info("Cancelled")
        return 0

    logger.step(1, 2, f"Restoring {new_name or database}...")
    try:
        outcome = DatabaseBackups(_service(logger)).restore_file(
            engine,
            database,
            backup_file,
            drop_existing,
            safety_backup,
            new_name,
        )
    except NoustError as e:
        return _fail(logger, e)

    logger.step(2, 2, "Restore complete")
    logger.success(f"Database {outcome.database} restored")
    if outcome.safety_copy:
        logger.info(f"  Safety copy: {outcome.safety_copy}")
    return 0


def _backups(*, engine: str | None, database: str | None, json_output: bool, logger: Logger) -> int:
    """
    List the backups on this server.

    Args:
        engine: Engine name or alias. Every engine when omitted.
        database: Only list backups of this database.
        json_output: Print the list as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        # The same rows the console's table shows: who made each dump, whether it
        # was checked, where a copy went.
        listed = DatabaseBackups(_service(logger)).list_dumps(engine, database)
        dumps = [dump.to_dict() for dump in listed]
    except NoustError as e:
        return _fail(logger, e)

    if json_output:
        _echo_json(dumps)
        return 0

    if not dumps:
        logger.info("No backups found")
        return 0

    click.echo("\nAvailable Backups:")
    click.echo("-" * 60)
    for backup_dict in dumps:
        click.echo(
            f"  {backup_dict['database']} ({backup_dict['engine']}, {backup_dict['format']})"
        )
        click.echo(f"    Path:    {backup_dict['path']}")
        click.echo(f"    Size:    {backup_dict['size_human']}")
        click.echo(f"    Created: {backup_dict['created']}")
        click.echo(f"    Checked: {backup_dict['verify_status']} ({backup_dict['kind']})")
        for copy in backup_dict["destinations"]:
            click.echo(f"    Copy:    {copy['destination']}")
        click.echo("")

    return 0


# ==================== Query, connection and exposure ====================


def _query(database: str, query: str, *, engine: str, read_only: bool, logger: Logger) -> int:
    """
    Run one statement against a database.

    Args:
        database: Database name.
        query: The statement.
        engine: Engine name or alias.
        read_only: Run it as the database's read-only account.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    if read_only and "read_only" not in getattr(manager, "CAPABILITIES", frozenset()):
        logger.error(f"Read-only mode is not available for {manager.DISPLAY_NAME}")
        logger.info(
            "Noust only runs a statement read-only where the database server itself can "
            "hold the session read-only (PostgreSQL and MySQL). Re-run with --write if you "
            "accept that the statement may change data."
        )
        return 1

    try:
        # Checked before the engine is looked at, so a refused statement never
        # reaches it. --write is the operator saying the statements may change
        # data, and a batch is then a legitimate thing to send.
        statement = console_request(query, single=read_only)
    except DatabaseQueryError as e:
        logger.error(str(e))
        if read_only:
            logger.info("Pass --write if you accept that the statements may change data.")
        return 1

    try:
        if not manager.is_running():
            logger.error(f"{manager.DISPLAY_NAME} is not running")
            return 1

        # Beyond the one-statement rule the guarantee is the server's: a
        # read runs signed in as the database's read-only account, whatever
        # its first keyword says.
        success, output = manager.execute_query(database, statement, read_only=read_only)
    except DatabaseError as e:
        return _fail(logger, e)

    if output:
        click.echo(output)
    return 0 if success else 1


def _connect(
    *,
    engine: str,
    database: str | None,
    username: str | None,
    dry_run: bool,
    logger: Logger,
) -> int:
    """
    Open an interactive session with the engine's own client.

    Args:
        engine: Engine name or alias.
        database: Database to open.
        username: User to connect as.
        dry_run: Report the client command instead of running it.
        logger: Logger for progress and errors.

    Returns:
        Process exit code. On success this process has already been replaced by
        the client and nothing is returned.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    try:
        if not manager.is_installed():
            logger.error(f"{manager.DISPLAY_NAME} is not installed")
            return 1

        if not manager.is_running():
            logger.error(f"{manager.DISPLAY_NAME} is not running")
            return 1

        cmd = manager.get_interactive_command(database=database, username=username)
    except (DatabaseError, NotImplementedError) as e:
        logger.error(str(e))
        return 1

    if dry_run:
        logger.info(f"would run: {' '.join(cmd)}")
        return 0

    logger.info(f"Connecting to {manager.DISPLAY_NAME}...")
    logger.info(f"Command: {' '.join(cmd)}")

    _open_client(cmd)


def _connection_string(
    database: str,
    username: str,
    *,
    engine: str,
    password: str | None,
    host: str,
    logger: Logger,
) -> int:
    """
    Print a connection string for an application to use.

    Args:
        database: Database name.
        username: User name.
        engine: Engine name or alias.
        password: Password. A placeholder is printed when omitted, so the
            string can be shown without a secret in it.
        host: Host the application will connect to.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    manager = _get_manager(engine, logger)
    if not manager:
        return 1

    conn_string = manager.get_connection_string(
        database=database,
        username=username,
        password=password or PASSWORD_PLACEHOLDER,
        host=host,
    )

    click.echo(conn_string)
    return 0


def _connect_info(
    database: str,
    *,
    engine: str,
    username: str | None,
    server: str | None,
    ssh_user: str | None,
    json_output: bool,
    logger: Logger,
) -> int:
    """
    Print how to reach a database from another computer, through SSH.

    Args:
        database: Database name.
        engine: Engine name or alias.
        username: The account; the provisioned one by default.
        server: This server's address as the operator reaches it.
        ssh_user: The SSH account.
        json_output: Print everything as JSON.
        logger: Logger for errors.

    Returns:
        Process exit code.
    """
    try:
        info = _service(logger).connection_info(
            engine, database, username=username, server=server, ssh_user=ssh_user
        )
    except NoustError as e:
        return _fail(logger, e)
    if json_output:
        _echo_json(info)
        return 0
    tunnel = info["tunnel"]
    click.echo("\nFrom your computer, open the tunnel:")
    click.echo(f"  {tunnel['command']}")
    click.echo("\nthen connect to:")
    click.echo(f"  {tunnel['url']}")
    for client, line in tunnel["clients"].items():
        click.echo(f"  {client}: {line}")
    for exposed in info["exposed"]:
        logger.warning(
            f"Port {exposed['port']} is open on {exposed['address']}. {exposed['advice']}"
        )
    return 0


def _exposure(*, json_output: bool, logger: Logger) -> int:
    """
    List the database ports open beyond this machine.

    A Docker-published port the firewall keeps the Internet out of (the
    ``DOCKER-USER`` chain refuses it on the public interface) is shown as
    information, with what closes it, and does not count.

    Args:
        json_output: Print the list as JSON: every finding, the closed ones
            flagged ``firewalled`` with ``closed_by`` and ``rule``.
        logger: Logger for the findings.

    Returns:
        Process exit code: 0 when nothing is exposed, 1 when something is,
        so a script can alert on it. A port the firewall closes is not.
    """
    found = _service(logger).exposure(include_firewalled=True)
    open_ports = [entry for entry in found if not entry.firewalled]
    if json_output:
        _echo_json([entry.to_dict() for entry in found])
        return 1 if open_ports else 0
    if not open_ports:
        logger.success("No database port is open beyond this machine")
    for entry in found:
        where = f" (container {entry.container}, {entry.image})" if entry.container else ""
        line = f"{entry.engine} on {entry.address}:{entry.port}{where}"
        if entry.firewalled:
            logger.info(f"{line}: closed by {entry.closed_by}")
            continue
        logger.warning(line)
        logger.info(f"  {entry.advice}")
    return 1 if open_ports else 0


def _config(*, engine: str, user: str | None, password: str | None, logger: Logger) -> int:
    """
    Store the account Noust signs in to an engine with, once the engine accepts it.

    Args:
        engine: Engine name or alias.
        user: Administrative user name.
        password: Administrative password.
        logger: Logger for progress and errors.

    Returns:
        Process exit code.
    """
    try:
        _service(logger).set_credentials(engine, user, password)
    except NoustError as e:
        return _fail(logger, e)
    logger.success(f"Noust signs in to {engine} with that account from now on")
    return 0


# ==================== The argparse front end, on its way out ====================

#: How each legacy action reaches the function that does the work. The lambdas
#: exist so :func:`handle_db` and the Click tree share one implementation.
#: ``noust.cli.parser`` is gone and nothing in Noust calls :func:`handle_db`
#: anymore - :mod:`noust.cli.interactive` has no database menu - but the tests
#: still exercise it directly, and this table is what keeps it from growing a
#: second copy of the logic if it is ever wired up again.
_LEGACY_ACTIONS: dict[str, Callable[[Namespace, Logger], int]] = {
    "install": lambda args, log: _install(args.engine, logger=log),
    "uninstall": lambda args, log: _uninstall(
        args.engine, purge=args.purge, force=args.force, logger=log
    ),
    "status": lambda args, log: _status(
        getattr(args, "engine", None), json_output=getattr(args, "json", False), logger=log
    ),
    "start": lambda args, log: _start(args.engine, logger=log),
    "stop": lambda args, log: _stop(args.engine, logger=log),
    "restart": lambda args, log: _restart(args.engine, logger=log),
    "engines": lambda args, log: _engines(json_output=getattr(args, "json", False), logger=log),
    "create": lambda args, log: _create(
        args.name,
        engine=args.engine,
        owner=getattr(args, "owner", None),
        encoding=getattr(args, "encoding", None),
        logger=log,
    ),
    "drop": lambda args, log: _drop(args.name, engine=args.engine, force=args.force, logger=log),
    "list": lambda args, log: _list(
        engine=getattr(args, "engine", None),
        json_output=getattr(args, "json", False),
        logger=log,
    ),
    "info": lambda args, log: _info(
        args.name, engine=args.engine, json_output=getattr(args, "json", False), logger=log
    ),
    "user-create": lambda args, log: _user_create(
        args.username,
        engine=args.engine,
        password=getattr(args, "password", None),
        database=getattr(args, "database", None),
        host=getattr(args, "host", "localhost"),
        logger=log,
    ),
    "user-delete": lambda args, log: _user_delete(
        args.username,
        engine=args.engine,
        host=getattr(args, "host", "localhost"),
        force=args.force,
        logger=log,
    ),
    "user-list": lambda args, log: _user_list(
        engine=args.engine, json_output=getattr(args, "json", False), logger=log
    ),
    "grant": lambda args, log: _grant(
        args.username,
        args.database,
        engine=args.engine,
        privileges=getattr(args, "privileges", None),
        host=getattr(args, "host", "localhost"),
        logger=log,
    ),
    "revoke": lambda args, log: _revoke(
        args.username,
        args.database,
        engine=args.engine,
        privileges=getattr(args, "privileges", None),
        host=getattr(args, "host", "localhost"),
        logger=log,
    ),
    "backup": lambda args, log: _backup(
        args.database,
        engine=args.engine,
        output=Path(args.output) if getattr(args, "output", None) else None,
        compress=not getattr(args, "no_compress", False),
        logger=log,
    ),
    "restore": lambda args, log: _restore(
        args.database,
        Path(args.file),
        engine=args.engine,
        drop_existing=getattr(args, "drop", False),
        force=args.force,
        logger=log,
    ),
    "backups": lambda args, log: _backups(
        engine=getattr(args, "engine", None),
        database=getattr(args, "database", None),
        json_output=getattr(args, "json", False),
        logger=log,
    ),
    # The argparse tree has no way to ask for a write, so it keeps its old
    # behaviour. Read-only by default is the Click front end's contract.
    "query": lambda args, log: _query(
        args.database, args.query, engine=args.engine, read_only=False, logger=log
    ),
    "connect": lambda args, log: _connect(
        engine=args.engine,
        database=getattr(args, "database", None),
        username=getattr(args, "username", None),
        dry_run=False,
        logger=log,
    ),
    "connection-string": lambda args, log: _connection_string(
        args.database,
        args.username,
        engine=args.engine,
        password=getattr(args, "password", None),
        host=getattr(args, "host", "localhost"),
        logger=log,
    ),
    "config": lambda args, log: _config(
        engine=args.engine,
        user=getattr(args, "user", None),
        password=getattr(args, "password", None),
        logger=log,
    ),
}


def handle_db(args: Namespace) -> int:
    """
    Route a parsed argparse namespace to the right database action.

    ``noust.cli.parser`` is gone and nothing calls this in production; it is
    kept, and tested directly, so a change to the Click commands cannot drift
    from :data:`_LEGACY_ACTIONS` unnoticed. It delegates to the same functions
    the Click commands call.

    Args:
        args: The parsed arguments.

    Returns:
        Process exit code.
    """
    verbose = getattr(args, "verbose", False)
    logger = Logger(verbose=verbose)
    action = getattr(args, "action", None)

    if not action:
        logger.error("No action specified")
        logger.info("Use: noust db --help")
        return 1

    handler = _LEGACY_ACTIONS.get(action)
    if not handler:
        logger.error(f"Unknown action: {action}")
        return 1

    return handler(args, logger)


# ==================== The Click front end ====================


class DatabaseGroup(NoustGroup):
    """
    The ``db`` group, with the shorthand its subcommands have always had.

    ``noust db ls`` is in scripts and in muscle memory, so it resolves here
    rather than being a second registration that drifts from the first.
    """

    #: Local shorthand to the command it stands for.
    ALIASES: dict[str, str] = {"ls": "list"}

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        """
        Look a subcommand up, resolving the group's own shorthand.

        Args:
            ctx: The Click context.
            name: The name the operator typed.

        Returns:
            The command, or None when there is no such subcommand.
        """
        return super().get_command(ctx, self.ALIASES.get(name, name))


@click.group(cls=DatabaseGroup, name="db")
def cli() -> None:
    """Install engines, and manage databases, users, backups and application links."""


@cli.command()
@click.argument("engine", type=ENGINE)
@click.option(
    "--version",
    "version",
    help="Version to install (see 'noust db catalog'); the distribution's by default.",
)
@pass_context
def install(ctx: Context, engine: str, version: str | None) -> None:
    """Install a database engine: postgresql, mysql, mariadb, redis, valkey or mongodb."""
    _exit(_install(engine, logger=ctx.logger, version=version))


@cli.command(read_only=True)
@json_option("Print the catalog as JSON.")
@pass_context
def catalog(ctx: Context) -> None:
    """Show which engines and versions can be installed on this server."""
    _exit(_catalog(json_output=ctx.json_output, logger=ctx.logger))


@cli.command(read_only=lambda params: not params.get("assignments"))
@click.argument("engine", type=ENGINE)
@click.argument("assignments", nargs=-1, metavar="[KEY=VALUE]...")
@click.option(
    "--yes",
    "-y",
    "confirm",
    is_flag=True,
    help=(
        "Accept what the change costs: listening beyond this server, a Redis that "
        "refuses writes or drops keys, persistence turned off."
    ),
)
@json_option("Print the settings or the outcome as JSON.")
@pass_context
def settings(ctx: Context, engine: str, assignments: tuple[str, ...], confirm: bool) -> None:
    """Show an engine's settings, or change them with KEY=VALUE."""
    _exit(
        _settings(
            engine,
            assignments,
            confirm=confirm,
            json_output=ctx.json_output,
            logger=ctx.logger,
        )
    )


@cli.command()
@click.argument("engine", type=ENGINE)
@click.option("--purge", is_flag=True, help="Also delete the data and the configuration.")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def uninstall(ctx: Context, engine: str, purge: bool, force: bool) -> None:
    """Remove a database engine from this server."""
    _exit(_uninstall(engine, purge=purge, force=force, logger=ctx.logger))


@cli.command()
@click.argument("engine", type=ENGINE, required=False)
@json_option("Print the statuses as JSON.")
@pass_context
def status(ctx: Context, engine: str | None) -> None:
    """Show which engines are installed and running, and their upstream support."""
    _exit(_status(engine, json_output=ctx.json_output, logger=ctx.logger))


@cli.command()
@click.argument("engine", type=ENGINE)
@pass_context
def start(ctx: Context, engine: str) -> None:
    """Start an engine's service."""
    _exit(_start(engine, logger=ctx.logger))


@cli.command()
@click.argument("engine", type=ENGINE)
@pass_context
def stop(ctx: Context, engine: str) -> None:
    """Stop an engine's service."""
    _exit(_stop(engine, logger=ctx.logger))


@cli.command()
@click.argument("engine", type=ENGINE)
@pass_context
def restart(ctx: Context, engine: str) -> None:
    """Restart an engine's service."""
    _exit(_restart(engine, logger=ctx.logger))


@cli.command()
@json_option("Print the engine list as JSON.")
@pass_context
def engines(ctx: Context) -> None:
    """List the engines Noust can manage, and their versions."""
    _exit(_engines(json_output=ctx.json_output, logger=ctx.logger))


@cli.command()
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to create it on.")
@click.option("--owner", "-o", help="User that will own the database.")
@click.option("--encoding", help="Character encoding. Defaults to UTF8.")
@click.option("--app", help="Application it belongs to, so its backups include it.")
@pass_context
def create(
    ctx: Context,
    name: str,
    engine: str,
    owner: str | None,
    encoding: str | None,
    app: str | None,
) -> None:
    """Create a database and record it in the store."""
    _exit(_create(name, engine=engine, owner=owner, encoding=encoding, logger=ctx.logger, app=app))


@cli.command()
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@click.option("--no-backup", is_flag=True, help="Do not take a last dump before dropping.")
@click.option(
    "--unlink", is_flag=True, help="Also remove its variables from the applications using it."
)
@pass_context
def drop(ctx: Context, name: str, engine: str, force: bool, no_backup: bool, unlink: bool) -> None:
    """Delete a database and everything in it, after a last dump."""
    _exit(
        _drop(
            name,
            engine=engine,
            force=force,
            logger=ctx.logger,
            keep_backup=not no_backup,
            unlink=unlink,
        )
    )


@cli.command("list")
@click.option("--engine", "-e", type=ENGINE, help="Only this engine. Defaults to all of them.")
@click.option(
    "--open",
    "open_panel",
    is_flag=True,
    help="Print the panel URL for the database list, opening it if a display is available.",
)
@json_option("Print the database list as JSON.")
@pass_context
def list_databases(ctx: Context, engine: str | None, open_panel: bool) -> None:
    """List the databases on every running engine."""
    code = _list(engine=engine, json_output=ctx.json_output, logger=ctx.logger)
    if open_panel and code == 0:
        open_in_panel("/databases", logger=ctx.logger)
    _exit(code)


@cli.command()
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@json_option("Print the database's details as JSON.")
@pass_context
def info(ctx: Context, name: str, engine: str) -> None:
    """Show a database's size, owner, application, support and access."""
    _exit(_info(name, engine=engine, json_output=ctx.json_output, logger=ctx.logger))


@cli.command()
@click.option("--engine", "-e", type=ENGINE, help="Only this engine. Defaults to all of them.")
@pass_context
def adopt(ctx: Context, engine: str | None) -> None:
    """Track the databases created outside Noust."""
    _exit(_adopt(engine=engine, logger=ctx.logger))


@cli.command()
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database was on.")
@pass_context
def forget(ctx: Context, name: str, engine: str) -> None:
    """Forget a tracked database that no longer exists on its engine."""
    _exit(_forget(name, engine=engine, logger=ctx.logger))


@cli.command("fix-owner")
@click.argument("name")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--owner", help="Role that must own it. Defaults to the one Noust provisioned.")
@click.option(
    "--apply", "apply_changes", is_flag=True, help="Make the change; only show it without."
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask before applying.")
@json_option("Print the plan as JSON.")
@pass_context
def fix_owner(
    ctx: Context, name: str, engine: str, owner: str | None, apply_changes: bool, force: bool
) -> None:
    """
    Give a PostgreSQL database, and its objects, to its application's role.

    Since PostgreSQL 15 only a database's owner may create in its public
    schema, so a database provisioned before Noust 3.1 (owned by postgres)
    fails its application's migrations. Shows the change; --apply makes it.
    """
    _exit(
        _fix_owner(
            name,
            engine=engine,
            owner=owner,
            apply=apply_changes,
            force=force,
            json_output=ctx.json_output,
            logger=ctx.logger,
        )
    )


@cli.command("user-create")
@click.argument("username")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to create the user on.")
@click.option("--password", "-p", help="Password. One is generated and shown when omitted.")
@click.option("--database", "-d", help="Database to give the new user access to.")
@click.option("--profile", type=PROFILE, help="Access profile on that database.")
@click.option(
    "--host", default="localhost", show_default=True, help="Host the user may connect from."
)
@pass_context
def user_create(
    ctx: Context,
    username: str,
    engine: str,
    password: str | None,
    database: str | None,
    profile: str | None,
    host: str,
) -> None:
    """Create a database user, optionally with a profile on one database."""
    _exit(
        _user_create(
            username,
            engine=engine,
            password=password,
            database=database,
            host=host,
            logger=ctx.logger,
            profile=profile,
        )
    )


@cli.command("user-delete")
@click.argument("username")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the user is on.")
@click.option(
    "--host", default="localhost", show_default=True, help="Host the user was created for."
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def user_delete(ctx: Context, username: str, engine: str, host: str, force: bool) -> None:
    """Delete a database user."""
    _exit(_user_delete(username, engine=engine, host=host, force=force, logger=ctx.logger))


@cli.command("user-list")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to list the users of.")
@json_option("Print the user list as JSON.")
@pass_context
def user_list(ctx: Context, engine: str) -> None:
    """List the users of an engine."""
    _exit(_user_list(engine=engine, json_output=ctx.json_output, logger=ctx.logger))


@cli.command("user-password")
@click.argument("username")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the user is on.")
@click.option("--host", default="localhost", show_default=True, help="Host the user connects from.")
@click.option(
    "--no-propagate",
    is_flag=True,
    help="Do not give the new password to the applications that sign in as the user.",
)
@click.option(
    "--first-password",
    is_flag=True,
    help="Give a Redis instance that has no password its first one; every client "
    "connecting without one gets NOAUTH.",
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def user_password(
    ctx: Context,
    username: str,
    engine: str,
    host: str,
    no_propagate: bool,
    first_password: bool,
    force: bool,
) -> None:
    """
    Give a user a new password, and the applications that use it too.

    Each application restarts behind its health gate; one that does not come
    back undoes the whole rotation. 'default' rotates Redis's requirepass.
    """
    _exit(
        _user_password(
            username,
            engine=engine,
            host=host,
            propagate=not no_propagate,
            force=force,
            logger=ctx.logger,
            first_password=first_password,
        )
    )


@cli.command()
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--user", "-u", "username", help="Account whose access to change.")
@click.option("--profile", type=PROFILE, help="The account's new profile.")
@click.option("--host", default="localhost", show_default=True, help="The account's host.")
@json_option("Print the access list as JSON.")
@pass_context
def access(
    ctx: Context,
    database: str,
    engine: str,
    username: str | None,
    profile: str | None,
    host: str,
) -> None:
    """List who can reach a database, or give one account a profile on it."""
    _exit(
        _access(
            database,
            engine=engine,
            username=username,
            profile=profile,
            host=host,
            json_output=ctx.json_output,
            logger=ctx.logger,
        )
    )


@cli.command()
@click.argument("username")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--privileges", help="Comma-separated privileges. Defaults to the engine's full set.")
@click.option("--host", default="localhost", show_default=True, help="Host the grant applies to.")
@pass_context
def grant(
    ctx: Context,
    username: str,
    database: str,
    engine: str,
    privileges: str | None,
    host: str,
) -> None:
    """Grant a user privileges on a database."""
    _exit(
        _grant(
            username, database, engine=engine, privileges=privileges, host=host, logger=ctx.logger
        )
    )


@cli.command()
@click.argument("username")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--privileges", help="Comma-separated privileges. Defaults to the engine's full set.")
@click.option("--host", default="localhost", show_default=True, help="Host the grant applies to.")
@pass_context
def revoke(
    ctx: Context,
    username: str,
    database: str,
    engine: str,
    privileges: str | None,
    host: str,
) -> None:
    """Take privileges away from a user."""
    _exit(
        _revoke(
            username, database, engine=engine, privileges=privileges, host=host, logger=ctx.logger
        )
    )


@cli.command()
@click.argument("domain")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--user", "-u", "username", help="Account to sign in as. Noust must know it.")
@click.option("--env-var", help="Variable to write. DATABASE_URL, or REDIS_URL for Redis.")
@click.option("--extra-vars", is_flag=True, help="Also write DB_HOST, DB_PORT, DB_NAME, ...")
@click.option("--no-restart", is_flag=True, help="Write the variables without restarting.")
@pass_context
def link(
    ctx: Context,
    domain: str,
    database: str,
    engine: str,
    username: str | None,
    env_var: str | None,
    extra_vars: bool,
    no_restart: bool,
) -> None:
    """Give an application a database's connection string, and restart it."""
    _exit(
        _link(
            domain,
            database,
            engine=engine,
            username=username,
            env_var=env_var,
            extra_vars=extra_vars,
            restart=not no_restart,
            logger=ctx.logger,
        )
    )


@cli.command()
@click.argument("domain")
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--drop", "drop_it", is_flag=True, help="Also drop the database, after a last dump.")
@click.option("--no-restart", is_flag=True, help="Remove the variables without restarting.")
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def unlink(
    ctx: Context,
    domain: str,
    database: str,
    engine: str,
    drop_it: bool,
    no_restart: bool,
    force: bool,
) -> None:
    """Take a database away from an application."""
    _exit(
        _unlink(
            domain,
            database,
            engine=engine,
            drop=drop_it,
            restart=not no_restart,
            force=force,
            logger=ctx.logger,
        )
    )


@cli.command(read_only=True)
@click.argument("domain")
@json_option("Print the list as JSON.")
@pass_context
def links(ctx: Context, domain: str) -> None:
    """List the databases an application uses."""
    _exit(_links(domain, json_output=ctx.json_output, logger=ctx.logger))


@cli.command()
@click.argument("domain")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to create it on.")
@click.option("--name", help="Database name. Derived from the application by default.")
@click.option("--env-var", help="Variable to write. DATABASE_URL, or REDIS_URL for Redis.")
@click.option("--extra-vars", is_flag=True, help="Also write DB_HOST, DB_PORT, DB_NAME, ...")
@click.option("--no-restart", is_flag=True, help="Write the variables without restarting.")
@pass_context
def provision(
    ctx: Context,
    domain: str,
    engine: str,
    name: str | None,
    env_var: str | None,
    extra_vars: bool,
    no_restart: bool,
) -> None:
    """Create a database and an account for an application, and link them."""
    _exit(
        _provision(
            domain,
            engine=engine,
            name=name,
            env_var=env_var,
            extra_vars=extra_vars,
            restart=not no_restart,
            logger=ctx.logger,
        )
    )


@cli.command()
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Where to write the file. Defaults to the engine's backup directory.",
)
@click.option("--no-compress", is_flag=True, help="Write the dump without gzip.")
@click.option(
    "--format",
    "dump_format",
    type=click.Choice(["custom", "plain", "tar"]),
    help="PostgreSQL's dump format. custom (pg_dump -Fc) by default.",
)
@pass_context
def backup(
    ctx: Context,
    database: str,
    engine: str,
    output: Path | None,
    no_compress: bool,
    dump_format: str | None,
) -> None:
    """Write a database to a backup file."""
    _exit(
        _backup(
            database,
            engine=engine,
            output=output,
            compress=not no_compress,
            logger=ctx.logger,
            dump_format=dump_format,
        )
    )


@cli.command()
@click.argument("database")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--drop",
    is_flag=True,
    help="Drop the database before restoring it. A safety copy is taken first and put back "
    "if the restore fails.",
)
@click.option("--as-new", "new_name", help="Restore into a new database of this name instead.")
@click.option(
    "--no-safety-copy",
    is_flag=True,
    help="Do not dump the database first when nothing is dropped.",
)
@click.option("--force", "-f", "-y", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def restore(
    ctx: Context,
    database: str,
    file: Path,
    engine: str,
    drop: bool,
    new_name: str | None,
    no_safety_copy: bool,
    force: bool,
) -> None:
    """Load a backup into a database, never losing what it held."""
    _exit(
        _restore(
            database,
            file,
            engine=engine,
            drop_existing=drop,
            force=force,
            logger=ctx.logger,
            new_name=new_name,
            safety_backup=not no_safety_copy,
        )
    )


@cli.command()
@click.option("--engine", "-e", type=ENGINE, help="Only this engine. Defaults to all of them.")
@click.option("--database", "-d", help="Only backups of this database.")
@json_option("Print the backup list as JSON.")
@pass_context
def backups(ctx: Context, engine: str | None, database: str | None) -> None:
    """List the database backups on this server."""
    _exit(
        _backups(
            engine=engine,
            database=database,
            json_output=ctx.json_output,
            logger=ctx.logger,
        )
    )


@cli.command()
@click.argument("database")
@click.argument("query")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option(
    "--write",
    is_flag=True,
    help="Allow the statement to change data. Without it the engine runs one statement "
    "as the database's read-only account.",
)
@pass_context
def query(ctx: Context, database: str, query: str, engine: str, write: bool) -> None:
    """
    Run one statement against a database, read-only unless told otherwise.

    Read-only mode takes a single statement: an embedded ';' can close the
    transaction the engine was asked to hold it in.
    """
    _exit(_query(database, query, engine=engine, read_only=not write, logger=ctx.logger))


@cli.command()
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine to connect to.")
@click.option("--database", "-d", help="Database to open.")
@click.option("--username", "-u", help="User to connect as.")
@pass_context
def connect(ctx: Context, engine: str, database: str | None, username: str | None) -> None:
    """Open a session with the engine's own client."""
    _exit(
        _connect(
            engine=engine,
            database=database,
            username=username,
            dry_run=ctx.dry_run,
            logger=ctx.logger,
        )
    )


@cli.command("connect-info", read_only=True)
@click.argument("database")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--username", "-u", help="Account to connect as.")
@click.option("--server", help="This server's address as you reach it. Detected by default.")
@click.option("--ssh-user", help="Account to sign in to the server as. root by default.")
@json_option("Print everything as JSON.")
@pass_context
def connect_info(
    ctx: Context,
    database: str,
    engine: str,
    username: str | None,
    server: str | None,
    ssh_user: str | None,
) -> None:
    """Show the SSH tunnel to reach a database from your computer."""
    _exit(
        _connect_info(
            database,
            engine=engine,
            username=username,
            server=server,
            ssh_user=ssh_user,
            json_output=ctx.json_output,
            logger=ctx.logger,
        )
    )


@cli.command(read_only=True)
@json_option("Print the open ports as JSON.")
@pass_context
def exposure(ctx: Context) -> None:
    """List database ports open beyond this machine; exits 1 when there is one."""
    _exit(_exposure(json_output=ctx.json_output, logger=ctx.logger))


@cli.command("connection-string")
@click.argument("database")
@click.argument("username")
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the database is on.")
@click.option("--password", "-p", help="Password. A placeholder is printed when omitted.")
@click.option("--host", default="localhost", show_default=True, help="Host the application uses.")
@pass_context
def connection_string(
    ctx: Context,
    database: str,
    username: str,
    engine: str,
    password: str | None,
    host: str,
) -> None:
    """Print a connection string for an application to use."""
    _exit(
        _connection_string(
            database,
            username,
            engine=engine,
            password=password,
            host=host,
            logger=ctx.logger,
        )
    )


@cli.command()
@click.option("--engine", "-e", type=ENGINE, required=True, help="Engine the credentials are for.")
@click.option("--user", "-u", help="Administrative user, such as root.")
@click.option(
    "--password",
    "-p",
    is_flag=False,
    flag_value=_ASK,
    help="Administrative password. Alone, it is asked for without echo, so it never "
    "reaches the shell history or the process list.",
)
@pass_context
def config(ctx: Context, engine: str, user: str | None, password: str | None) -> None:
    """Store the account Noust signs in to an engine with, after trying it."""
    if password == _ASK:
        password = click.prompt("Password", hide_input=True)
    _exit(_config(engine=engine, user=user, password=password, logger=ctx.logger))


def _attach_backup_commands() -> None:
    """
    Add the backup policy commands (``backup-schedule``, ``backup-run``...).

    They live in their own module, which does not import this one at load
    time, so the order the two are imported in never matters.
    """
    from noust.cli.commands import db_backups

    db_backups.register(cli)


_attach_backup_commands()


def _attach_data_commands() -> None:
    """
    Add the data explorer, console and metrics commands (``rows``, ``explain``...).

    They live in :mod:`noust.cli.commands.db_data`, which imports this module
    only at call time, like the backup commands.
    """
    from noust.cli.commands import db_data

    db_data.register(cli)


_attach_data_commands()
