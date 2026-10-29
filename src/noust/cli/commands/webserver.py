# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust webserver``: which web server serves each site, and moving them to the other one.

``webserver`` in the configuration is the web server **new** applications are put behind; each
application keeps the one it was deployed behind. ``status`` says what is behind each and which one
holds ports 80 and 443; ``plan`` says what moving applications to the other one would do, site by
site and line by line, and changes nothing; ``migrate`` does it. They are
:func:`~noust.deployers.webserver_move_plan.plan_move` and
:func:`~noust.deployers.webserver_move.move`, which ``/api/webserver`` and
``/api/apps/{d}/webserver`` call too. This module only parses, presents and asks.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.config import DEFAULT_CONFIG, Config
from noust.core.logger import Logger
from noust.core.webserver_switch import usage
from noust.deployers import webserver_move_plan as planning
from noust.deployers.webserver_move import move, require_ready
from noust.deployers.webserver_move_plan import (
    MovePlan,
    SitePlan,
    display,
    other_than,
    plan_move,
)

_F = TypeVar("_F", bound=Callable[..., Any])

#: The two web servers the command takes.
SERVERS = click.Choice(["nginx", "apache"])


@click.group("webserver", cls=NoustGroup)
def cli() -> None:
    """Which web server serves each site, and moving them to the other one."""


# -- status ---------------------------------------------------------------------------------


