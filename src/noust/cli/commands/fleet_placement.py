# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet placement``: an application that runs on several servers.

A placement is the central's record of where an application runs. ``add`` records it (and touches no
server), ``status`` compares the application across its servers, ``rollout`` deploys one commit on all
of them, ``copy`` puts it on the servers that lack it, ``upstreams`` writes the list of servers for the
load balancer in front of them, and ``remove`` forgets the record. The commands are clients of the same
modules ``/api/fleet/placements`` is (:mod:`noust.fleet.placements`, :mod:`noust.fleet.placement_status`,
:mod:`noust.web.fleet_placement_actions`), and the actions run through the fleet engine like
``noust fleet run``.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.fleet import cli as fleet_cli
from noust.cli.commands.fleet_views import _run
from noust.core.exceptions import ValidationError

if TYPE_CHECKING:
    from noust.fleet.nodes import NodeManager
    from noust.fleet.placements import Placement, Placements


@fleet_cli.group("placement", cls=NoustGroup)
def placement_cli() -> None:
    """An application that runs on several servers: where, how it looks, rolling it out."""


def _csv(text: str) -> list[str]:
    """
    Split a comma-separated list.

    Args:
        text: ``web-1,web-2``.

    Returns:
        The non-empty parts, trimmed.
    """
    return [part.strip() for part in text.split(",") if part.strip()]


def _registry() -> tuple[NodeManager, Placements]:
    """
    Open the node registry and the placements over its store.

    Returns:
        Both.
    """
    from noust.fleet.nodes import NodeManager
    from noust.fleet.placements import Placements

    manager = NodeManager()
    return manager, Placements(manager.store)


def _placed(domain: str) -> tuple[NodeManager, Placements, Placement]:
    """
    Find the placement of an application.

    Args:
        domain: The application.

    Returns:
        The registry, the placements and the placement.

    Raises:
        ValidationError: When the application is not placed.
    """
    from noust.fleet.placements import checked_domain

    manager, placements = _registry()
    found = placements.get(checked_domain(domain))
    if found is None:
        raise ValidationError(
            f"{domain} has no placement",
            details=f"Record where it runs: noust fleet placement add {domain} --nodes web-1,web-2",
            field="domain",
        )
    return manager, placements, found


def _describe(placement: Placement, members: list[str]) -> dict[str, Any]:
    """
    Describe a placement for JSON, as the API does.

    Args:
        placement: The placement.
        members: The servers it runs on now.

    Returns:
        The description.
    """
    data = placement.to_dict()
    data["labels"] = data.pop("selector")
    data["members"] = members
    return data


@placement_cli.command("list", read_only=True)
@json_option("Print the placements as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the applications that run on several servers (the central's own record)."""
    manager, placements = _registry()
    records = manager.list()
    listed = placements.list()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"placements": [_describe(p, placements.members(p, records)) for p in listed]}
            )
        )
        return
    if not listed:
        ctx.logger.info(
            "No application is placed on several servers. Record one: "
            "noust fleet placement add shop.example.com --nodes web-1,web-2"
        )
        return
    ctx.logger.table(
        ["Application", "Servers", "Label", "Strategy"],
        [
            [
                p.domain,
                ", ".join(placements.members(p, records)) or "-",
                ",".join(f"{k}={v}" for k, v in p.selector.items()) or "-",
                f"{p.serial or 1} at a time"
                + (f", {p.canary} first" if p.canary else "")
                + ("" if p.rollback_on_failure else ", no rollback"),
            ]
            for p in listed
        ],
    )


