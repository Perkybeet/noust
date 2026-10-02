# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app``: settings of one deployed application that are not a deploy.

``migrate`` moves an in-place application onto the release layout; it is
:func:`noust.deployers.migrate.plan_migration` and
:func:`~noust.deployers.migrate.migrate`, which ``POST /api/apps/{d}/migrate``
calls too. ``limits`` sets the memory, CPU and task limits of its unit through
:func:`noust.deployers.lifecycle.set_resource_limits`, like ``PATCH
/api/apps/{d}/limits``. ``health`` sets what the health gate asks of it
through :func:`noust.deployers.lifecycle.set_health_check`, like ``PATCH
/api/apps/{d}/health``. ``zero-downtime`` shows or switches blue/green
activation through :mod:`noust.deployers.bluegreen`, like ``GET`` and ``PUT
/api/apps/{d}/zero-downtime``. ``export`` and ``import`` write and read an
application's definition through :mod:`noust.deployers.app_export`, like ``GET
/api/apps/{d}/export`` and ``POST /api/apps/import``; an import deploys through
``noust create``'s own path. This module only parses, presents and asks.
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.cli.commands.app_backup import backup_before_update_command
from noust.cli.commands.app_headless import headless_command
from noust.cli.commands.app_hooks import hooks as hooks_group
from noust.cli.commands.app_identity import identity as identity_group
from noust.cli.commands.app_sandbox import sandbox as sandbox_group
from noust.cli.commands.app_webhook import webhook as webhook_group
from noust.cli.commands.webapp import _create_app, _read_env_file
from noust.core.exceptions import DeploymentError, NoustError, ValidationError
from noust.core.fs import SECRET_MODE, get_fs
from noust.core.logger import Logger
from noust.core.store import MAX_DRAIN_SECONDS, App, DeploymentTrigger, get_store
from noust.deployers.app_export import (
    CreateSpec,
    ImportPlan,
    ImportReport,
    apply_import,
    dumps,
    export_app,
    load_document,
    plan_import,
    plan_summary,
    report_summary,
)
from noust.deployers.bluegreen import (
    ModeChange,
    ZeroDowntimeStatus,
    set_zero_downtime,
    zero_downtime_status,
)
from noust.deployers.helpers.health_gate import HealthCheck
from noust.deployers.lifecycle import (
    set_branch,
    set_follow_tags,
    set_health_check,
    set_resource_limits,
)
from noust.deployers.migrate import MigrationPlan, migrate, plan_migration
from noust.deployers.recorder import CapturingLogger
from noust.managers.service_manager import ResourceLimits

#: What removes a limit instead of setting one.
NO_LIMIT = frozenset({"none", "unlimited", "off"})


def print_plan(logger: Logger, plan: MigrationPlan) -> None:
    """
    Render a migration plan for a human.

    Args:
        logger: Logger the command writes through.
        plan: The plan.
    """
    logger.key_value("Application", f"{plan.domain} ({plan.app_path})")
    logger.key_value("First release", f"{plan.release_id} (the live tree, moved, not copied)")
    how = {
        "git": "what git does not track",
        "explicit": "as named",
        "common": "the usual upload directories that exist",
    }[plan.persistent_source]
    logger.key_value(
        "Moves to shared/", ", ".join([*plan.env_files, *plan.persistent]) or "nothing"
    )
    logger.key_value("Persistent paths", f"{', '.join(plan.persistent) or 'none'} ({how})")
    logger.key_value("Unit", "rewritten to run from current" if plan.unit_rewrite else "unchanged")
    logger.key_value("Site", "rewritten to serve current" if plan.site_rewrite else "unchanged")
    logger.key_value(
        "Files", f"{plan.count.files} ({plan.count.bytes} bytes), all kept, none copied"
    )
    for warning in plan.warnings:
        logger.warning(warning)


@click.group("app", cls=NoustGroup)
def cli() -> None:
    """Change how a deployed application is laid out and what it may use."""


cli.add_command(webhook_group)
cli.add_command(sandbox_group)
cli.add_command(identity_group)
cli.add_command(hooks_group)
cli.add_command(backup_before_update_command)
cli.add_command(headless_command)


