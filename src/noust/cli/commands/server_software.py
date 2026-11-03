# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust server software``: what the server is made of, and installing and removing it (item 118).

The command line and the console's Server > Software are two clients of
:class:`~noust.managers.server.software.SoftwareManager`; this module formats, asks and prints and
holds no decision of its own. A refusal is the manager's: a piece that is never removed, one
something depends on, one whose removal would take another along, and it says why and exits 1. A
removal is never a purge, and what Noust did not install is removed only with
``--confirm-unknown-origin``, which is more than ``--yes``: other things on the system may use it.

``noust server`` (:mod:`noust.cli.commands.server`) loads this module and calls :func:`register`.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NoustError
from noust.core.utils import check_root
from noust.managers.server.component_dependents import Dependent
from noust.managers.server.context import ServerContext
from noust.managers.server.errors import ConfirmationRequiredError
from noust.managers.server.facts import FactCache
from noust.managers.server.software import (
    Impact,
    InstallOptions,
    SoftwareItem,
    SoftwareManager,
)


@click.group("software", cls=NoustGroup)
def software() -> None:
    """What the server is made of: what is installed, who depends on it, installing and removing it."""


def _software() -> SoftwareManager:
    """
    Build the manager this invocation uses.

    Returns:
        The software manager over a context with no background work: a command waits for what it
        asks.
    """
    return ServerContext(cache=FactCache(background=False)).software


def _require_root(ctx: Context, action: str) -> None:
    """
    Refuse an action that cannot work without root, except in a rehearsal.

    Args:
        ctx: The command's context.
        action: What was asked, for the message.

    Raises:
        NoustError: The process is not root and this is not a dry run.
    """
    if ctx.dry_run or check_root():
        return
    raise NoustError(f"'{action}' needs root", details="Run it with sudo.")


def _interactive(ctx: Context) -> bool:
    """
    Args:
        ctx: The command's context.

    Returns:
        Whether there is somebody to ask: a terminal, and no ``--json``.
    """
    return sys.stdin.isatty() and not ctx.json_output


def _confirm(ctx: Context, question: str, *, yes: bool) -> None:
    """
    Ask before doing something, or require ``--yes`` when nobody can answer.

    Args:
        ctx: The command's context.
        question: What is about to happen.
        yes: ``--yes`` was given.

    Raises:
        click.Abort: The answer was no.
        click.UsageError: There is no terminal to ask on and ``--yes`` was not given.
    """
    if yes or ctx.dry_run:
        return
    if not _interactive(ctx):
        raise click.UsageError(f"{question} Add --yes to confirm.")
    if not click.confirm(question, default=False):
        raise click.Abort()


def _json(data: Any) -> None:
    """
    Args:
        data: Anything JSON-serialisable.
    """
    click.echo(json.dumps(data, indent=2, sort_keys=True, default=str))


def _used_by(dependents: tuple[Dependent, ...]) -> str:
    """
    Args:
        dependents: What depends on a component.

    Returns:
        Them on one line, by kind (``application: a.example.com, b.example.com``), or ``-``.
    """
    return "; ".join(f"{d.kind}: {', '.join(d.names)}" for d in dependents) or "-"


def _state(item: SoftwareItem) -> str:
    """
    Args:
        item: A component.

    Returns:
        ``not installed``, or ``installed`` with its version and who put it there.
    """
    if not item.installed:
        return "not installed"
    version = f" {item.version}" if item.version else ""
    origin = {"noust": "by Noust", "unknown": "before Noust"}.get(item.origin or "", "")
    return f"installed{version}" + (f" ({origin})" if origin else "")


