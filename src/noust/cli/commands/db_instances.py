# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db instance``: engines Noust runs itself, in containers, beside the host's.

The host's PostgreSQL, MySQL, Redis and MongoDB stay the server's primary
engines. An instance is another one: another major version (PostgreSQL 16 beside
17), the other flavour (MariaDB beside MySQL), or an engine the host does not
provide, run from the engine's official image pinned by digest, published on
127.0.0.1 only and held to a memory limit. Once it exists it is an engine like
the others: it is addressed as ``postgresql@pg16`` wherever ``--engine`` is
taken, and every ``noust db`` command works on it. This group is only its own
life: make it, list it, start and stop it, move it to a newer image of its
version, move its databases to a newer major version (``migrate``) and back
(``rollback``), remove it. ``noust db move`` moves one database between engines.
The work is
:class:`noust.managers.database.managed.ManagedInstances`, which the console's API
calls too; this module parses arguments and prints.

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls
:func:`register`, so the commands join its group without this module importing
that one at load time.
"""

from __future__ import annotations

import json
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.db_instance_store import TLS_MODES
from noust.core.exceptions import ConfirmationRequired
from noust.core.fs import is_rehearsal
from noust.core.logger import Logger
from noust.managers.database.instance_catalog import ALIASES, SPECS, offered
from noust.managers.database.instance_migration import InstanceMigrations, MigrationPlan
from noust.managers.database.instance_tls import InstanceTls, TlsPlan
from noust.managers.database.instance_tuning import InstanceTuning, TuningPlan
from noust.managers.database.managed import InstancePlan, ManagedInstances
from noust.managers.database.moves import DatabaseMover, MovePlan
from noust.managers.database.tls_authority import TlsAuthority

#: Every name an operator may type for what to run.
ENGINES = click.Choice([*SPECS, *ALIASES], case_sensitive=False)


def _managed(ctx: Context) -> ManagedInstances:
    """
    Build the class every command here uses.

    Args:
        ctx: The invocation's context.

    Returns:
        The manager over ``noust db``'s service, so an engine resolves the way
        every ``noust db`` command resolves it. With ``--json`` its progress goes
        to stderr, so what is on stdout is the JSON alone.
    """
    from noust.cli.commands.db import _service

    logger = ctx.logger
    if ctx.json_output:
        logger = Logger(verbose=ctx.logger.verbose, stream=sys.stderr)
    return ManagedInstances(_service(logger))


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _show_plan(ctx: Context, plan: InstancePlan) -> None:
    """
    Print what making an instance would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Instance", f"{plan.name} ({plan.display_name} {plan.version})")
    logger.key_value("Image", f"{plan.image}:{plan.version}@{plan.digest}")
    logger.key_value("Listens on", f"127.0.0.1:{plan.host_port}")
    logger.key_value("Memory limit", f"{plan.memory_limit_mb} MB")
    logger.key_value("Addressed as", plan.key)
    for warning in plan.warnings:
        logger.warning(warning)


def _row(entry: dict[str, Any]) -> list[str]:
    """
    Args:
        entry: An instance, as :meth:`ManagedInstances.describe` says.

    Returns:
        The cells of its line in the listing.
    """
    state = entry["status"] if entry["state"] == "ready" else entry["state"]
    return [
        entry["name"],
        f"{entry['display_name']} {entry['version']}",
        f"127.0.0.1:{entry['host_port']}",
        f"{entry['memory_limit_mb']} MB",
        state,
        entry["key"],
    ]


@click.group("instance", cls=NoustGroup)
def instance() -> None:
    """Run another version of an engine, or an engine the host lacks, in a container."""