@placement_cli.command("add")
@click.argument("domain")
@click.option("--nodes", default=None, help="Comma-separated servers it runs on.")
@click.option(
    "--label",
    "labels",
    multiple=True,
    metavar="KEY=VALUE",
    help="Every server carrying this label runs it too (repeat: all must match).",
)
@click.option("--serial", default=None, help="Servers an update goes to at a time: 2 or 25%.")
@click.option(
    "--max-failures", type=click.IntRange(min=0), default=None, help="Failures that stop an update."
)
@click.option("--canary", default=None, help="The server that goes first and alone.")
@click.option(
    "--rollback/--no-rollback",
    "rollback",
    default=None,
    help="Put the servers already moved back when an update stops (the default).",
)
@json_option("Print the placement as JSON.")
@pass_context
def add_command(
    ctx: Context,
    domain: str,
    nodes: str | None,
    labels: tuple[str, ...],
    serial: str | None,
    max_failures: int | None,
    canary: str | None,
    rollback: bool | None,
) -> None:
    """
    Record where DOMAIN runs, or change it; what you do not give is kept.

    This touches no server: it is the central's own record. Put the application
    on a server that lacks it with 'noust fleet placement copy'.
    """
    from noust.fleet.audit import audit
    from noust.fleet.labels import parse_selector
    from noust.fleet.nodes import cli_actor
    from noust.fleet.placements import UNSET, checked_domain

    manager, placements = _registry()
    key = checked_domain(domain)
    if ctx.dry_run:
        ctx.logger.info(f"would record where {key} runs")
        return
    saved = placements.save(
        key,
        nodes=_csv(nodes) if nodes is not None else None,
        selector=parse_selector(labels) if labels else None,
        serial=(int(serial) if serial and serial.isdigit() else serial) if serial else UNSET,
        max_failures=max_failures if max_failures is not None else UNSET,
        canary=canary if canary else UNSET,
        rollback_on_failure=rollback if rollback is not None else UNSET,
        actor=cli_actor(),
    )
    audit("fleet.placement.save", "success", resource=f"app:{key}")
    members = placements.members(saved, manager.list())
    if ctx.json_output:
        click.echo(json.dumps(_describe(saved, members)))
        return
    ctx.logger.success(f"{key} runs on {', '.join(members) or 'no server yet'}")
    ctx.logger.info(f"See it across them: noust fleet placement status {key}")


@placement_cli.command("remove")
@click.argument("domain")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask.")
@pass_context
def remove_command(ctx: Context, domain: str, assume_yes: bool) -> None:
    """Forget where DOMAIN runs. Nothing is touched on any server."""
    from noust.fleet.audit import audit

    _, placements, found = _placed(domain)
    if ctx.dry_run:
        ctx.logger.info(f"would forget where {found.domain} runs")
        return
    if not assume_yes and not click.confirm(
        f"Forget where {found.domain} runs? The servers keep it.", default=False
    ):
        ctx.logger.info("Nothing was changed.")
        return
    placements.remove(found.domain)
    audit("fleet.placement.remove", "success", resource=f"app:{found.domain}")
    ctx.logger.success(f"{found.domain} is no longer tracked across servers")


@placement_cli.command("status", read_only=True)
@click.argument("domain")
@json_option("Print the placement and its status as JSON.")
@pass_context
def status_command(ctx: Context, domain: str) -> None:
    """Show DOMAIN across its servers: which commit each serves, and who differs."""
    from noust.fleet.aggregate import cli_asker
    from noust.fleet.placement_status import placement_status

    manager, _, found = _placed(domain)
    status = placement_status(found, manager=manager, asker=cli_asker(), refresh=True)
    if ctx.json_output:
        click.echo(json.dumps({"placement": _describe(found, status["members"]), "status": status}))
        return
    logger = ctx.logger
    logger.header(found.domain)
    if not status["servers"]:
        logger.info("It runs on no server right now.")
        return
    logger.table(
        ["Server", "State", "Commit", "Sync", "Note"],
        [
            [
                row["node"],
                row["status"] or "-",
                row["commit"] or "-",
                row["sync"],
                row["message"] or "",
            ]
            for row in status["servers"]
        ],
    )
    reference = status["reference_commit"]
    if status["in_sync"]:
        logger.success(f"In sync on {reference}")
    else:
        logger.warning(f"Not in sync (most servers are on {reference or 'no known commit'})")
        logger.info(f"Roll one commit out to all: noust fleet placement rollout {found.domain}")


def _strategy_options(function: Any) -> Any:
    """Add the options every action over a placement takes to a command."""
    for option in reversed(
        (
            click.option("--serial", default=None, help="Servers at a time: 2 or 25%."),
            click.option(
                "--max-failures",
                type=click.IntRange(min=-1),
                default=None,
                help="Failures that stop it; -1 never stops.",
            ),
            click.option("--plan", "plan_only", is_flag=True, help="Only show what would happen."),
            click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask first."),
        )
    ):
        function = option(function)
    return function


