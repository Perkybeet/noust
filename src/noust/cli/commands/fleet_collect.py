# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet collect``: the copies a central takes from its servers (item 195).

A central comes for the copies of the servers it manages, through the tunnel it already has, and keeps
them in its own backup directory: nothing is opened on the office router, and a server holds no
credential of the place its copies live. ``status`` says how it is going, ``enable`` and ``disable``
turn it on for the fleet, ``follow`` gives one server its own answer, ``set`` changes the speed limit,
the retention and the room kept free, ``run`` takes what is new now, ``list`` shows what is held,
``delete`` lets a copy go and ``restore`` gives one back to a server (the one it came from, or
another). The commands are clients of the module the API is (:mod:`noust.fleet.collect`).
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.fleet_group import cli as fleet_cli
from noust.core.audit import record as record_audit
from noust.core.exceptions import ValidationError


@fleet_cli.group("collect", cls=NoustGroup)
def collect_cli() -> None:
    """Take the copies of the servers this central manages, through its tunnels."""


def _line(row: dict[str, Any]) -> list[str]:
    """
    Args:
        row: One server's status.

    Returns:
        The cells of its row.
    """
    from noust.core.utils import format_bytes as format_size

    state = row["last_error_code"] or ("followed" if row["following"] else "not followed")
    own = {True: "on", False: "off", None: "fleet"}[row["follow"]]
    return [
        row["node"],
        own,
        state,
        f"{row['held']['count']} ({format_size(row['held']['bytes'])})",
        row["last_ok_at"] or "-",
    ]


@collect_cli.command("status", read_only=True)
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say how collecting is going: who is followed, the disk, what each server holds."""
    from noust.core.utils import format_bytes as format_size
    from noust.fleet.collect.status import status_of

    status = status_of()
    if ctx.json_output:
        click.echo(json.dumps(status))
        return
    count = status["following"]
    ctx.logger.key_value("Collected", f"{count['following']} of {count['of']} servers")
    settings = status["settings"]
    ctx.logger.key_value("Folder", status["directory"])
    space = status["space"]
    ctx.logger.key_value(
        "Disk",
        f"{format_size(space['used'])} held, {format_size(space['free'])} free"
        f" ({format_size(space['min_free'])} kept free)",
    )
    limit = settings["rate_limit_kbps"]
    ctx.logger.key_value("Speed limit", f"{limit} KB/s" if limit else "none")
    ctx.logger.table(
        ["Server", "Own answer", "State", "Held", "Last whole pass"],
        [_line(r) for r in status["nodes"]],
    )
    if not settings["enabled"] and not count["following"]:
        ctx.logger.info("Turn it on for the fleet: noust fleet collect enable")


@collect_cli.command("enable")
@pass_context
def enable_command(ctx: Context) -> None:
    """Collect the copies of every server that has no answer of its own."""
    _enabled(ctx, True)


@collect_cli.command("disable")
@pass_context
def disable_command(ctx: Context) -> None:
    """Stop collecting from the servers that follow the fleet. What is held stays."""
    _enabled(ctx, False)


def _enabled(ctx: Context, value: bool) -> None:
    """
    Turn collecting on or off for the fleet.

    Args:
        ctx: The command's context.
        value: The answer.
    """
    from noust.fleet.collect.settings import SettingsStore

    if ctx.dry_run:
        ctx.logger.info(f"Would {'enable' if value else 'disable'} collecting for the fleet")
        return
    SettingsStore().set(enabled=value)
    record_audit("central_backup.settings", target="central", details={"enabled": value})
    ctx.logger.success("Collecting is on for the fleet" if value else "Collecting is off")


@collect_cli.command("follow")
@click.argument("node")
@click.argument("answer", type=click.Choice(["on", "off", "fleet"]))
@pass_context
def follow_command(ctx: Context, node: str, answer: str) -> None:
    """Give NODE its own answer (on or off), or take it back to the fleet's (fleet)."""
    from noust.fleet.collect.settings import Follows

    value = {"on": True, "off": False, "fleet": None}[answer]
    if ctx.dry_run:
        ctx.logger.info(f"Would set the answer of {node} to {answer}")
        return
    Follows().set_follow(node, value)
    record_audit("central_backup.settings", target=f"node:{node}", details={"follow": value})
    ctx.logger.success(f"{node}: {answer}")


