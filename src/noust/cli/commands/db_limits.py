# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust db limits``: what an engine on the host may use.

Shows the memory, processors, swap and tasks an engine is held to, with what systemd holds and what
the kernel enforces, and sets them: ``noust db limits postgresql --memory 4g --cpus 2``. The work
is :class:`noust.managers.database.host_limits.HostLimits`, which the console's API calls too; this
module parses arguments and prints. An engine in a container has its own limits
(``noust db instance limits``).

``noust db`` (:mod:`noust.cli.commands.db`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustCommand, json_option, pass_context
from noust.core.exceptions import ConfirmationRequired, NoustError
from noust.core.fs import is_rehearsal
from noust.managers.database.host_limits import DIRECTIVES, LimitsStatus


class _Engine(click.ParamType):
    """An engine name, checked by ``noust db``'s own type at call time."""

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

#: The words an operator uses for the limits that can be removed.
CLEARABLE = ("memory", "cpus", "swap", "tasks")

_KEYS = {"memory": "memory_mb", "cpus": "cpus", "swap": "swap_mb", "tasks": "tasks"}


def _echo_json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _show(status: LimitsStatus) -> str:
    """
    Args:
        status: The engine's limits.

    Returns:
        The limits as a table, one line each, with the proof next to the value.
    """
    names = {"memory_mb": "Memory", "cpus": "Processors", "swap_mb": "Swap", "tasks": "Tasks"}
    units = {"memory_mb": " MB", "cpus": "", "swap_mb": " MB", "tasks": ""}
    lines = [
        f"{status.display_name} ({status.unit}) on {status.server_memory_mb} MB and "
        f"{status.cpus} processors",
        f"Noust's drop-in: {status.file}",
        "",
        f"  {'Limit':<12} {'Noust sets':<14} {'systemd holds':<16} Kernel",
    ]
    for key in DIRECTIVES:
        configured = status.configured.get(key)
        held = status.effective.get(key)
        proof = status.enforced.get(key)
        kernel = "-" if proof is None else "enforced" if proof else "NOT enforced"
        lines.append(
            f"  {names[key]:<12} "
            f"{('-' if configured is None else f'{configured:g}{units[key]}'):<14} "
            f"{('-' if held is None else f'{held:g}{units[key]}'):<16} {kernel}"
        )
    if status.memory_in_use_mb is not None:
        lines.append(f"\nIt uses {status.memory_in_use_mb} MB now.")
    if status.elsewhere:
        lines.append(
            f"Also set outside Noust's drop-in: {', '.join(status.elsewhere)} "
            f"(see: systemctl cat {status.unit})."
        )
    return "\n".join(lines)


@click.command(
    "limits",
    cls=NoustCommand,
    read_only=lambda params: (
        not any(params.get(name) for name in ("memory", "cpus", "swap", "tasks", "clear"))
    ),
)
@click.argument("engine", type=ENGINE)
@click.option(
    "--memory", help="Memory the engine may use: 4096, 512m or 4g. The kernel kills it above."
)
@click.option("--cpus", help="Processors it may use: 0.5, 2 or 3.5.")
@click.option("--swap", help="Swap it may use besides its memory, in megabytes; 0 gives it none.")
@click.option("--tasks", help="Processes and threads it may have (256 or more).")
@click.option(
    "--remove",
    "clear",
    multiple=True,
    type=click.Choice(CLEARABLE),
    help="Take a limit away; repeat it for several.",
)
@click.option(
    "--yes",
    "-y",
    "confirm",
    is_flag=True,
    help="Accept a memory limit below what the engine uses now.",
)
@json_option("Print the limits or the outcome as JSON.")
@pass_context
def limits(
    ctx: Context,
    engine: str,
    memory: str | None,
    cpus: str | None,
    swap: str | None,
    tasks: str | None,
    clear: tuple[str, ...],
    confirm: bool,
) -> None:
    """
    Show the limits of an engine on this host, or set them.

    A limit is a systemd drop-in applied without restarting the engine, and proved against
    systemd and the kernel's cgroup; if the engine stops answering the previous limits are put
    back. A memory limit is a promise the kernel keeps by killing the engine's process, so set it
    well above the engine's cache settings.
    """
    from noust.cli.commands.db import _service

    asked = {
        key: value
        for key, value in (
            ("memory_mb", memory),
            ("cpus", cpus),
            ("swap_mb", swap),
            ("tasks", tasks),
        )
        if value is not None
    }
    service = _service(ctx.logger)
    try:
        if not asked and not clear:
            status = service.engine_limits(engine)
            if ctx.json_output:
                _echo_json(status.to_dict())
            else:
                click.echo(_show(status))
            return
        if is_rehearsal():
            ctx.logger.info(f"Would set the limits of {engine}")
            return
        outcome = service.change_engine_limits(
            engine, asked, clear=[_KEYS[name] for name in clear], confirm=confirm
        )
    except ConfirmationRequired as warning:
        for text in warning.warnings:
            ctx.logger.warning(text)
        ctx.logger.info("Nothing was changed. Run it again with --yes to go ahead.")
        raise SystemExit(1) from None
    except NoustError as failure:
        ctx.logger.error(str(failure))
        output = getattr(failure, "output", None)
        if output:
            click.echo(output)
        raise SystemExit(1) from None
    if ctx.json_output:
        _echo_json(outcome.to_dict())
        return
    if not outcome.changed:
        ctx.logger.info(f"{outcome.display_name} already has those limits; nothing changed")
        return
    ctx.logger.success(
        f"{outcome.display_name}: {', '.join(outcome.changed)} changed in {outcome.file}, "
        "applied without a restart and proved"
    )
    for text in outcome.warnings:
        ctx.logger.warning(text)


def register(group: click.Group) -> None:
    """
    Join ``noust db``: what :mod:`noust.cli.commands.db` calls.

    Args:
        group: The ``db`` group.
    """
    group.add_command(limits)