@cli.command("migrate")
@click.argument("domain")
@click.option(
    "--persist",
    "persist",
    multiple=True,
    metavar="PATH",
    help="Keep this path in shared/ across releases. Repeat for each; replaces detection.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print the migration plan (or, with --yes, its result) as JSON.")
@pass_context
def migrate_command(ctx: Context, domain: str, persist: tuple[str, ...], yes: bool) -> None:
    """
    Move an in-place application onto the release layout.

    The live tree becomes the first release; the .env and every path the
    application writes for itself move to shared/; the unit and the site are
    rewritten to run from current. Nothing is deleted and nothing is copied.
    If the application does not answer afterwards, everything is put back as
    it was.
    """
    plan = plan_migration(domain, list(persist) if persist else None)
    if not ctx.json_output:
        print_plan(ctx.logger, plan)
    elif not (yes or ctx.dry_run):
        # A script asked for JSON without --yes: there is nobody to confirm,
        # so it gets the plan and nothing changes.
        click.echo(json.dumps({"plan": dataclasses.asdict(plan)}, default=str))
        return

    if not yes and not ctx.dry_run:
        click.confirm(f"Migrate {plan.domain} to releases?", abort=True)

    logger = CapturingLogger(verbose=ctx.verbose)
    result = migrate(domain, plan, trigger=DeploymentTrigger.CLI.value, logger=logger)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"plan": dataclasses.asdict(plan), "result": dataclasses.asdict(result)},
                default=str,
            )
        )
        return
    if result.rehearsed:
        logger.info("Rehearsal: nothing was changed")
        return
    logger.success(f"{result.domain} runs from release {result.release_id}")
    logger.info(
        f"Kept {result.after.files} files ({result.after.bytes} bytes); "
        f"see its releases with: noust releases list {result.domain}"
    )


def parse_memory(value: str) -> int | None:
    """
    Read a memory limit as an operator writes it.

    Args:
        value: ``512M``, ``512``, ``2G`` or ``none``.

    Returns:
        Megabytes, or None to remove the limit.

    Raises:
        click.BadParameter: It is none of those.
    """
    if value.strip().lower() in NO_LIMIT:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*([mMgG])?[bB]?\s*", value)
    if match is None:
        raise click.BadParameter(f"{value!r} is not a size; use 512M, 2G or none")
    amount = int(match.group(1))
    return amount * 1024 if (match.group(2) or "M").upper() == "G" else amount


def parse_cpu(value: str) -> int | None:
    """
    Read a CPU quota as an operator writes it.

    Args:
        value: ``50%``, ``50``, ``200%`` (two CPUs) or ``none``.

    Returns:
        Percent of one CPU, or None to remove the limit.

    Raises:
        click.BadParameter: It is none of those.
    """
    if value.strip().lower() in NO_LIMIT:
        return None
    match = re.fullmatch(r"\s*(\d+)\s*%?\s*", value)
    if match is None:
        raise click.BadParameter(f"{value!r} is not a percentage; use 50%, 200% or none")
    return int(match.group(1))


def parse_tasks(value: str) -> int | None:
    """
    Read a task limit as an operator writes it.

    Args:
        value: A number, or ``none``.

    Returns:
        The limit, or None to remove it.

    Raises:
        click.BadParameter: It is neither.
    """
    if value.strip().lower() in NO_LIMIT:
        return None
    if not value.strip().isdigit():
        raise click.BadParameter(f"{value!r} is not a number of tasks; use 256 or none")
    return int(value)


def _describe(limits: ResourceLimits) -> str:
    """
    Say what limits there are, in unit terms.

    Args:
        limits: The limits.

    Returns:
        The directives, or "no limits".
    """
    return ", ".join(limits.directives()) or "no limits"


