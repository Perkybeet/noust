# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust node`` command group: the servers this central manages.

Enrollment starts here and finishes on the node:

1. ``noust node key web-2`` prints this central's key for web-2 and the
   ``noust fleet authorize`` command to run on web-2.
2. On web-2, that command prints a join code.
3. ``noust node add web-2 --ssh root@web2.example.com --join-code -`` reads
   the code from stdin (never from argv, where ``ps`` shows it), pins web-2's
   host key from it and checks the tunnel and the token.

A thin front end over :class:`~noust.fleet.nodes.NodeManager`, the same one
``/api/nodes`` uses.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NodeError

if TYPE_CHECKING:
    from noust.fleet.nodes import NodeManager


@click.group("node", cls=NoustGroup)
def cli() -> None:
    """Manage the servers this central manages, through their API over SSH tunnels."""


def _manager() -> NodeManager:
    """
    Build the node registry. Imported here: it needs httpx, which only the
    commands that talk to nodes should require.

    Returns:
        The registry.
    """
    from noust.fleet.nodes import NodeManager

    return NodeManager()


def _read_join_code(value: str | None) -> str:
    """
    Get the join code without it ever appearing in argv or on the screen.

    Args:
        value: ``-`` to read standard input, None to prompt with the input
            hidden, anything else is the code itself (discouraged: argv is
            visible in ``ps``).

    Returns:
        The code.
    """
    if value == "-":
        return sys.stdin.readline().strip()
    if value is None:
        return str(click.prompt("Join code", hide_input=True)).strip()
    return value


@cli.command("key")
@click.argument("name")
@json_option("Print the key and the authorize command as JSON.")
@pass_context
def key_command(ctx: Context, name: str) -> None:
    """
    Print this central's key for NAME and the command that authorizes it on NAME.

    The first step of adding a node. The key pair is generated once, for NAME
    alone; the node restricts it to forwarding its console port.
    """
    from noust.fleet.models import parse_public_key

    manager = _manager()
    key = manager.central_public_key(name)
    command = manager.authorize_command(name)
    fingerprint = parse_public_key(key).fingerprint
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "node": name,
                    "public_key": key,
                    "fingerprint": fingerprint,
                    "authorize_command": command,
                }
            )
        )
        return
    logger = ctx.logger
    logger.info(f"This central's key for {name} ({fingerprint}). On {name}, as root, run:")
    logger.blank()
    click.echo(command)
    logger.blank()
    logger.info(
        f"It prints a join code. Then, here: noust node add {name} --ssh root@<address> "
        "--join-code -   (and paste the code)"
    )


@cli.command("add")
@click.argument("name")
@click.option("--ssh", "ssh_target", required=True, help="USER@HOST[:PORT] the node is reached at.")
@click.option(
    "--join-code",
    default=None,
    help="The code 'noust fleet authorize' printed; '-' reads it from stdin. "
    "Omitted, it is asked for without echo.",
)
@json_option("Print the registered node as JSON.")
@pass_context
def add_command(ctx: Context, name: str, ssh_target: str, join_code: str | None) -> None:
    """
    Register NAME from the join code it printed, and check the tunnel and the token.

    Needs two-factor sign-in enabled on this central. Nothing is kept unless
    the node answers with the token.
    """
    from noust.fleet.audit import audit

    if ctx.dry_run:
        # NodeManager.add() pins a host key, stores the token, writes the
        # node's row and opens a real tunnel to call the node - too much to
        # rehearse truthfully, so nothing runs at all, the same way
        # 'noust central run' and 'noust central seal' answer under
        # --dry-run without reaching their own real work.
        ctx.logger.info(f"would register node {name} at {ssh_target} from the join code")
        return
    code = _read_join_code(join_code)
    try:
        record = _manager().add(name, ssh_target=ssh_target, join_code=code)
    except NodeError as exc:
        audit("fleet.node.add", "failure", resource=f"node:{name}", detail=exc.message)
        raise
    audit(
        "fleet.node.add",
        "success",
        resource=f"node:{name}",
        detail=f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}, Noust {record.version}",
    )
    if ctx.json_output:
        click.echo(json.dumps(record.to_dict()))
        return
    ctx.logger.success(f"Node {name} added: Noust {record.version or 'unknown'}, reachable")