@instance.command("list", read_only=True)
@global_flags
@json_option("Print the instances and the memory they hold as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the instances Noust runs, and how much of the server's memory they hold."""
    managed = _managed(ctx)
    entries = managed.all()
    budget = managed.budget()
    if ctx.json_output:
        _echo_json({"instances": entries, "budget": budget})
        return
    if not entries:
        ctx.logger.info(
            "No instances. Make one with: noust db instance create NAME --engine ENGINE "
            "--version VERSION --memory 512m"
        )
        return
    ctx.logger.table(
        ["Name", "Engine", "Listens on", "Memory", "State", "Key"], [_row(e) for e in entries]
    )
    if budget["server_mb"]:
        ctx.logger.info(
            f"Memory limits add up to {budget['allocated_mb']} MB of the "
            f"{budget['allocatable_mb']} MB instances may hold ({budget['available_mb']} MB left)."
        )


@instance.command("catalog", read_only=True)
@global_flags
@json_option("Print the catalog as JSON.")
@pass_context
def catalog_command(ctx: Context) -> None:
    """List the engines Noust can run as an instance, and the versions it offers."""
    entries = offered()
    if ctx.json_output:
        _echo_json({"engines": entries})
        return
    rows = []
    for entry in entries:
        versions = ", ".join(
            item["version"] + (" (default)" if item["default"] else "")
            for item in entry["versions"]
        )
        rows.append(
            [
                entry["flavour"],
                entry["display_name"],
                entry["image"],
                f"{entry['min_memory_mb']} MB",
                versions,
            ]
        )
    ctx.logger.table(["Engine", "Name", "Official image", "Least memory", "Versions"], rows)
    ctx.logger.info(
        "Make one: noust db instance create NAME --engine ENGINE --version VERSION --memory 512m"
    )


@instance.command("create")
@click.argument("name")
@click.option(
    "--engine", "-e", type=ENGINES, required=True, help="What to run: a flavour of the catalog."
)
@click.option(
    "--version",
    metavar="VERSION",
    help="Its version, such as 16 or 8.4 ('noust db instance catalog'). The default one when omitted.",
)
@click.option(
    "--memory",
    metavar="SIZE",
    required=True,
    help="Its memory limit, such as 512m or 2g. Mandatory: the container is held to it and the "
    "engine sizes its caches to it.",
)
@click.option(
    "--port",
    type=int,
    help="The port on 127.0.0.1. A free one just above the engine's own when omitted.",
)
@click.option("--yes", "-y", is_flag=True, help="Do not ask before making it.")
@global_flags
@json_option("Print the instance as JSON.")
@pass_context
def create_command(
    ctx: Context,
    name: str,
    engine: str,
    version: str | None,
    memory: str,
    port: int | None,
    yes: bool,
) -> None:
    """
    Make an instance NAME and start it.

    It runs from the engine's official image pinned by tag and digest, is
    published on 127.0.0.1 only, and gets a superuser whose password Noust
    generates and keeps where only the container reads it. It is then addressed
    as ENGINE@NAME.
    """
    managed = _managed(ctx)
    plan = managed.plan(name, engine, version=version, memory=memory, port=port)
    if is_rehearsal():
        ctx.logger.info(f"Would make {plan.name} ({plan.display_name} {plan.version})")
        _show_plan(ctx, plan)
        return
    if not yes and not ctx.json_output:
        _show_plan(ctx, plan)
        click.confirm("Make it?", default=True, abort=True)
    described = managed.create(name, engine, version=version, memory=memory, port=port)
    if ctx.json_output:
        _echo_json(described)
        return
    ctx.logger.success(f"{described['name']} runs on 127.0.0.1:{described['host_port']}")
    ctx.logger.info(
        f"Use it as --engine {described['key']}: noust db create SHOP --engine {described['key']}"
    )


@instance.command("show", read_only=True)
@click.argument("name")
@global_flags
@json_option("Print the instance as JSON.")
@pass_context
def show_command(ctx: Context, name: str) -> None:
    """Show one instance: its image, its port, its memory, where its files are."""
    managed = _managed(ctx)
    described = managed.describe(managed.get(name))
    if ctx.json_output:
        _echo_json(described)
        return
    logger = ctx.logger
    logger.key_value(
        "Instance", f"{described['name']} ({described['display_name']} {described['version']})"
    )
    logger.key_value("Key", described["key"])
    logger.key_value("State", f"{described['state']}, {described['status']}")
    logger.key_value("Image", described["reference"])
    logger.key_value("Listens on", f"{described['address']}:{described['host_port']}")
    logger.key_value("Memory limit", f"{described['memory_limit_mb']} MB")
    logger.key_value(
        "CPUs", "no limit" if described["cpu_limit"] is None else f"{described['cpu_limit']:g}"
    )
    logger.key_value(
        "Swap",
        "Docker's default"
        if described["swap_limit_mb"] is None
        else f"{described['swap_limit_mb']} MB",
    )
    logger.key_value("TLS", described["tls_mode"])
    logger.key_value("Unit", described["unit"])
    logger.key_value("Data volume", described["volume"])
    logger.key_value("Files", described["directory"])
    if described["end_of_life"]:
        logger.key_value("Upstream support ends", described["end_of_life"])


@instance.command("start")
@click.argument("name")
@global_flags
@json_option("Print the instance as JSON.")
@pass_context
def start_command(ctx: Context, name: str) -> None:
    """Start an instance and wait until its engine answers."""
    described = _managed(ctx).start(name)
    if ctx.json_output:
        _echo_json(described)
        return
    ctx.logger.success(f"{name} is running")


@instance.command("stop")
@click.argument("name")
@global_flags
@json_option("Print the instance as JSON.")
@pass_context
def stop_command(ctx: Context, name: str) -> None:
    """Stop an instance. Its data stays; the applications that use it lose their database."""
    described = _managed(ctx).stop(name)
    if ctx.json_output:
        _echo_json(described)
        return
    ctx.logger.success(f"{name} is stopped; 'noust db instance start {name}' brings it back")


@instance.command("upgrade")
@click.argument("name")
@click.option(
    "--no-backup",
    is_flag=True,
    help="Do not dump every database first. Only when its data does not matter.",
)
@global_flags
@json_option("Print what changed as JSON.")
@pass_context
def upgrade_command(ctx: Context, name: str, no_backup: bool) -> None:
    """
    Move an instance to the newest image of its version.

    The version does not change (PostgreSQL 17 stays 17): what moves is the build
    the registry serves for it. Every database is dumped first, the instance must
    become healthy, and when it does not the image it ran is put back.
    """
    outcome = _managed(ctx).upgrade(name, backup=not no_backup)
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    if not outcome.changed:
        ctx.logger.info(f"{name} already runs the newest image of its version")
        return
    ctx.logger.success(f"{name} runs {outcome.to_digest}")
    ctx.logger.info(f"It ran {outcome.from_digest}; the dumps taken first: {len(outcome.dumps)}")


def _words(value: str | None, name: str, clear: list[str], *, words: tuple[str, ...]) -> str | None:
    """
    Read an option that may also say "put the default back".

    Args:
        value: What the operator typed, or None.
        name: The setting, as :data:`~noust.managers.database.instance_tuning.CLEARABLE` names it.
        clear: Where the setting is added when the operator asked for its default.
        words: The words that ask for it.

    Returns:
        The value, or None when it was not given or was a request for the default.
    """
    if value is None:
        return None
    if value.strip().lower() in words:
        clear.append(name)
        return None
    return value


def _show_tuning(ctx: Context, status: dict[str, Any]) -> None:
    """
    Print what an instance is held to and how it is probed.

    Args:
        ctx: The invocation's context.
        status: What :meth:`InstanceTuning.status` returned.
    """
    logger = ctx.logger
    limits, probe, health = status["limits"], status["probe"], status["health"]
    logger.key_value("Instance", status["name"])
    logger.key_value("Memory limit", f"{limits['memory_mb']} MB")
    logger.key_value("CPUs", "no limit" if limits["cpus"] is None else f"{limits['cpus']:g}")
    logger.key_value(
        "Swap", "Docker's default" if limits["swap_mb"] is None else f"{limits['swap_mb']} MB"
    )
    logger.key_value(
        "Health probe",
        f"every {probe['interval_s']} s, {probe['timeout_s']} s to answer, "
        f"unhealthy after {probe['retries']} failures in a row",
    )
    logger.key_value("Health now", health["status"])
    if health["failing_streak"]:
        logger.key_value("Failures in a row", str(health["failing_streak"]))
    if health["last"] and health["last"]["output"]:
        logger.key_value("Last probe said", health["last"]["output"])


def _show_tuning_plan(ctx: Context, plan: TuningPlan) -> None:
    """
    Print what a change of limits or probe would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Instance", plan.name)
    for change in plan.changes:
        before = "default" if change["from"] is None else change["from"]
        after = "default" if change["to"] is None else change["to"]
        logger.key_value(change["setting"].capitalize(), f"{before} -> {after}")
    if plan.apps:
        logger.key_value("Applications", ", ".join(plan.apps))
    for warning in plan.warnings:
        logger.warning(warning)
    for blocker in plan.blockers:
        logger.error(blocker)


def _tune(
    ctx: Context,
    name: str,
    *,
    plan_only: bool,
    yes: bool,
    clear: list[str],
    **wanted: str | None,
) -> None:
    """
    Show, plan or apply a change of limits or of the probe: what both commands share.

    Args:
        ctx: The invocation's context.
        name: The instance.
        plan_only: Say what it would do and stop.
        yes: Do not ask before restarting the engine.
        clear: Settings to put back to their default.
        **wanted: The settings given, as :meth:`InstanceTuning.plan` takes them.
    """
    tuning = InstanceTuning(_managed(ctx))
    if not any(value is not None for value in wanted.values()) and not clear:
        status = tuning.status(name)
        if ctx.json_output:
            _echo_json(status)
        else:
            _show_tuning(ctx, status)
        return
    plan = tuning.plan(name, clear=clear, **wanted)
    if plan_only or is_rehearsal():
        if ctx.json_output:
            _echo_json(plan.to_dict())
        else:
            _show_tuning_plan(ctx, plan)
        if plan.blockers:
            raise click.exceptions.Exit(1)
        return
    if plan.blockers:
        _show_tuning_plan(ctx, plan)
        raise click.ClickException("It cannot go ahead: see what is listed above.")
    if not plan.changes:
        ctx.logger.info(f"{name} already has these settings")
        return
    if not yes and not ctx.json_output:
        _show_tuning_plan(ctx, plan)
        click.confirm(f"Restart {name} on these settings?", default=False, abort=True)
    status = tuning.apply(name, clear=clear, **wanted)
    if ctx.json_output:
        _echo_json(status)
        return
    ctx.logger.success(f"{name} runs on the new settings")


@instance.command(
    "limits",
    read_only=lambda params: (
        bool(params.get("plan_only"))
        or all(params.get(key) is None for key in ("memory", "cpus", "swap"))
    ),
)
@click.argument("name")
@click.option(
    "--memory", metavar="SIZE", help="Its memory limit, such as 2g. The engine's caches follow."
)
@click.option(
    "--cpus", metavar="N", help="How many CPUs it may use, such as 1.5. 'none' lifts the limit."
)
@click.option(
    "--swap",
    metavar="SIZE",
    help="Swap it may use on top of its memory: 0 for none, or 512m. 'default' leaves Docker's.",
)
@click.option("--plan", "plan_only", is_flag=True, help="Only say what it would do.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting it.")
@global_flags
@json_option("Print the settings, the plan or what was done as JSON.")
@pass_context
def limits_command(
    ctx: Context,
    name: str,
    memory: str | None,
    cpus: str | None,
    swap: str | None,
    plan_only: bool,
    yes: bool,
) -> None:
    """
    Show or change what an instance may use: memory, CPUs and swap.

    Docker applies a container's limits when it is made, so the engine restarts, behind the
    same readiness gate a creation passes; when it does not come up the previous limits are put
    back. Without options it shows the limits and how the instance is doing.
    """
    clear: list[str] = []
    _tune(
        ctx,
        name,
        plan_only=plan_only,
        yes=yes,
        clear=clear,
        memory=memory,
        cpus=_words(cpus, "cpus", clear, words=("none", "unlimited", "default")),
        swap=_words(swap, "swap", clear, words=("default",)),
    )


@instance.command(
    "health",
    read_only=lambda params: (
        bool(params.get("plan_only"))
        or all(params.get(key) is None for key in ("interval", "timeout", "retries"))
    ),
)
@click.argument("name")
@click.option("--interval", metavar="SECONDS", help="Seconds between probes. 'default' is 10.")
@click.option("--timeout", metavar="SECONDS", help="Seconds a probe may take. 'default' is 5.")
@click.option(
    "--retries",
    metavar="N",
    help="Failed probes in a row before the engine is unhealthy. 'default' is 12.",
)
@click.option("--plan", "plan_only", is_flag=True, help="Only say what it would do.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting it.")
@global_flags
@json_option("Print the settings, the plan or what was done as JSON.")
@pass_context
def health_command(
    ctx: Context,
    name: str,
    interval: str | None,
    timeout: str | None,
    retries: str | None,
    plan_only: bool,
    yes: bool,
) -> None:
    """
    Show or change how an instance is asked whether it is well.

    Without options it shows the probe and what Docker says of the engine now (with the last
    probe's own output). A change restarts the engine, behind the readiness gate. A probe may
    not take longer than the time between probes.
    """
    clear: list[str] = []
    _tune(
        ctx,
        name,
        plan_only=plan_only,
        yes=yes,
        clear=clear,
        interval=_words(interval, "interval", clear, words=("default",)),
        timeout=_words(timeout, "timeout", clear, words=("default",)),
        retries=_words(retries, "retries", clear, words=("default",)),
    )


@instance.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Accept that its data is deleted, without asking.")
@global_flags
@json_option("Print what was removed as JSON.")
@pass_context
def remove_command(ctx: Context, name: str, yes: bool) -> None:
    """
    Remove an instance, its container, its volumes and its data.

    Applications that use it, and databases made public on it, stop the removal.
    Dumps already taken stay where they are. A removal that could not finish is
    finished by running the same command again.
    """
    managed = _managed(ctx)
    plan = managed.plan_remove(name)
    if is_rehearsal():
        ctx.logger.info(f"Would remove {name}: {'; '.join(plan['warnings'])}")
        return
    if not yes and not plan["blockers"]:
        for warning in plan["warnings"]:
            ctx.logger.warning(warning)
        click.confirm(f"Remove {name} and its data?", default=False, abort=True)
    try:
        outcome = managed.remove(name, confirm=True)
    except ConfirmationRequired as exc:  # pragma: no cover - confirm=True never raises it
        raise click.Abort() from exc
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    ctx.logger.success(f"{name} is removed")
    if outcome.kept:
        ctx.logger.info("Kept: " + ", ".join(outcome.kept))


def _show_move_plan(ctx: Context, plan: MovePlan) -> None:
    """
    Print what moving a database would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Database", f"{plan.database} ({plan.size or 'size unknown'})")
    logger.key_value("From", f"{plan.source} ({plan.source_version or 'version unknown'})")
    logger.key_value("To", f"{plan.target} ({plan.target_version or 'version unknown'})")
    if plan.apps:
        logger.key_value("Restarts", ", ".join(plan.apps))
    if plan.accounts:
        logger.key_value("Accounts", ", ".join(plan.accounts))
    for warning in plan.warnings:
        logger.warning(warning)
    for blocker in plan.blockers:
        logger.error(blocker)


def _show_migration_plan(ctx: Context, plan: MigrationPlan) -> None:
    """
    Print what moving an engine to a newer version would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value(
        "Engine",
        f"{plan.source}: {plan.display_name} {plan.from_version or '?'} to {plan.to_version}",
    )
    logger.key_value("New instance", f"{plan.target} ({plan.new_instance['key']})")
    logger.key_value("Listens on", f"127.0.0.1:{plan.new_instance['host_port']}")
    logger.key_value("Memory limit", f"{plan.new_instance['memory_limit_mb']} MB")
    names = ", ".join(entry["database"] for entry in plan.databases) or "none"
    logger.key_value("Databases moved", names)
    if plan.apps:
        logger.key_value("Applications restarted", ", ".join(plan.apps))
    for warning in plan.warnings:
        logger.warning(warning)
    for blocker in plan.blockers:
        logger.error(blocker)


@instance.command("migrate")
@click.argument("source")
@click.option(
    "--to-version",
    "to_version",
    metavar="VERSION",
    required=True,
    help="The version to go to, such as 17 ('noust db instance catalog'). Newer than the one "
    "it leaves.",
)
@click.option(
    "--name", help="What to call the new instance. The engine and its version when omitted."
)
@click.option(
    "--memory",
    metavar="SIZE",
    help="Its memory limit, such as 1g. The old instance's, or the engine's suggestion, when "
    "omitted.",
)
@click.option("--port", type=int, help="The port on 127.0.0.1. A free one when omitted.")
@click.option("--plan", "plan_only", is_flag=True, help="Only say what it would do.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before moving.")
@global_flags
@json_option("Print the plan or what was done as JSON.")
@pass_context
def migrate_command(
    ctx: Context,
    source: str,
    to_version: str,
    name: str | None,
    memory: str | None,
    port: int | None,
    plan_only: bool,
    yes: bool,
) -> None:
    """
    Move an engine's databases to a new instance of a newer major version.

    SOURCE is an instance (pg16) or the host's engine (postgresql). A new
    instance of the newer version is made, every database is dumped, loaded into
    it and the applications that use them are pointed at it behind their health
    gate. SOURCE is left as it was: 'noust db instance rollback' goes back, and
    removing the old instance is yours to do once the new one has proved itself.
    A failure at any point puts everything back.
    """
    migrations = InstanceMigrations(_managed(ctx))
    plan = migrations.plan(source, to_version, name=name, memory=memory, port=port)
    if plan_only or is_rehearsal():
        if ctx.json_output:
            _echo_json(plan.to_dict())
        else:
            _show_migration_plan(ctx, plan)
        if plan.blockers:
            raise click.exceptions.Exit(1)
        return
    if plan.blockers:
        _show_migration_plan(ctx, plan)
        raise click.ClickException("It cannot go ahead: see what is listed above.")
    if not yes and not ctx.json_output:
        _show_migration_plan(ctx, plan)
        click.confirm("Move it?", default=False, abort=True)
    outcome = migrations.run(source, to_version, name=name, memory=memory, port=port)
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    ctx.logger.success(
        f"{len(outcome.moved)} database(s) are on {outcome.target}; the move is number "
        f"{outcome.migration}"
    )
    for warning in outcome.warnings:
        ctx.logger.warning(warning)
    ctx.logger.info(f"Kept for the way back: {'; '.join(outcome.kept)}")
    ctx.logger.info(f"Go back with: noust db instance rollback {outcome.migration}")


@instance.command("migrations", read_only=True)
@global_flags
@json_option("Print the moves as JSON.")
@pass_context
def migrations_command(ctx: Context) -> None:
    """List the moves to a newer version, newest first, and which can be rolled back."""
    entries = InstanceMigrations(_managed(ctx)).all()
    if ctx.json_output:
        _echo_json({"migrations": entries})
        return
    if not entries:
        ctx.logger.info(
            "No moves yet. Start one with: noust db instance migrate SOURCE --to-version N"
        )
        return
    ctx.logger.table(
        ["Number", "From", "To", "State", "Databases", "Rollback"],
        [
            [
                str(entry["id"]),
                f"{entry['source']} {entry['from_version'] or ''}".strip(),
                f"{entry['target']} {entry['to_version']}",
                entry["state"],
                str(len(entry["moved"])),
                "yes" if entry["can_roll_back"] else "no",
            ]
            for entry in entries
        ],
    )


@instance.command("rollback")
@click.argument("migration")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before going back.")
@global_flags
@json_option("Print what was done as JSON.")
@pass_context
def rollback_command(ctx: Context, migration: str, yes: bool) -> None:
    """
    Point the applications back at the engine a move left.

    MIGRATION is the move's number ('noust db instance migrations') or the name
    of the instance it made. What was written to the new instance since the move
    is dumped first and is not carried back: an older server cannot load a newer
    one's dump. The new instance is stopped, not removed.
    """
    migrations = InstanceMigrations(_managed(ctx))
    if is_rehearsal():
        ctx.logger.info(f"Would point the applications of move {migration} back")
        return
    if not yes and not ctx.json_output:
        click.confirm(
            "Go back? What was written to the new instance since the move is not carried back.",
            default=False,
            abort=True,
        )
    outcome = migrations.rollback(migration)
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    ctx.logger.success(
        f"{len(outcome.repointed)} database(s) are on {outcome.source} again; "
        f"{outcome.target} is stopped"
    )
    for warning in outcome.warnings:
        ctx.logger.warning(warning)


def _show_tls(ctx: Context, status: dict[str, Any]) -> None:
    """
    Print how an instance serves TLS.

    Args:
        ctx: The invocation's context.
        status: What :meth:`InstanceTls.status` returned.
    """
    logger = ctx.logger
    logger.key_value("Instance", status["name"])
    logger.key_value("TLS", status["mode"])
    certificate = status["certificate"]
    if certificate:
        logger.key_value("Certificate for", ", ".join(certificate["names"]))
        logger.key_value(
            "Valid until", f"{certificate['not_after']} ({certificate['days_left']} days)"
        )
        logger.key_value("Fingerprint", certificate["fingerprint"])
        if certificate["expiring"]:
            logger.warning(f"It expires soon: noust db instance renew-tls {status['name']}")
    authority = status["ca"]
    if authority:
        logger.key_value("Authority", authority["path"])
    for mode, why in status["supported"].items():
        if why:
            logger.info(f"  {mode}: {why}")


def _show_tls_plan(ctx: Context, plan: TlsPlan) -> None:
    """
    Print what changing an instance's TLS would do.

    Args:
        ctx: The invocation's context.
        plan: The plan.
    """
    logger = ctx.logger
    logger.key_value("Instance", plan.name)
    logger.key_value("TLS", f"{plan.current} -> {plan.mode}")
    if plan.names:
        logger.key_value("Certificate for", ", ".join(plan.names))
    if plan.apps:
        logger.key_value("Applications", ", ".join(plan.apps))
    for warning in plan.warnings:
        logger.warning(warning)
    for blocker in plan.blockers:
        logger.error(blocker)


@instance.command(
    "tls", read_only=lambda params: params.get("mode") is None or bool(params.get("plan_only"))
)
@click.argument("name")
@click.option(
    "--mode",
    type=click.Choice(list(TLS_MODES)),
    help="off, optional (TLS when the client asks) or required (nothing else gets in). "
    "Only shows how it serves TLS when omitted.",
)
@click.option("--plan", "plan_only", is_flag=True, help="Only say what it would do.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting it.")
@global_flags
@json_option("Print the status, the plan or what was done as JSON.")
@pass_context
def tls_command(ctx: Context, name: str, mode: str | None, plan_only: bool, yes: bool) -> None:
    """
    Show or change how an instance serves TLS.

    The certificate is signed by Noust's own authority and carries the names the
    instance answers to; applications verify it by linking with --tls verify-full.
    Changing the mode restarts the engine, and when it does not come up the
    previous configuration is put back.
    """
    tls = InstanceTls(_managed(ctx))
    if mode is None:
        status = tls.status(name)
        if ctx.json_output:
            _echo_json(status)
        else:
            _show_tls(ctx, status)
        return
    plan = tls.plan(name, mode)
    if plan_only or is_rehearsal():
        if ctx.json_output:
            _echo_json(plan.to_dict())
        else:
            _show_tls_plan(ctx, plan)
        if plan.blockers:
            raise click.exceptions.Exit(1)
        return
    if plan.blockers:
        _show_tls_plan(ctx, plan)
        raise click.ClickException("It cannot go ahead: see what is listed above.")
    if not yes and not ctx.json_output and plan.restarts:
        _show_tls_plan(ctx, plan)
        click.confirm(f"Restart {name} to serve TLS as {mode}?", default=False, abort=True)
    status = tls.set_mode(name, mode)
    if ctx.json_output:
        _echo_json(status)
        return
    ctx.logger.success(f"{name} serves TLS as {status['mode']}")


@instance.command("renew-tls")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before restarting it.")
@global_flags
@json_option("Print the new status as JSON.")
@pass_context
def renew_tls_command(ctx: Context, name: str, yes: bool) -> None:
    """
    Sign an instance's certificate again and restart it on the new one.

    Use it when a name changed or a key must not be kept, or when the certificate
    is about to expire.
    """
    if is_rehearsal():
        ctx.logger.info(f"Would sign a new certificate for {name} and restart it")
        return
    if not yes and not ctx.json_output:
        click.confirm(f"Restart {name} on a new certificate?", default=False, abort=True)
    status = InstanceTls(_managed(ctx)).renew(name)
    if ctx.json_output:
        _echo_json(status)
        return
    ctx.logger.success(f"{name} serves a new certificate")
    _show_tls(ctx, status)


@instance.command("tls-ca", read_only=True)
@global_flags
@json_option("Print the authority's certificate as JSON.")
@pass_context
def tls_ca_command(ctx: Context) -> None:
    """
    Show Noust's certificate authority, the one applications trust.

    Its public certificate is the only file handed to applications; its key stays
    with root. It is made the first time an instance turns TLS on.
    """
    found = TlsAuthority().existing()
    if ctx.json_output:
        _echo_json({"authority": found.to_dict() if found else None})
        return
    if found is None:
        ctx.logger.info(
            "No authority yet: it is made when an instance first serves TLS "
            "(noust db instance tls NAME --mode optional)"
        )
        return
    info = found.to_dict()
    ctx.logger.key_value("Certificate", info["path"])
    ctx.logger.key_value("Valid until", f"{info['not_after']} ({info['days_left']} days)")
    ctx.logger.key_value("Fingerprint", info["fingerprint"])


@click.command("move")
@click.argument("database")
@click.option(
    "--from", "source", required=True, help="The engine it is on, such as postgresql@pg16."
)
@click.option("--to", "target", required=True, help="The engine it goes to.")
@click.option(
    "--replace",
    is_flag=True,
    help="Replace a database of that name the target already holds, after a safety copy.",
)
@click.option(
    "--drop-source",
    is_flag=True,
    help="Drop it from the engine it leaves, after a last dump. It stays otherwise.",
)
@click.option("--plan", "plan_only", is_flag=True, help="Only say what it would do.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask before moving.")
@global_flags
@json_option("Print the plan or what was done as JSON.")
@pass_context
def move_command(
    ctx: Context,
    database: str,
    source: str,
    target: str,
    replace: bool,
    drop_source: bool,
    plan_only: bool,
    yes: bool,
) -> None:
    """
    Move DATABASE to another engine: another version, the host's or an instance.

    It is dumped, its accounts are made on the target with the passwords Noust
    keeps, it is loaded, and the applications that use it are pointed at the
    target behind their health gate. The engine it leaves is not touched, so
    whatever fails is put back.
    """
    from noust.cli.commands.db import _service

    logger = ctx.logger
    if ctx.json_output:
        logger = Logger(verbose=ctx.logger.verbose, stream=sys.stderr)
    mover = DatabaseMover(_service(logger))
    plan = mover.plan(source, database, target, replace=replace)
    if plan_only or is_rehearsal():
        if ctx.json_output:
            _echo_json(plan.to_dict())
        else:
            _show_move_plan(ctx, plan)
        if plan.blockers:
            raise click.exceptions.Exit(1)
        return
    if plan.blockers:
        _show_move_plan(ctx, plan)
        raise click.ClickException("It cannot go ahead: see what is listed above.")
    if not yes and not ctx.json_output:
        _show_move_plan(ctx, plan)
        click.confirm("Move it?", default=False, abort=True)
    outcome = mover.move(source, database, target, replace=replace, drop_source=drop_source)
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    ctx.logger.success(f"{database} is on {outcome.target}")
    for warning in outcome.warnings:
        ctx.logger.warning(warning)
    if outcome.new_passwords:
        ctx.logger.warning(
            "New passwords were made for: "
            + ", ".join(outcome.new_passwords)
            + ". Noust keeps them: 'noust db user-password --show' reveals one."
        )
    if not outcome.source_dropped:
        ctx.logger.info(
            f"{database} is still on {outcome.source}. Drop it there when you are done: "
            f"noust db drop {database} --engine {outcome.source}"
        )


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(instance)
    group.add_command(move_command)