@cli.command("status", read_only=True)
@json_option("Print what is behind each web server as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """
    Show the web server of new applications and what is behind each.

    The setting `webserver` is the one new applications are put behind; every application
    keeps the one it was deployed behind until it is moved with 'noust webserver migrate'. Ports
    80 and 443 belong to the web server that is running.
    """
    both = planning.managers_between("nginx", "apache")
    counts = usage()
    setting = str(Config().get("webserver", DEFAULT_CONFIG["webserver"]))
    servers: dict[str, dict[str, Any]] = {}
    for manager in (both.source, both.target):
        status = manager.get_status()
        name = manager.backend.name
        servers[name] = {
            "installed": status.installed,
            "running": status.active,
            "starts_at_boot": status.enabled,
            "version": status.version,
            "applications": counts.applications.get(name, 0),
            "sites": counts.sites.get(name, 0),
        }
    running = [name for name, item in servers.items() if item["running"]]
    holder = running[0] if len(running) == 1 else None
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"webserver": setting, "holds_ports": holder, "servers": servers}, default=str
            )
        )
        return
    logger = ctx.logger
    logger.key_value("For new applications", f"{display(setting)} (the setting webserver)")
    for name, item in servers.items():
        state = (
            "running"
            if item["running"]
            else ("installed, not running" if item["installed"] else "not installed")
        )
        logger.key_value(
            display(name),
            f"{state}; {_count(item['applications'], 'application')} and "
            f"{_count(item['sites'], 'site')} behind it",
        )
    logger.key_value(
        "Ports 80 and 443",
        f"held by {display(holder)}" if holder else "not held by one web server",
    )
    other = other_than(setting)
    behind = servers[other]["applications"] + servers[other]["sites"]
    if behind:
        logger.info(
            f"{behind} are behind {display(other)}, which is not the setting. Move them with: "
            f"noust webserver migrate --to {setting} --all"
        )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}{'' if number == 1 else 's'}"


# -- plan and migrate -----------------------------------------------------------------------


def _options(function: _F) -> _F:
    """
    Give a command the sites it is about and where they go.

    Args:
        function: The command's function.

    Returns:
        The same function, with ``DOMAINS``, ``--to`` and ``--all``.
    """
    for decorator in reversed(
        (
            click.argument("domains", nargs=-1),
            click.option(
                "--to",
                "target",
                required=True,
                type=SERVERS,
                help="The web server to move them to.",
            ),
            click.option(
                "--all",
                "everything",
                is_flag=True,
                default=False,
                help="Every application and site behind the other web server: what a handover of "
                "ports 80 and 443 moves.",
            ),
        )
    ):
        function = decorator(function)
    return function


def _planned(target: str, domains: Sequence[str], everything: bool) -> MovePlan:
    """
    Plan what the command line asked for.

    Args:
        target: The web server to move to.
        domains: The sites named.
        everything: Whether every site was asked for.

    Returns:
        The plan.

    Raises:
        click.UsageError: Neither sites nor ``--all`` were given, or both.
    """
    if everything == bool(domains):
        raise click.UsageError(
            f"Name the sites to move, or use --all for every one behind {display(other_than(target))}."
        )
    return plan_move(
        target,
        None if everything else list(domains),
        managers=planning.managers_between(other_than(target), target),
    )


def print_plan(logger: Logger, plan: MovePlan, *, show: str | None = None) -> None:
    """
    Render a plan for a person.

    Args:
        logger: Where it is written.
        plan: The plan.
        show: A site whose translated text is printed in full.
    """
    how = (
        f"{display(plan.target)} takes ports 80 and 443 from {display(plan.source)} (a handover)"
        if plan.mode == "handover"
        else f"{display(plan.target)} is serving, so each site moves on its own"
    )
    logger.key_value("Move", f"{display(plan.source)} -> {display(plan.target)}: {how}")
    if plan.skipped:
        logger.key_value("Already there", ", ".join(plan.skipped))
    if plan.modules:
        logger.key_value("Apache modules", ", ".join(plan.modules))
    for site in plan.sites:
        _print_site(logger, site)
    if plan.blockers:
        logger.blank()
        logger.warning("This cannot go ahead as it stands:")
        for item in plan.blockers:
            logger.list_item(item.text)
            if item.hint:
                logger.list_item(item.hint, indent=6)
    if show is not None:
        match = next((site for site in plan.sites if site.domain == show), None)
        if match is None or not match.text:
            raise click.UsageError(f"There is no translated site for {show} in this plan.")
        logger.blank()
        click.echo(match.text.rstrip("\n"))


def _print_site(logger: Logger, site: SitePlan) -> None:
    state = "can move" if site.movable else "cannot move"
    logger.blank()
    logger.key_value(site.domain, f"{state} ({site.kind})")
    for item in site.blockers:
        logger.warning(item.text)
        if item.hint:
            logger.list_item(item.hint, indent=6)
    if site.keeps_operator_site:
        logger.info("Your own site stays as it is; only the application moves.")
    if site.translation is None:
        return
    if site.writes:
        logger.key_value("New site", site.target_path, indent=4)
    for finding in site.findings:
        if finding.severity == "notes":
            continue
        logger.warning(finding.sentence.text)
        if finding.sentence.hint:
            logger.list_item(finding.sentence.hint, indent=6)
    notes = sorted({finding.directive for finding in site.findings if finding.severity == "notes"})
    if notes:
        logger.info(
            f"Carried over in another form or left out without a change: {', '.join(notes)}"
        )


@cli.command("plan", read_only=True)
@_options
@click.option(
    "--show",
    metavar="DOMAIN",
    default=None,
    help="Print the translated site of this one in full.",
)
@json_option("Print the plan as JSON.")
@pass_context
def plan_command(
    ctx: Context, domains: tuple[str, ...], target: str, everything: bool, show: str | None
) -> None:
    """
    Show what moving sites to a web server would do. Changes nothing.

    Name the applications or sites to move, or use --all for every one behind the other web
    server. Each site is translated from one server's configuration to the other's and the plan says,
    line by line, what the new site will not do; a site you wrote yourself is never rewritten.
    Ports 80 and 443 belong to one web server at a time: when the one you move to is not running the
    move is a handover of the ports and takes every site at once.
    """
    plan = _planned(target, domains, everything)
    if ctx.json_output:
        click.echo(json.dumps(plan.to_dict(), default=str))
        return
    print_plan(ctx.logger, plan, show=show)


@cli.command("migrate", gates_json=True)
@_options
@click.option(
    "--accept-losses",
    is_flag=True,
    default=False,
    help="Go ahead although the new sites will not do everything the old ones did.",
)
@click.option("--yes", "-y", is_flag=True, default=False, help="Do not ask for confirmation.")
@json_option("Print the plan (or, with --yes, its result) as JSON.")
@pass_context
def migrate_command(
    ctx: Context,
    domains: tuple[str, ...],
    target: str,
    everything: bool,
    accept_losses: bool,
    yes: bool,
) -> None:
    """
    Move sites to the other web server, and put everything back if one does not answer.

    The sites are translated, the new web server tests each one in its own configuration before
    anything is written, then it takes over (the ports too, when it is not serving) and every site
    is asked for through it with its application's health check. If the new server refuses a site,
    does not start, or a site does not answer, everything is put back as it was. A site you wrote
    yourself is never rewritten: it moves only when you already wrote its equivalent.
    """
    plan = _planned(target, domains, everything)
    if not ctx.json_output:
        print_plan(ctx.logger, plan)
    elif not (yes or ctx.dry_run):
        # A script asked for JSON without --yes: there is nobody to confirm, so it gets the plan.
        click.echo(json.dumps({"plan": plan.to_dict()}, default=str))
        return
    # What cannot go ahead is said before anything is asked.
    require_ready(plan, accept_losses=True, confirm=True)

    if plan.losses and not accept_losses and not yes and not ctx.dry_run:
        click.confirm(
            f"The new sites will not do {len(plan.losses)} thing(s) the old ones did. Go ahead anyway?",
            abort=True,
        )
        accept_losses = True
    if not yes and not ctx.dry_run:
        click.confirm(f"{plan.confirmation().text}. Go ahead?", abort=True)

    result = move(
        plan,
        accept_losses=accept_losses or ctx.dry_run,
        confirm=True,
        managers=planning.managers_between(plan.source, plan.target),
        logger=ctx.logger,
    )
    if ctx.json_output:
        click.echo(
            json.dumps({"plan": plan.to_dict(), "result": dataclasses.asdict(result)}, default=str)
        )
        return
    if result.rehearsed:
        ctx.logger.info("Rehearsal: nothing was changed")
        return
    for moved in result.sites:
        ctx.logger.success(f"{moved.domain} is behind {display(result.target)}")
    for warning in result.warnings:
        ctx.logger.warning(warning)
    setting = str(Config().get("webserver", DEFAULT_CONFIG["webserver"]))
    if setting != result.target:
        ctx.logger.info(
            f"New applications still go behind {display(setting)}; change that with: "
            f"noust config set webserver {result.target}"
        )