@cli.command("list")
@json_option("Print the nodes as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the nodes this central manages, with what it last learnt about each."""
    records = _manager().list()
    if ctx.json_output:
        click.echo(json.dumps({"nodes": [record.to_dict() for record in records]}))
        return
    logger = ctx.logger
    if not records:
        logger.info("This central manages no nodes. Add one with 'noust node key NAME'.")
        return
    logger.table(
        ["Name", "SSH", "Status", "Version", "Last seen"],
        [
            [
                record.name,
                f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
                record.status,
                record.version or "-",
                record.last_seen or "never",
            ]
            for record in records
        ],
    )


@cli.command("show")
@click.argument("name")
@json_option("Print the node and its tunnel as JSON.")
@pass_context
def show_command(ctx: Context, name: str) -> None:
    """Show one node: its address, pinned host key, tunnel and last status."""
    manager = _manager()
    record = manager.get(name)
    tunnel = manager.tunnels.status(name)
    public = manager.keys.public_key(name)
    payload = {
        **record.to_dict(),
        "tunnel": tunnel,
        "central_key_fingerprint": public.fingerprint if public else None,
    }
    if ctx.json_output:
        click.echo(json.dumps(payload))
        return
    logger = ctx.logger
    logger.header(f"Node {record.name}")
    logger.key_value("SSH", f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}")
    logger.key_value("Host key", record.host_key)
    logger.key_value("Central key", payload["central_key_fingerprint"] or "none")
    logger.key_value("Console", f"127.0.0.1:{record.console_port} on the node")
    logger.key_value("Status", record.status)
    logger.key_value("Version", record.version or "-")
    logger.key_value("Last seen", record.last_seen or "never")
    logger.key_value(
        "Tunnel", f"open on 127.0.0.1:{tunnel['local_port']}" if tunnel["open"] else "closed"
    )
    logger.key_value("Added", record.created_at or "-")


@cli.command("test")
@click.argument("name")
@json_option("Print the result as JSON.")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """
    Check NAME end to end: the tunnel, the token and the API. Exits 1 when it fails.

    An unreachable node is reported with ssh's own words.
    """
    result = _manager().test(name)
    if ctx.json_output:
        click.echo(json.dumps({"node": name, **result}))
    elif result["reachable"]:
        ctx.logger.success(
            f"{name} answered: Noust {result['version'] or 'unknown'}, {result['latency_ms']} ms"
        )
    else:
        ctx.logger.error(f"{name}: {result['error']}")
        if result["details"]:
            click.echo(result["details"])
    if not result["reachable"]:
        raise SystemExit(1)


@cli.command("remove")
@click.argument("name")
@click.option(
    "--no-revoke",
    is_flag=True,
    help="Do not ask the node to revoke this central's token.",
)
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@json_option("Print the warnings as JSON.")
@pass_context
def remove_command(ctx: Context, name: str, no_revoke: bool, force: bool) -> None:
    """
    Stop managing NAME: revoke its token there, close its tunnel, delete its key and token here.

    The central cannot edit the node's authorized_keys (it has no shell
    there); the warnings say what to run on the node to finish.
    """
    from noust.fleet.audit import audit

    manager = _manager()
    manager.get(name)
    if ctx.dry_run:
        # remove() revokes the token over a real call to the node, closes
        # the tunnel and deletes secrets and the store row: rehearsed the
        # same way 'noust node add' is, by not running any of it.
        ctx.logger.info(
            f"would forget node {name}"
            + ("" if no_revoke else " and ask it to revoke this central's token")
        )
        return
    if not force and not click.confirm(
        f"Stop managing {name}? This central forgets its key and token", default=False
    ):
        ctx.logger.info("Cancelled")
        return
    warnings = manager.remove(name, revoke=not no_revoke)
    audit(
        "fleet.node.remove",
        "success",
        resource=f"node:{name}",
        detail="; ".join(warnings) if warnings else None,
    )
    if ctx.json_output:
        click.echo(json.dumps({"node": name, "removed": True, "warnings": warnings}))
        return
    ctx.logger.success(f"Node {name} removed from this central")
    for warning in warnings:
        ctx.logger.warning(warning)