@cli.command("limits")
@click.argument("domain")
@click.option("--memory", metavar="SIZE", help="Memory limit: 512M, 2G, or none to remove it.")
@click.option("--cpu", metavar="PERCENT", help="CPU quota: 50% of one CPU, 200% for two, or none.")
@click.option("--tasks", metavar="N", help="Processes and threads it may run, or none.")
@click.option("--restart", is_flag=True, default=False, help="Restart now so the new limits apply.")
@json_option("Print the limits as JSON.")
@pass_context
def limits_command(
    ctx: Context,
    domain: str,
    memory: str | None,
    cpu: str | None,
    tasks: str | None,
    restart: bool,
) -> None:
    """
    Show or set the memory, CPU and task limits of an application.

    A limit not named keeps its value; name it as none to remove it. The
    unit is rewritten and systemd reloaded; the running process keeps its
    old limits until it restarts, which --restart does now.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    current = ResourceLimits.of(app)
    if memory is None and cpu is None and tasks is None and not restart:
        if ctx.json_output:
            click.echo(json.dumps({"domain": app.domain, **dataclasses.asdict(current)}))
        else:
            ctx.logger.key_value("Limits", _describe(current))
        return

    wanted = ResourceLimits(
        memory_max_mb=current.memory_max_mb if memory is None else parse_memory(memory),
        cpu_quota_percent=current.cpu_quota_percent if cpu is None else parse_cpu(cpu),
        tasks_max=current.tasks_max if tasks is None else parse_tasks(tasks),
    )
    change = set_resource_limits(app.domain, wanted, restart=restart)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": change.domain,
                    **dataclasses.asdict(change.limits),
                    "units": list(change.units),
                    "restarted": change.restarted,
                }
            )
        )
        return
    ctx.logger.success(f"{change.domain}: {_describe(change.limits)}")
    if change.restarted:
        ctx.logger.info(f"Restarted {', '.join(change.units)} under the new limits")
    else:
        ctx.logger.info(
            f"The running process keeps its old limits until it restarts: noust restart {domain}"
        )


def health_settings(app: App) -> dict[str, object]:
    """
    Describe an application's health check: what it set, and what the gate uses.

    Args:
        app: The application.

    Returns:
        The stored values (None where it keeps the default) and, under
        ``effective``, what the gate actually asks.
    """
    check = HealthCheck.for_app(app)
    return {
        "domain": app.domain,
        "path": app.health_path,
        "expect": app.health_expect,
        "timeout": app.health_timeout,
        "effective": {
            "path": check.path,
            "expect": check.describe_expect(),
            "timeout": check.seconds,
        },
    }


@cli.command("health")
@click.argument("domain")
@click.option("--path", metavar="PATH", help="Path the health check requests, such as /healthz.")
@click.option(
    "--expect",
    metavar="STATUSES",
    help="Statuses that mean up: 200-399, or 200,204. Default: any status below 500.",
)
@click.option("--timeout", type=int, metavar="SECONDS", help="Seconds it gets to answer, 5 to 600.")
@click.option("--reset", is_flag=True, default=False, help="Go back to the defaults for all three.")
@global_flags
@json_option("Print the health check settings as JSON.")
@pass_context
def health_command(
    ctx: Context,
    domain: str,
    path: str | None,
    expect: str | None,
    timeout: int | None,
    reset: bool,
) -> None:
    """
    Show or set what the health gate asks of an application.

    Every deploy, update, rollback and migration keeps the new release only
    if it answers the health check; the diagnosis asks the same. An option
    not named keeps its value. Nothing restarts: the next activation uses
    the new settings.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    named = path is not None or expect is not None or timeout is not None
    if reset and named:
        raise click.UsageError("--reset goes back to every default; name no value with it.")
    if reset or named:
        app = set_health_check(
            app.domain,
            path=None if reset else (path if path is not None else app.health_path),
            expect=None if reset else (expect if expect is not None else app.health_expect),
            timeout=None if reset else (timeout if timeout is not None else app.health_timeout),
        )
    settings = health_settings(app)
    if ctx.json_output:
        click.echo(json.dumps(settings))
        return
    if reset or named:
        ctx.logger.success(f"Health check of {app.domain} updated")
    check = HealthCheck.for_app(app)
    ctx.logger.key_value("Path", check.path + ("" if app.health_path else " (default)"))
    ctx.logger.key_value(
        "Healthy", check.describe_expect() + ("" if app.health_expect else " (default)")
    )
    ctx.logger.key_value(
        "Timeout", f"{check.seconds} s" + ("" if app.health_timeout is not None else " (default)")
    )