@collect_cli.command("set")
@click.argument("knob")
@click.argument("value")
@pass_context
def set_command(ctx: Context, knob: str, value: str) -> None:
    """
    Change a knob: rate_limit_kbps, poll_seconds, pass_hours, retention_count, retention_days,
    retention_gb, min_free_gb or kinds (comma-separated). VALUE none takes a limit off.
    """
    from noust.fleet.collect.settings import KEYS, SettingsStore

    if knob not in KEYS or knob == "enabled":
        raise ValidationError(
            f"No such setting: {knob!r}",
            details=f"The settings are: {', '.join(k for k in KEYS if k != 'enabled')}. "
            "Turn it on or off with 'noust fleet collect enable'.",
            field="knob",
        )
    parsed: Any
    if knob == "kinds":
        parsed = [part.strip() for part in value.split(",") if part.strip()]
    elif value.lower() in ("none", "off", "0") and knob not in ("poll_seconds", "pass_hours"):
        parsed = None
    elif knob in ("retention_gb", "min_free_gb"):
        parsed = _number(value, knob)
    else:
        parsed = int(_number(value, knob))
    if ctx.dry_run:
        ctx.logger.info(f"Would set {knob} to {parsed}")
        return
    SettingsStore().set(**{knob: parsed})
    record_audit("central_backup.settings", target="central", details={knob: parsed})
    ctx.logger.success(f"{knob}: {'none' if parsed is None else parsed}")


def _number(value: str, knob: str) -> float:
    """
    Args:
        value: What the operator typed.
        knob: The setting, for the error.

    Returns:
        The number.

    Raises:
        ValidationError: When it is not one.
    """
    try:
        return float(value)
    except ValueError as exc:
        raise ValidationError(f"{knob} is a number, not {value!r}", field=knob) from exc


@collect_cli.command("run")
@click.argument("node", required=False)
@click.option("--force", is_flag=True, help="Read the whole list even when nothing changed.")
@json_option("Print how each pass went as JSON.")
@pass_context
def run_command(ctx: Context, node: str | None, force: bool) -> None:
    """Take what is new from NODE (or from every followed server) now, in this terminal."""
    from noust.core.store import get_store
    from noust.fleet.collect.collector import NodeCollector
    from noust.fleet.collect.settings import Follows

    names = [node] if node else [n.name for n in get_store().list_nodes()]
    followed = [n for n in names if node or Follows().follows(n)]
    if ctx.dry_run:
        ctx.logger.info(f"Would take the copies of: {', '.join(followed) or 'nothing'}")
        return
    collector = NodeCollector()
    results = [collector.collect(name, force=force) for name in followed]
    if ctx.json_output:
        click.echo(json.dumps({"passes": [r.to_dict() for r in results]}))
    else:
        for result in results:
            ctx.logger.info(
                f"{result.node}: {result.state}, {result.fetched} taken, {result.held} already held"
            )
            for failure in result.failures:
                ctx.logger.warning(f"  {failure}")
            for note in result.notes:
                ctx.logger.info(f"  {note}")
    if any(r.state == "failed" for r in results):
        raise click.exceptions.Exit(1)


@collect_cli.command("list", read_only=True)
@click.option("--node", default=None, help="Only this server's copies.")
@click.option("--kind", default=None, help="Only this kind: app, database, volume or noust.")
@json_option("Print the copies as JSON.")
@pass_context
def list_command(ctx: Context, node: str | None, kind: str | None) -> None:
    """List the copies this central holds."""
    from noust.core.utils import format_bytes as format_size
    from noust.fleet.collect.records import CollectedStore

    copies = CollectedStore().list(node=node, kind=kind)
    if ctx.json_output:
        click.echo(json.dumps({"items": [c.to_dict() for c in copies], "total": len(copies)}))
        return
    if not copies:
        ctx.logger.info("The central holds no copy yet. See: noust fleet collect status")
        return
    ctx.logger.table(
        ["Server", "Kind", "Of", "Taken", "Size", "Checked", "Id"],
        [
            [c.node, c.kind, c.owner, c.created_at, format_size(c.size), c.verified, c.item_id]
            for c in copies
        ],
    )