@placement_cli.command("rollout")
@click.argument("domain")
@click.option("--commit", default=None, help="Deploy exactly this commit on every server.")
@click.option("--tag", default=None, help="Deploy the commit this tag points at.")
@click.option("--force", is_flag=True, help="Rebuild even a server already on it.")
@click.option("--canary", default=None, help="The server that goes first and alone.")
@click.option(
    "--rollback/--no-rollback",
    "rollback",
    default=None,
    help="Put the servers already moved back if it stops (the placement's own by default).",
)
@_strategy_options
@json_option("Print the plan, then the job's result, as JSON.")
@pass_context
def rollout_command(
    ctx: Context,
    domain: str,
    commit: str | None,
    tag: str | None,
    force: bool,
    canary: str | None,
    rollback: bool | None,
    serial: str | None,
    max_failures: int | None,
    plan_only: bool,
    assume_yes: bool,
) -> None:
    """
    Roll DOMAIN out over its servers so that they all run the same commit.

    The first server goes alone and builds the head of the branch (or the commit or
    tag you give); the commit it ends on is what the others are asked for. When the
    rollout stops, the servers already moved are put back by their previous release.
    The exit status is 1 when any server failed.
    """
    from noust.web.fleet_placement_actions import rollout_request

    manager, placements, found = _placed(domain)
    fleet_request = rollout_request(
        found,
        placements.members(found, manager.list()),
        commit=commit,
        tag=tag,
        force=force,
        rollback_on_failure=rollback,
        serial=(int(serial) if serial and serial.isdigit() else serial) if serial else None,
        max_failures=max_failures,
        canary=canary,
    )
    _run(ctx, fleet_request, assume_yes, plan_only)


@placement_cli.command("copy")
@click.argument("domain")
@click.option("--from", "from_node", default=None, help="The server to copy it from.")
@click.option("--to", "to", default=None, help="Comma-separated servers to copy it to.")
@click.option("--new-domain", default=None, help="Make the copy under another domain.")
@click.option(
    "--no-secrets", is_flag=True, help="Leave the secret values out; the servers will ask for them."
)
@click.option(
    "--no-match-commit", is_flag=True, help="Keep the commit the import builds (the branch head)."
)
@click.option("--plan", "plan_only", is_flag=True, help="Only show what would happen.")
@click.option("-y", "--yes", "assume_yes", is_flag=True, help="Do not ask first.")
@json_option("Print the plan, then the job's result, as JSON.")
@pass_context
def copy_command(
    ctx: Context,
    domain: str,
    from_node: str | None,
    to: str | None,
    new_domain: str | None,
    no_secrets: bool,
    no_match_commit: bool,
    plan_only: bool,
    assume_yes: bool,
) -> None:
    """
    Put DOMAIN on the servers that lack it, from one that has it.

    The source's own export (with its secret values) is imported on each destination, then
    brought to the commit the source serves. The document passes through this central and is
    kept nowhere.
    """
    from noust.fleet.aggregate import cli_asker
    from noust.fleet.placement_status import placement_status
    from noust.web.fleet_placement_actions import copy_request

    manager, _, found = _placed(domain)
    status = (
        placement_status(found, manager=manager, asker=cli_asker(), refresh=True)
        if from_node is None or to is None
        else None
    )
    fleet_request = copy_request(
        found,
        status,
        from_node=from_node,
        to=_csv(to) if to is not None else None,
        new_domain=new_domain,
        secrets=not no_secrets,
        match_commit=not no_match_commit,
    )
    _run(ctx, fleet_request, assume_yes, plan_only)


@placement_cli.command("upstreams", read_only=True)
@click.argument("domain")
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["nginx", "list", "json"]),
    default="nginx",
    show_default=True,
    help="nginx: an upstream block. list: address:port per serving server. json: everything.",
)
@click.option("--port", type=click.IntRange(1, 65535), default=443, show_default=True)
@pass_context
def upstreams_command(ctx: Context, domain: str, fmt: str, port: int) -> None:
    """
    Write the servers DOMAIN runs on for the load balancer in front of them.

    Noust routes no traffic between servers; the balancer is yours. The servers that do not
    serve right now are marked down.
    """
    from noust.fleet.aggregate import cli_asker
    from noust.fleet.placement_status import placement_status
    from noust.fleet.placement_upstreams import render_upstreams, upstream_servers

    manager, _, found = _placed(domain)
    status = placement_status(found, manager=manager, asker=cli_asker(), refresh=True)
    addresses = {record.name: record.ssh_host for record in manager.list()}
    servers = upstream_servers(status, addresses, port=port)
    click.echo(render_upstreams(found.domain, servers, fmt), nl=False)
