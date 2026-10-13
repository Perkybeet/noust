# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app process``: the processes an application runs beside its web process.

``list`` shows them and their copies, ``set`` defines or changes one, ``scale``
runs another number of copies, ``remove`` stops one and forgets it, ``logs``
reads its journal. All of it is :mod:`noust.managers.app_processes`, which
``/api/apps/{d}/processes`` calls too. A change is judged like an activation:
its copies must stay up, or the process is put back as it was.
"""

from __future__ import annotations

import json

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.app_process_store import MAX_INSTANCES, ProcessRecord
from noust.managers.app_processes import ProcessManager
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


@click.group("process", cls=NoustGroup)
def process_group() -> None:
    """The processes an application runs beside its web process: workers, schedulers."""


@process_group.command("list", read_only=True)
@click.argument("domain")
@json_option("Print the processes as JSON.")
@pass_context
def list_command(ctx: Context, domain: str) -> None:
    """
    List the processes of an application with the state of each copy.
    """
    found = ProcessManager(log=ctx.logger).status(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps({"processes": [item.to_dict() for item in found]}))
        return
    if not found:
        ctx.logger.info("No processes beside the web process")
        ctx.logger.blank()
        ctx.logger.info(f"Add one with: noust app process set {domain} worker 'node worker.js'")
        return
    rows = [
        [
            item.record.name,
            item.record.command,
            f"{item.running}/{item.record.instances}",
            item.record.source,
        ]
        for item in found
    ]
    ctx.logger.table(["Process", "Command", "Running", "Defined by"], rows)


@process_group.command("set")
@click.argument("domain")
@click.argument("name")
@click.argument("command")
@click.option(
    "--instances",
    type=click.IntRange(0, MAX_INSTANCES),
    default=1,
    show_default=True,
    help="How many copies run on this server.",
)
@click.option("--memory", "memory_max_mb", type=int, metavar="MB", help="MemoryMax of each copy.")
@click.option(
    "--cpu", "cpu_quota_percent", type=int, metavar="PERCENT", help="CPUQuota of each copy."
)
@click.option("--tasks", "tasks_max", type=int, metavar="N", help="TasksMax of each copy.")
@json_option("Print what was done as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    name: str,
    command: str,
    instances: int,
    memory_max_mb: int | None,
    cpu_quota_percent: int | None,
    tasks_max: int | None,
) -> None:
    """
    Define a process of the application, or change one, and run it.

    COMMAND is the program and its arguments, split like a POSIX shell would
    and run without one. It runs as the application's account, in the release
    that serves, with the application's environment, and is restarted with the
    application at every deploy: if it does not stay up on the new release, the
    release is put back.
    """
    change = ProcessManager(log=ctx.logger).set_process(
        validate_domain(domain),
        ProcessRecord(
            name=name,
            command=command,
            instances=instances,
            memory_max_mb=memory_max_mb,
            cpu_quota_percent=cpu_quota_percent,
            tasks_max=tasks_max,
        ),
        actor=_actor(),
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": change.domain,
                    "name": change.name,
                    "instances": change.instances,
                    "started": list(change.started),
                    "stopped": list(change.stopped),
                }
            )
        )
        return
    ctx.logger.success(
        f"{change.name}: {change.instances} cop{'y' if change.instances == 1 else 'ies'} running"
    )


@process_group.command("scale")
@click.argument("domain")
@click.argument("name")
@click.argument("instances", type=click.IntRange(0, MAX_INSTANCES))
@json_option("Print what was done as JSON.")
@pass_context
def scale_command(ctx: Context, domain: str, name: str, instances: int) -> None:
    """
    Run INSTANCES copies of a process. 0 stops them and keeps the definition.

    The copies that already run are not restarted.
    """
    change = ProcessManager(log=ctx.logger).scale(
        validate_domain(domain), name, instances, actor=_actor()
    )
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "domain": change.domain,
                    "name": change.name,
                    "instances": change.instances,
                    "started": list(change.started),
                    "stopped": list(change.stopped),
                }
            )
        )
        return
    ctx.logger.success(
        f"{change.name}: {change.instances} cop{'y' if change.instances == 1 else 'ies'}"
    )


@process_group.command("remove")
@click.argument("domain")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, domain: str, name: str, yes: bool) -> None:
    """
    Stop a process and forget it.
    """
    if not (yes or ctx.json_output or ctx.dry_run):
        click.confirm(f"Stop {name} and remove it from {domain}?", abort=True)
    change = ProcessManager(log=ctx.logger).remove(validate_domain(domain), name, actor=_actor())
    ctx.logger.success(f"Removed {change.name}")


@process_group.command("logs", read_only=True)
@click.argument("domain")
@click.argument("name")
@click.option("--lines", "-n", type=click.IntRange(1, 5000), default=100, show_default=True)
@pass_context
def logs_command(ctx: Context, domain: str, name: str, lines: int) -> None:
    """
    Read the journal of a process: all its copies, interleaved.
    """
    click.echo(ProcessManager(log=ctx.logger).logs(validate_domain(domain), name, lines=lines))