@collect_cli.command("delete")
@click.argument("node")
@click.argument("kind")
@click.argument("item")
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def delete_command(ctx: Context, node: str, kind: str, item: str, yes: bool) -> None:
    """Let a copy go: its files are deleted here, and NODE is not asked for it again."""
    from noust.fleet.collect.collector import NodeCollector
    from noust.fleet.collect.records import HELD, CollectedStore

    copy = CollectedStore().get(node, kind, item)
    if copy is None or copy.state != HELD:
        raise ValidationError(
            f"The central holds no {kind} copy {item!r} of {node}",
            details="List what it holds: noust fleet collect list",
        )
    if ctx.dry_run:
        ctx.logger.info(f"Would let go of {copy.kind} {copy.item_id} of {node}")
        return
    if not yes:
        click.confirm(f"Delete the central's copy of {copy.owner} ({copy.item_id})?", abort=True)
    NodeCollector().let_go(copy, reason="operator")
    record_audit(
        "central_backup.delete",
        target=f"node:{node}",
        details={
            "kind": copy.kind,
            "copy": copy.item_id,
            "owner": copy.owner,
            "reason": "operator",
        },
    )
    ctx.logger.success(f"Let go of {copy.item_id}")


@collect_cli.command("restore")
@click.argument("node")
@click.argument("kind")
@click.argument("item")
@click.option("--to", "target_node", default=None, help="The server it goes to (default: NODE).")
@click.option("--domain", default=None, help="The application it goes into (applications, data).")
@click.option("--database", default=None, help="The database it is loaded into (dumps).")
@click.option("--engine", default=None, help="The engine of that database.")
@click.option("--new-name", default=None, help="Load a dump into a new database of this name.")
@click.option("--yes", "-y", is_flag=True, help="Do not ask.")
@pass_context
def restore_command(
    ctx: Context,
    node: str,
    kind: str,
    item: str,
    target_node: str | None,
    domain: str | None,
    database: str | None,
    engine: str | None,
    new_name: str | None,
    yes: bool,
) -> None:
    """
    Give a copy back to a server and restore it there.

    The copy is checked whole before a byte leaves; the server restores it with the way a restore
    always goes. Moving a copy to another server passes it through this central's line.
    """
    from noust.fleet.aggregate import Asker
    from noust.fleet.collect.layout import Layout
    from noust.fleet.collect.records import HELD, CollectedStore
    from noust.fleet.collect.restore import GiveBack
    from noust.fleet.collect.settings import SettingsStore
    from noust.fleet.collect.throttle import Throttle

    copy = CollectedStore().get(node, kind, item)
    if copy is None or copy.state != HELD:
        raise ValidationError(
            f"The central holds no {kind} copy {item!r} of {node}",
            details="List what it holds: noust fleet collect list",
        )
    target = target_node or node
    where = {
        k: v
        for k, v in {
            "domain": domain,
            "database": database,
            "engine": engine,
            "new_name": new_name,
        }.items()
        if v
    }
    if ctx.dry_run:
        ctx.logger.info(f"Would give {copy.item_id} back to {target}")
        return
    if not yes:
        click.confirm(
            f"Restore the copy of {copy.owner} on {target}? It replaces what is there.", abort=True
        )
    record_audit(
        "central_backup.restore",
        target=f"node:{target}",
        details={"from": node, "kind": copy.kind, "copy": copy.item_id, "owner": copy.owner},
    )
    result = GiveBack(
        copy=copy,
        target=target,
        layout=Layout.default(),
        asker=Asker(actor="cli:root", scope="admin", elevated=True),
        throttle=Throttle(SettingsStore().get().rate_bytes_per_second),
        log=ctx.logger.info,
        step=lambda what, percent: ctx.logger.info(f"{percent:3d}% {what}"),
    ).run(target_args=where)
    if ctx.json_output:
        click.echo(json.dumps(result))
    else:
        ctx.logger.success(f"Restored on {target}")
        if result.get("result", {}).get("command"):
            ctx.logger.info(f"Next, on {target}: {result['result']['command']}")
