# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app tuning``: the limits and options of an application's unit beyond memory, CPU and tasks.

``status`` says what is set and the unit lines it becomes; ``set`` changes some of it (the rest is
kept), ``clear`` removes it all. A change rewrites the unit and restarts the application behind its
health gate, putting the previous unit back when it does not answer. All of it is
:mod:`noust.deployers.unit_tuning`, which ``/api/apps/{d}/tuning`` calls too; this module only
parses, presents and asks.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.deployers import unit_tuning as engine
from noust.managers import unit_tuning
from noust.validators.domain import validate_domain


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _memlock(value: str | None) -> int | str | None:
    """
    Read ``--memlock``: megabytes or ``infinity``.

    Args:
        value: What was typed.

    Returns:
        The number of megabytes, ``infinity``, or None when not given.

    Raises:
        click.BadParameter: Neither.
    """
    if value is None or value == "infinity":
        return value
    try:
        return int(value)
    except ValueError as exc:
        raise click.BadParameter("give megabytes or 'infinity'", param_hint="--memlock") from exc


@click.group("tuning", cls=NoustGroup)
def tuning_group() -> None:
    """Limits and options of an application's unit: soft memory, swap, weights, CPUs, ulimits, capabilities, devices."""


@tuning_group.command("status", read_only=True)
@click.argument("domain")
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context, domain: str) -> None:
    """
    Show how the application's unit is tuned and the lines it has for it.
    """
    info = engine.status(validate_domain(domain))
    if ctx.json_output:
        click.echo(json.dumps(info.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Application", info.domain)
    if not info.eligible:
        logger.key_value("Tuning", f"not applicable: {info.reason}")
        return
    if not info.options:
        logger.key_value("Tuning", "none (the unit is as the template wrote it)")
        logger.blank()
        logger.info(f"Set some with: noust app tuning set {info.domain} --memory-high 512")
        return
    for name, value in info.options.items():
        shown = (
            ", ".join(
                entry["path"] + ":" + entry["mode"] if isinstance(entry, dict) else str(entry)
                for entry in value
            )
            if isinstance(value, list)
            else str(value)
        )
        logger.key_value(name, shown)
    logger.blank()
    for directive in info.directives:
        logger.info(f"  {directive}")


def _merge(
    current: dict[str, Any],
    *,
    given: dict[str, Any],
    capabilities: tuple[str, ...],
    devices: tuple[str, ...],
    gpus: tuple[str, ...],
    unset: tuple[str, ...],
) -> dict[str, Any]:
    """
    Lay what was typed over what the unit has.

    Args:
        current: The options now.
        given: Scalar options typed this time; None means not typed.
        capabilities: ``--cap`` values; given, they replace the list.
        devices: ``--device`` values; given, they replace the list.
        gpus: ``--gpu`` presets, added to the devices.
        unset: Option names to remove.

    Returns:
        The complete options to set.

    Raises:
        click.UsageError: A preset or an option name that does not exist.
    """
    merged = dict(current)
    merged.update({name: value for name, value in given.items() if value is not None})
    if capabilities:
        merged["capabilities"] = list(capabilities)
    if devices or gpus:
        found: list[str] = list(devices)
        for preset in gpus:
            if preset not in unit_tuning.GPU_PRESETS:
                raise click.UsageError(
                    f"--gpu {preset!r} is not a preset. Offered: {', '.join(unit_tuning.GPU_PRESETS)}."
                )
            found += [f"{group}:rw" for group in unit_tuning.GPU_PRESETS[preset]]
        merged["devices"] = found
    for name in unset:
        if name not in unit_tuning.OPTION_NAMES:
            raise click.UsageError(
                f"--unset {name!r} is not an option. Options: {', '.join(unit_tuning.OPTION_NAMES)}."
            )
        merged.pop(name, None)
    return merged


@tuning_group.command("set")
@click.argument("domain")
@click.option(
    "--memory-high",
    "memory_high_mb",
    type=int,
    metavar="MB",
    help="Soft memory limit: throttled above it, killed only at the hard one.",
)
@click.option(
    "--swap-max",
    "memory_swap_max_mb",
    type=int,
    metavar="MB",
    help="Swap the unit may use; 0 forbids it.",
)
@click.option(
    "--cpu-weight",
    type=int,
    metavar="1-10000",
    help="Share of the CPU when it is contended (100 is the default).",
)
@click.option(
    "--allowed-cpus", metavar="SET", help="Pin the unit to these CPUs, such as 0-3 or 0,2."
)
@click.option(
    "--io-weight", type=int, metavar="1-10000", help="Share of the disk when it is contended."
)
@click.option("--nofile", "limit_nofile", type=int, metavar="N", help="Open files (ulimit -n).")
@click.option(
    "--nproc", "limit_nproc", type=int, metavar="N", help="Processes of the account (ulimit -u)."
)
@click.option("--memlock", metavar="MB|infinity", help="Locked memory (ulimit -l).")
@click.option(
    "--core-dumps/--no-core-dumps", "core_dumps", default=None, help="Allow core dumps (ulimit -c)."
)
@click.option(
    "--cap",
    "capabilities",
    multiple=True,
    metavar="NAME",
    help="A capability the process keeps (NET_BIND_SERVICE, ...); repeat it. Replaces the list.",
)
@click.option(
    "--device",
    "devices",
    multiple=True,
    metavar="PATH[:MODE]",
    help="A device the process may open (/dev/nvidia0:rw, char-drm); repeat it. Replaces the list.",
)
@click.option(
    "--gpu",
    "gpus",
    multiple=True,
    metavar="VENDOR",
    help="Allow the devices a GPU needs (nvidia, dri); repeat it.",
)
@click.option(
    "--unset",
    "unset",
    multiple=True,
    metavar="OPTION",
    help="Remove one option (memory_high_mb, capabilities, ...); repeat it.",
)
@click.option(
    "--no-restart",
    is_flag=True,
    default=False,
    help="Write the unit but do not restart: the running process keeps what it has.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print what was done as JSON.")
@pass_context
def set_command(
    ctx: Context,
    domain: str,
    memory_high_mb: int | None,
    memory_swap_max_mb: int | None,
    cpu_weight: int | None,
    allowed_cpus: str | None,
    io_weight: int | None,
    limit_nofile: int | None,
    limit_nproc: int | None,
    memlock: str | None,
    core_dumps: bool | None,
    capabilities: tuple[str, ...],
    devices: tuple[str, ...],
    gpus: tuple[str, ...],
    unset: tuple[str, ...],
    no_restart: bool,
    yes: bool,
) -> None:
    """
    Tune the application's unit; what is not named is kept.

    Memory, CPU and swap act on the application's own cgroup: --memory-high
    makes the kernel reclaim from it before the hard limit (noust app limits
    --memory) would kill it. --cpu-weight and --io-weight only matter when the
    machine is contended. Capabilities are a short list of the ones a web
    process can need; a GPU is a device group.

    The application is restarted behind its health gate; when it does not
    answer, the previous unit is put back and it is restarted as it was.
    """
    validated = validate_domain(domain)
    current = engine.status(validated)
    if not current.eligible:
        raise click.UsageError(f"{validated} cannot be tuned: {current.reason}")
    options = _merge(
        dict(current.options or {}),
        given={
            "memory_high_mb": memory_high_mb,
            "memory_swap_max_mb": memory_swap_max_mb,
            "cpu_weight": cpu_weight,
            "allowed_cpus": allowed_cpus,
            "io_weight": io_weight,
            "limit_nofile": limit_nofile,
            "limit_nproc": limit_nproc,
            "limit_memlock_mb": _memlock(memlock),
            "core_dumps": core_dumps,
        },
        capabilities=capabilities,
        devices=devices,
        gpus=gpus,
        unset=unset,
    )
    _apply(ctx, validated, options, no_restart=no_restart, yes=yes)


@tuning_group.command("clear")
@click.argument("domain")
@click.option(
    "--no-restart", is_flag=True, default=False, help="Write the unit but do not restart."
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print what was done as JSON.")
@pass_context
def clear_command(ctx: Context, domain: str, no_restart: bool, yes: bool) -> None:
    """
    Remove all the tuning: the unit goes back to what the template writes.
    """
    _apply(ctx, validate_domain(domain), {}, no_restart=no_restart, yes=yes)


def _apply(
    ctx: Context, domain: str, options: dict[str, Any], *, no_restart: bool, yes: bool
) -> None:
    """
    Ask, change the tuning and say what happened.

    Args:
        ctx: The command's context.
        domain: The application.
        options: The complete options to set.
        no_restart: Do not restart.
        yes: Do not ask.
    """
    if not (yes or ctx.json_output or ctx.dry_run or no_restart):
        click.confirm(
            f"Restart {domain} with this tuning? It is restarted behind its health check",
            abort=True,
        )
    change = engine.set_tuning(
        domain, options, restart=not no_restart, actor=_actor(), logger=ctx.logger
    )
    if ctx.json_output:
        click.echo(json.dumps(change.to_dict()))
        return
    if not change.restarted:
        ctx.logger.warning(
            f"{change.domain} was not restarted: it keeps what it has until it is "
            f"(noust restart {change.domain})"
        )