@cli.command("branch")
@click.argument("domain")
@click.argument("branch", required=False)
@click.option("--unpin", is_flag=True, default=False, help="Deploy any branch a push names.")
@global_flags
@json_option("Print the branch as JSON.")
@pass_context
def branch_command(ctx: Context, domain: str, branch: str | None, unpin: bool) -> None:
    """
    Show, pin or unpin the branch an application deploys from.

    With a branch pinned, the webhook ignores pushes to any other branch and
    every update builds it. BRANCH must exist on the remote. Nothing is
    rebuilt now: the next update builds it.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    if unpin and branch:
        raise click.UsageError("--unpin removes the pin; name no branch with it.")
    if unpin or branch:
        pin = set_branch(app.domain, None if unpin else branch)
        current: str | None = pin.branch
        if not ctx.json_output:
            if pin.branch is None:
                ctx.logger.success(f"{app.domain} deploys any branch a push names")
            else:
                ctx.logger.success(
                    f"{app.domain} deploys {pin.branch} (now at {(pin.commit or '')[:7]})"
                )
    else:
        current = app.branch
    if ctx.json_output:
        click.echo(json.dumps({"domain": app.domain, "branch": current, "pinned": bool(current)}))
        return
    if not (unpin or branch):
        ctx.logger.key_value("Branch", current or "any (not pinned)")


# Showing what it follows only reads; changing it is audited.
@cli.command(
    "follow-tags",
    read_only=lambda params: params.get("pattern") is None and not params.get("off"),
)
@click.argument("domain")
@click.argument("pattern", required=False)
@click.option("--off", is_flag=True, default=False, help="Follow a branch again instead of tags.")
@global_flags
@json_option("Print what the application follows as JSON.")
@pass_context
def follow_tags_command(ctx: Context, domain: str, pattern: str | None, off: bool) -> None:
    """
    Show, set or clear the tags an application deploys.

    With a PATTERN (a glob over tag names, such as 'v*'), the application
    deploys the tag a release or a tag push names, in version order and never
    an older one than is deployed, and a push to a branch deploys nothing. An
    update with no tag deploys the newest tag that matches. --off goes back to
    following a branch. Nothing is rebuilt now: the next release or update
    deploys.
    """
    app = get_store().get_app(domain)
    if app is None:
        raise NoustError(
            f"Application not found: {domain}", details="Run 'noust list' to see what is deployed."
        )
    if off and pattern:
        raise click.UsageError("--off follows a branch again; name no pattern with it.")
    changed = off or pattern is not None
    current = app.follow_tags
    if changed:
        current = set_follow_tags(app.domain, None if off else pattern).pattern
        if not ctx.json_output:
            if current is None:
                ctx.logger.success(f"{app.domain} follows its branch again")
            else:
                ctx.logger.success(
                    f"{app.domain} deploys the tags matching {current}, newest version first"
                )
                ctx.logger.info(
                    f"Nothing is rebuilt now: the next release, or 'noust update {app.domain}', "
                    "deploys the newest tag."
                )
    if ctx.json_output:
        click.echo(
            json.dumps({"domain": app.domain, "follow_tags": current, "following": bool(current)})
        )
        return
    if not changed:
        ctx.logger.key_value("Follows", f"tags matching {current}" if current else "a branch")


def zero_downtime_payload(status: ZeroDowntimeStatus) -> dict[str, object]:
    """
    Describe an application's zero-downtime mode as JSON.

    Args:
        status: The mode, from :func:`noust.deployers.bluegreen.zero_downtime_status`.

    Returns:
        Its fields, the instances as a list.
    """
    return dataclasses.asdict(status) | {
        "instances": [dataclasses.asdict(instance) for instance in status.instances]
    }


def _print_zero_downtime(logger: Logger, status: ZeroDowntimeStatus) -> None:
    """
    Render an application's zero-downtime mode for a human.

    Args:
        logger: Logger the command writes through.
        status: The mode.
    """
    if not status.enabled:
        logger.key_value("Zero downtime", "off: an activation restarts the unit")
        if status.eligible:
            logger.info(f"Turn it on with: noust app zero-downtime {status.domain} on")
        else:
            logger.key_value("Available", f"no: {status.reason}")
            if status.hint:
                logger.info(status.hint)
        return
    logger.key_value("Zero downtime", f"on: {status.active_color} serves")
    for instance in status.instances:
        logger.key_value(
            instance.color.capitalize(),
            f"{instance.unit}, port {instance.port}, release {instance.release or 'none'}, "
            f"{instance.state}" + (" (serving)" if instance.serving else ""),
        )
    logger.key_value(
        "nginx upstream",
        f"127.0.0.1:{status.upstream_port}" if status.upstream_port else "missing",
    )
    logger.key_value("Drain", f"{status.drain_seconds} s")
    if status.reason:
        # A unit an interrupted switch left running: the next activation
        # refuses to start blue on the port it holds.
        logger.warning(status.reason)
        if status.hint:
            logger.info(status.hint)


@cli.command("zero-downtime")
@click.argument("domain")
@click.argument("mode", required=False, type=click.Choice(["on", "off"]))
@click.option(
    "--drain",
    type=click.IntRange(0, MAX_DRAIN_SECONDS),
    metavar="SECONDS",
    help=f"Seconds the old instance keeps running after a switch, 0 to {MAX_DRAIN_SECONDS}. "
    "Default: 10.",
)
@global_flags
@json_option("Print the mode (or what was changed) as JSON.")
@pass_context
def zero_downtime_command(ctx: Context, domain: str, mode: str | None, drain: int | None) -> None:
    """
    Show or switch blue/green activation of an application.

    On, the application runs as two instances of one unit, blue on its port
    and green on the next: each deploy, update and rollback starts the new
    release on the idle instance, switches nginx to it once it answers, and
    stops the old one after the drain. The application must tolerate two
    copies running for those seconds (a SQLite database written by both, a
    queue with a single consumer or jobs scheduled in-process do not).
    Switching the mode on or off is itself done without a cut. Without ON
    or OFF, shows the mode.
    """
    if mode is None and drain is None:
        status = zero_downtime_status(domain)
        if ctx.json_output:
            click.echo(json.dumps(zero_downtime_payload(status)))
        else:
            _print_zero_downtime(ctx.logger, status)
        return

    if mode is None:
        # Only the drain: meaningful for an application already switched on.
        current = zero_downtime_status(domain)
        if not current.enabled:
            raise click.UsageError(
                f"{current.domain} is not in zero-downtime mode; turn it on with the drain: "
                f"noust app zero-downtime {current.domain} on --drain {drain}"
            )
        mode = "on"

    logger = CapturingLogger(verbose=ctx.verbose)
    change: ModeChange = set_zero_downtime(domain, mode == "on", drain_seconds=drain, logger=logger)
    if ctx.json_output:
        click.echo(json.dumps(dataclasses.asdict(change)))
        return
    if change.rehearsed:
        logger.info("Rehearsal: nothing was changed")
    elif not change.changed:
        logger.info(f"{change.domain} is already {'on' if change.enabled else 'off'}")
    elif change.enabled:
        logger.key_value("Serving", str(change.active_color))
        logger.key_value("Drain", f"{change.drain_seconds} s")


# Export and import -----------------------------------------------------------


def parse_env_pairs(pairs: tuple[str, ...]) -> dict[str, str]:
    """
    Read ``--env NAME=VALUE`` options.

    Args:
        pairs: The options, as typed.

    Returns:
        Name to value, the last one winning.

    Raises:
        click.BadParameter: One has no ``=`` or no name.
    """
    values: dict[str, str] = {}
    for pair in pairs:
        name, sep, value = pair.partition("=")
        if not sep or not name.strip():
            raise click.BadParameter(f"{pair!r} is not NAME=VALUE", param_hint="--env")
        values[name.strip()] = value
    return values


def gather_env(env_file: Path | None, pairs: tuple[str, ...], logger: Logger) -> dict[str, str]:
    """
    Collect the variables an import is given: the file, then ``--env`` over it.

    The file is read by ``noust create``'s own reader, so it means the same
    thing here as it does there.

    Args:
        env_file: File given to ``--env-file``, or None.
        pairs: ``--env`` options.
        logger: Where a skipped line is reported.

    Returns:
        Name to value.
    """
    from_file = _read_env_file(env_file, logger) if env_file is not None else {}
    return {**from_file, **parse_env_pairs(pairs)}


def cli_deploy(logger: Logger) -> Callable[[CreateSpec], None]:
    """
    Build the deploy an import runs from the terminal: ``noust create``'s own.

    Args:
        logger: Logger of the current command.

    Returns:
        A function that deploys what a plan asks for and raises when it fails.
    """

    def deploy(spec: CreateSpec) -> None:
        code = _create_app(
            logger=logger,
            domain=spec.domain,
            source=spec.source,
            app_type=spec.app_type,
            port=spec.port,
            webserver=spec.webserver,
            branch=spec.branch,
            ssl=spec.ssl,
            www=spec.include_www,
            layout=spec.layout,
            persist=spec.persistent_paths,
            env_vars=spec.env_vars,
            env_secret_marks=spec.env_secret_marks,
            limits=ResourceLimits(
                memory_max_mb=spec.memory_max_mb,
                cpu_quota_percent=spec.cpu_quota_percent,
                tasks_max=spec.tasks_max,
            ),
            initial_health=spec.initial_health,
        )
        if code != 0:
            raise DeploymentError(
                f"The deployment of {spec.domain} did not start",
                details="Fix what is reported above and import again.",
            )

    return deploy


def print_import_plan(logger: Logger, plan: ImportPlan) -> None:
    """
    Render what an import will do for a human.

    Args:
        logger: Logger the command writes through.
        plan: The plan.
    """
    summary = plan_summary(plan)
    create = summary["create"]
    logger.key_value("Application", plan.domain)
    if plan.domain != plan.exported_domain:
        logger.key_value("Exported from", plan.exported_domain)
    logger.key_value("Type", create["app_type"])
    logger.key_value(
        "Source", f"{create['source']}" + (f" ({create['branch']})" if create["branch"] else "")
    )
    logger.key_value("Variables", ", ".join(create["env"]) or "none")
    for line in plan.steps[1:]:
        logger.list_item(line)
    for skipped in plan.skipped:
        logger.warning(f"Will not apply {skipped.part}: {skipped.detail}")
    for reason in plan.confirm:
        logger.warning(f"The import {reason}")


def print_import_report(logger: Logger, report: ImportReport) -> None:
    """
    Render what an import did for a human, what it did not do last.

    Args:
        logger: Logger the command writes through.
        report: The report.
    """
    missing = report.not_applied
    if not missing:
        logger.success(f"Imported {report.domain}: everything in the export was applied")
        return
    logger.success(f"Imported {report.domain}")
    logger.warning(f"{len(missing)} part(s) of the export were not applied:")
    for step in missing:
        logger.list_item(f"{step.part}: {step.detail}")


def _interactive() -> bool:
    """Whether someone is at the terminal to answer a question."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm_import(ctx: Context, plan: ImportPlan, yes: bool) -> None:
    """
    Have the operator agree to what a document may do as root.

    A document can create cron jobs, which run its commands on a schedule
    (as root, if it says so), and turn on previews, which deploy pull
    requests with the application's variables. The plan shows each; this
    asks for a yes, or ``--yes``, before any of it runs.

    Args:
        ctx: The command's context.
        plan: The plan, already shown.
        yes: ``--yes`` was given.

    Raises:
        ValidationError: Nobody is at the terminal to confirm and ``--yes``
            was not given.
        click.Abort: The operator said no.
    """
    if yes or not plan.confirm:
        return
    reasons = "; ".join(plan.confirm)
    if ctx.json_output or not _interactive():
        raise ValidationError(
            f"The import needs confirming: it {reasons}",
            details="Review the plan with --dry-run, then run the import again with --yes.",
        )
    click.confirm(f"Import {plan.domain}? It {reasons}", abort=True)