@software.command("list", read_only=True)
@json_option("Print the software as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List every piece of software Noust manages: installed or not, who depends on it."""
    inventory = _software().inventory(refresh=True)
    if ctx.json_output:
        _json(inventory.to_dict())
        return
    if not inventory.managed:
        ctx.logger.warning(f"Noust does not change this system's software: {inventory.reason}")
    ctx.logger.table(
        ["Software", "State", "Used by", "What for"],
        [
            [item.id, _state(item), _used_by(item.dependents), item.purpose.text]
            for item in inventory.items
        ],
    )


def _show_plan(ctx: Context, impact: Impact) -> None:
    """
    Print what a removal would do.

    Args:
        ctx: The command's context.
        impact: What the manager found.
    """
    logger = ctx.logger
    logger.key_value("Software", impact.id)
    logger.key_value(
        "Removes", ", ".join(impact.removes) or "nothing: the engine's own manager does"
    )
    if impact.also_removes:
        logger.key_value("Also removes", ", ".join(impact.also_removes))
    if impact.repository_files:
        logger.key_value("Repository files taken back out", ", ".join(impact.repository_files))
    if impact.dependents:
        logger.key_value("Used by", _used_by(impact.dependents))
    for blocker in impact.blockers:
        logger.warning(blocker.text.text)


@software.command("install")
@click.argument("component")
@click.option(
    "--epel",
    is_flag=True,
    help="Allow enabling EPEL, where Knot DNS and fail2ban come from on RHEL and its rebuilds.",
)
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print what was done as JSON.")
@pass_context
def install_command(ctx: Context, component: str, epel: bool, yes: bool) -> None:
    """
    Install a piece of software, unless it is installed.

    The names are those of 'noust server software list'. It is installed through the installer
    that already knows it (cloudflared from Cloudflare's repository with its pinned key, a database
    engine as 'noust db install' does, the rest through the package manager), and written in the
    ledger of what Noust installed.
    """
    manager = _software()
    manager.check_install(component)
    _require_root(ctx, "server software install")
    _confirm(ctx, f"Install {component}?", yes=yes)
    ignore = []
    connection = os.environ.get("SSH_CONNECTION", "").split()
    if connection:
        ignore.append(connection[0])
    result = manager.install(
        component,
        InstallOptions(epel=epel, ignore=tuple(ignore)),
        on_output=None if ctx.json_output else click.echo,
    )
    if ctx.json_output:
        _json(result.to_dict())
        return
    if result.rehearsal:
        ctx.logger.info(f"Would install {component}")
    elif not result.installed_now:
        ctx.logger.info(f"{component} is already installed")
    else:
        ctx.logger.success(f"{component} installed ({', '.join(result.packages) or 'no package'})")


@software.command("remove", read_only=lambda params: bool(params["plan"]))
@click.argument("component")
@click.option("--plan", is_flag=True, help="Only show what would be removed, and what stops it.")
@click.option(
    "--confirm-unknown-origin",
    is_flag=True,
    help="Remove something Noust did not install: other things on the system may use it.",
)
@click.option("-y", "--yes", is_flag=True, help="Do not ask.")
@json_option("Print the plan, or what was done, as JSON.")
@pass_context
def remove_command(
    ctx: Context, component: str, plan: bool, confirm_unknown_origin: bool, yes: bool
) -> None:
    """
    Remove a piece of software, never purging what it leaves in /etc.

    It is refused, with its reason, while something depends on it (the sites behind nginx, the
    tunnels cloudflared runs, the databases an engine holds), when removing its packages would also
    remove something that is never removed or that something depends on, and for Noust itself,
    systemd, sshd, the firewall, git, curl and gnupg. What Noust did not install needs
    --confirm-unknown-origin. The repository and the key Noust added go with the package.
    """
    manager = _software()
    impact = manager.impact(component)
    if plan:
        if ctx.json_output:
            _json(impact.to_dict())
            return
        _show_plan(ctx, impact)
        return
    _require_root(ctx, "server software remove")
    confirmed = confirm_unknown_origin
    try:
        manager.check_remove(component, confirm_unknown_origin=confirmed)
    except ConfirmationRequiredError as asked:
        if not _interactive(ctx):
            raise click.UsageError(
                f"Noust did not install {component}, and other things on the system may use it. "
                "Add --confirm-unknown-origin to remove it anyway."
            ) from asked
        if not click.confirm(
            f"Noust did not install {component}, and other things on the system may use it. "
            "Remove it anyway?",
            default=False,
        ):
            raise click.Abort() from asked
        confirmed = True
    if not ctx.json_output:
        _show_plan(ctx, impact)
    _confirm(ctx, f"Remove {component}?", yes=yes or (confirmed and _interactive(ctx)))
    result = manager.remove(
        component,
        confirm_unknown_origin=confirmed,
        on_output=None if ctx.json_output else click.echo,
    )
    if ctx.json_output:
        _json(result.to_dict())
        return
    if result.rehearsal:
        ctx.logger.info(f"Would remove {component}")
        return
    ctx.logger.success(f"{component} removed ({', '.join(result.removed) or 'its own manager'})")


def register(group: click.Group) -> None:
    """
    Join ``noust server``: what :mod:`noust.cli.commands.server` calls.

    Args:
        group: The ``server`` group.
    """
    group.add_command(software)