def run_import(ctx: Context, plan: ImportPlan, *, yes: bool = False) -> None:
    """
    Show a plan, then carry it out unless this is a rehearsal.

    Shared by ``noust app import`` and ``noust import --deploy``. A plan that
    creates cron jobs or previews runs only after :func:`confirm_import`.

    Args:
        ctx: The command's context.
        plan: The plan.
        yes: Do not ask for confirmation.
    """
    if ctx.dry_run:
        if ctx.json_output:
            click.echo(json.dumps({"plan": plan_summary(plan), "rehearsed": True}))
            return
        print_import_plan(ctx.logger, plan)
        ctx.logger.info("Rehearsal: nothing was created")
        return
    if not ctx.json_output:
        print_import_plan(ctx.logger, plan)
    confirm_import(ctx, plan, yes)
    report = apply_import(plan, deploy=cli_deploy(ctx.logger), logger=ctx.logger)
    if ctx.json_output:
        click.echo(json.dumps({"plan": plan_summary(plan), "result": report_summary(report)}))
        return
    print_import_report(ctx.logger, report)


@cli.command("export")
@click.argument("domain")
@click.option(
    "--with-secrets",
    is_flag=True,
    default=False,
    help="Include the values of secret variables. The file is written 0600.",
)
@click.option(
    "-o",
    "--output",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Write the export to this file instead of printing it.",
)
@global_flags
@json_option("The export is JSON already; accepted for scripts that always pass it.")
@pass_context
def export_command(ctx: Context, domain: str, with_secrets: bool, output: Path | None) -> None:
    """
    Export everything that defines an application as a JSON document.

    Type, source, branch, layout, domains, variables, secret marks, health
    check, limits, retention, persistent paths, cron jobs, backup schedule,
    previews and zero-downtime. Secret values are left out unless
    --with-secrets; Noust's own credentials (webhook secret, backup
    destination keys, GitHub App) never go in. Recreate it anywhere with
    'noust app import'.
    """
    text = dumps(export_app(domain, with_secrets=with_secrets))
    if output is None:
        if with_secrets:
            # On stderr, so a redirect to a file still gets the document alone.
            click.echo(
                "Warning: the export carries secret values in clear. Write it with -o FILE, "
                "which is created readable by root only (0600), rather than through a "
                "terminal or a redirect.",
                err=True,
            )
        click.echo(text, nl=False)
        return
    fs = get_fs()
    fs.write_text(output, text, mode=SECRET_MODE if with_secrets else 0o644)
    if not ctx.json_output:
        ctx.logger.success(f"Exported {domain} to {output}")
        if not with_secrets:
            ctx.logger.info(
                "Secret values were left out; give them to 'noust app import' with "
                "--env-file or --env."
            )


@cli.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--domain", help="Create it on this domain instead of the exported one.")
@click.option("--source", help="Deploy from this source instead of the exported one.")
@click.option(
    "--env-file",
    type=click.Path(exists=True, dir_okay=False, readable=True, path_type=Path),
    help="File of NAME=value lines: the secret values the export left out, or overrides.",
)
@click.option(
    "--env",
    "env_pairs",
    multiple=True,
    metavar="NAME=VALUE",
    help="A variable's value, over the export and --env-file. Repeat for several.",
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="Do not ask before creating the export's cron jobs and previews.",
)
@global_flags
@json_option("Print the plan and what was applied as JSON.")
@pass_context
def import_command(
    ctx: Context,
    file: Path,
    domain: str | None,
    source: str | None,
    env_file: Path | None,
    env_pairs: tuple[str, ...],
    yes: bool,
) -> None:
    """
    Create an application from a 'noust app export' document.

    It is deployed through the normal path (built, health-gated, recorded),
    then its domains, health check, retention, secret marks, cron jobs,
    backup schedule, previews and zero-downtime are applied. What cannot be
    applied here is listed at the end. With --dry-run, only the plan is shown.
    An export that creates cron jobs or previews is confirmed first: every
    job is shown with its user, directory and command; --yes skips the
    question, and without a terminal it is required.
    """
    document = load_document(file.read_text(encoding="utf-8"))
    env = gather_env(env_file, env_pairs, ctx.logger)
    plan = plan_import(document, domain=domain, source=source, env=env)
    run_import(ctx, plan, yes=yes)
