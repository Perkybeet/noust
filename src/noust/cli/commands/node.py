# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust node`` command group: the servers this central manages.

Enrollment starts here and finishes on the node:

1. ``noust node key web-2`` prints this central's key for web-2 and the
   ``noust fleet authorize`` command to run on web-2.
2. On web-2, that command prints a join code.
3. ``noust node add web-2 --ssh web2.example.com --join-code -`` reads the
   code from stdin (never from argv, where ``ps`` shows it), pins web-2's host
   key from it and checks the tunnel and the token. The SSH account comes from
   the code: ``noust-tunnel`` on a 3.1 node.

``noust node rekey`` and ``noust node migrate-tunnel`` follow the same two
steps for a node already registered: a command for the node, then its code.

A thin front end over :class:`~noust.fleet.nodes.NodeManager`, the same one
``/api/nodes`` uses.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NodeError

if TYPE_CHECKING:
    from noust.core.store import NodeRecord
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
        f"It prints a join code. Then, here: noust node add {name} --ssh <address> "
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
@click.option(
    "--bastion",
    default=None,
    metavar="USER@HOST[:PORT]",
    help="Reach the node through this host. Its host key is pinned from "
    "--bastion-fingerprint or --bastion-host-key, never learnt on first use.",
)
@click.option("--bastion-fingerprint", default=None, help="SHA256:... of the bastion's host key.")
@click.option("--bastion-host-key", default=None, help="The bastion's ssh-ed25519 host key line.")
@click.option(
    "--timeout",
    "connect_timeout",
    type=click.IntRange(1, 300),
    default=None,
    help="Seconds to wait for the connection (default 10).",
)
@click.option(
    "--inventory",
    "inventory_file",
    type=click.Path(dir_okay=False, path_type=str),
    default=None,
    help="An inventory exported by the central the server comes from (noust node export): "
    "its labels, placements and the rest are recorded here once the server is added.",
)
@click.option(
    "--include-ssh",
    is_flag=True,
    help="With --inventory: also apply how that central reached the server (its wait, its bastion).",
)
@json_option("Print the registered node as JSON.")
@pass_context
def add_command(
    ctx: Context,
    name: str,
    ssh_target: str,
    join_code: str | None,
    bastion: str | None,
    bastion_fingerprint: str | None,
    bastion_host_key: str | None,
    connect_timeout: int | None,
    inventory_file: str | None,
    include_ssh: bool,
) -> None:
    """
    Register NAME from the join code it printed, and check the tunnel and the token.

    Needs two-factor sign-in enabled on this central. Nothing is kept unless
    the node answers with the token. A node with no address this central can reach
    is reached through a bastion (--bastion); authorize this central's key for the
    node there too (noust node ssh NAME --show prints the line).
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
    document = _read_inventory(inventory_file) if inventory_file else None
    code = _read_join_code(join_code)
    manager = _manager()
    try:
        chosen = (
            manager.resolve_bastion(
                bastion, host_key=bastion_host_key, fingerprint=bastion_fingerprint
            )
            if bastion
            else None
        )
        record = manager.add(
            name,
            ssh_target=ssh_target,
            join_code=code,
            bastion=chosen,
            connect_timeout=connect_timeout,
        )
    except NodeError as exc:
        audit("fleet.node.add", "failure", resource=f"node:{name}", detail=exc.message)
        raise
    audit(
        "fleet.node.add",
        "success",
        resource=f"node:{name}",
        detail=f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}, Noust {record.version}",
    )
    report = (
        _apply_inventory(manager, name, document, include_ssh=include_ssh)
        if document is not None
        else None
    )
    if ctx.json_output:
        click.echo(json.dumps({**record.to_dict(), "inventory": report}))
        return
    ctx.logger.success(f"Node {name} added: Noust {record.version or 'unknown'}, reachable")
    if report is not None:
        _print_inventory(ctx, report)


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
        ["Name", "SSH", "Status", "Version", "Access", "Last seen"],
        [
            [
                record.name,
                f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
                record.status,
                record.version or "-",
                _access_cell(record),
                record.last_seen or "never",
            ]
            for record in records
        ],
    )
    if any(record.ssh_user == "root" for record in records):
        logger.warning(
            "Nodes reached as root can be moved to the unprivileged tunnel account: "
            "noust node migrate-tunnel NAME"
        )


def _access_cell(record: NodeRecord) -> str:
    """
    Show the ceiling a node published, as a table cell.

    Args:
        record: The node.

    Returns:
        Such as ``read`` or ``admin + host``; ``unknown`` for a node that never said.
    """
    if record.access_level is None:
        return "unknown"
    return record.access_level + (" + host" if record.host_access else "")


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
    logger.key_value(
        "Access",
        _access_cell(record)
        + (" (the node enforces it)" if record.access_level else " (Noust 3.0, or not asked yet)"),
    )
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


@cli.command("rekey")
@click.argument("name")
@click.option(
    "--join-code",
    default=None,
    help="The code NAME printed for the new key; '-' reads it from stdin. Omitted, the "
    "rotation starts and the command to run on NAME is printed.",
)
@json_option("Print the command, or the node, as JSON.")
@pass_context
def rekey_command(ctx: Context, name: str, join_code: str | None) -> None:
    """
    Give NAME a new key: first the command to run on it, then the join code it prints.

    Without --join-code, a new key pair is generated (once: asking again
    prints the same command) and the command to authorize it on NAME is
    printed. With --join-code, the new key replaces the old one here, once
    NAME answers with it; otherwise nothing changes and the code can be
    pasted again. Authorizing the new key on NAME already removed the old one
    there.
    """
    _two_step(ctx, name, join_code, what="rekey")


@cli.command("migrate-tunnel")
@click.argument("name")
@click.option(
    "--join-code",
    default=None,
    help="The code NAME printed; '-' reads it from stdin. Omitted, the command to run "
    "on NAME is printed.",
)
@json_option("Print the command, or the node, as JSON.")
@pass_context
def migrate_tunnel_command(ctx: Context, name: str, join_code: str | None) -> None:
    """
    Move NAME from root to the unprivileged noust-tunnel account (a node enrolled by 3.0).

    Without --join-code, prints the command to run on NAME (Noust 3.1.1 or
    later there): it creates noust-tunnel, restricts it in sshd, installs this
    central's key for it and takes that key out of root's authorized_keys
    (--replace-root-key, matched by the key whatever the line's comment). With
    --join-code, this central switches to it once NAME answers; otherwise
    nothing changes here and the code can be pasted again.
    """
    _two_step(ctx, name, join_code, what="migrate")


def _two_step(ctx: Context, name: str, join_code: str | None, *, what: str) -> None:
    """
    Run either step of ``rekey`` or ``migrate-tunnel``.

    Args:
        ctx: The command's context.
        name: The node's name.
        join_code: None for the first step, the code (or ``-``) for the second.
        what: ``rekey`` or ``migrate``.
    """
    from noust.fleet.audit import audit

    manager = _manager()
    manager.get(name)
    action = f"fleet.node.{what}"
    command_name = "rekey" if what == "rekey" else "migrate-tunnel"
    if join_code is None:
        if ctx.dry_run:
            ctx.logger.info(
                f"would print the command that authorizes {name} again"
                + (", with a new key pair" if what == "rekey" else "")
            )
            return
        command = (
            manager.rekey_command(name) if what == "rekey" else manager.migrate_tunnel_command(name)
        )
        if ctx.json_output:
            click.echo(json.dumps({"node": name, "authorize_command": command}))
            return
        logger = ctx.logger
        logger.info(
            f"On {name}, as root (Noust {'3.1' if what == 'rekey' else '3.1.1'} or later "
            "there), run:"
        )
        logger.blank()
        click.echo(command)
        logger.blank()
        logger.info(
            f"It prints a join code. Then, here: noust node {command_name} {name} "
            "--join-code -   (and paste the code)"
        )
        return
    if ctx.dry_run:
        ctx.logger.info(f"would switch {name} to what the join code authorizes")
        return
    code = _read_join_code(join_code)
    try:
        record = (
            manager.rekey(name, join_code=code)
            if what == "rekey"
            else manager.migrate_tunnel(name, join_code=code)
        )
    except NodeError as exc:
        audit(action, "failure", resource=f"node:{name}", detail=exc.message)
        raise
    audit(
        action,
        "success",
        resource=f"node:{name}",
        detail=f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
    )
    if ctx.json_output:
        click.echo(json.dumps(record.to_dict()))
        return
    done = "has a new key" if what == "rekey" else f"is reached as {record.ssh_user}"
    ctx.logger.success(f"{name} {done}, and answered: Noust {record.version or 'unknown'}")


def _read_inventory(path: str) -> dict[str, Any]:
    """
    Read an inventory file.

    Args:
        path: The file ``noust node export`` wrote.

    Returns:
        Its document.

    Raises:
        NodeError: When it cannot be read or is not JSON.
    """
    from pathlib import Path

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise NodeError(f"Cannot read {path}", details=str(exc)) from exc
    try:
        document = json.loads(text)
    except ValueError as exc:
        raise NodeError(f"{path} is not JSON", details=str(exc)) from exc
    return document if isinstance(document, dict) else {}


def _apply_inventory(
    manager: NodeManager, name: str, document: dict[str, Any], *, include_ssh: bool
) -> dict[str, Any]:
    """
    Record an inventory for a node, and the audit entry of it.

    Args:
        manager: The registry.
        name: The node.
        document: The inventory.
        include_ssh: Also apply how the other central reached it.

    Returns:
        What was applied and left out.
    """
    from noust.fleet.audit import audit
    from noust.fleet.inventory import apply_inventory
    from noust.fleet.nodes import cli_actor

    report = apply_inventory(manager, name, document, actor=cli_actor(), include_ssh=include_ssh)
    audit(
        "fleet.node.inventory",
        "success",
        resource=f"node:{name}",
        detail=", ".join(item["part"] for item in report["applied"]) or "nothing to apply",
    )
    return report


def _print_inventory(ctx: Context, report: dict[str, Any]) -> None:
    """
    Say what applying an inventory did.

    Args:
        ctx: The CLI context.
        report: :func:`~noust.fleet.inventory.apply_inventory`'s answer.
    """
    applied = report["applied"]
    skipped = report["skipped"]
    for item in applied:
        ctx.logger.success(f"Inventory: {item['part']} recorded")
    for item in skipped:
        ctx.logger.warning(f"Inventory: {item['part']} left out: {item['reason']}")
    if not applied and not skipped:
        ctx.logger.info("The inventory had nothing to record.")


@cli.command("export", read_only=True)
@click.argument("name")
@click.option(
    "-o",
    "--output",
    "output",
    type=click.Path(dir_okay=False, path_type=str),
    default=None,
    help="Write the inventory to this file instead of printing it.",
)
@pass_context
def export_command(ctx: Context, name: str, output: str | None) -> None:
    """
    Write down what this central knows about NAME, to take it over on another central.

    The inventory is its labels, the applications placed on it, the machine it is at a
    provider, whether its monitoring is paused and how this central reaches it. It holds no
    secret: not the server's token, not this central's key for it. Move a server with it:
    authorize the new central on the server (noust fleet authorize), add the server there with
    noust node add NAME --inventory FILE, then remove it here.
    """
    from noust.core.fs import get_fs
    from noust.fleet.inventory import export_inventory

    document = export_inventory(_manager(), name)
    text = json.dumps(document, indent=2) + "\n"
    if output is None:
        click.echo(text, nl=False)
        return
    if ctx.dry_run:
        ctx.logger.info(f"would write the inventory of {name} to {output}")
        return
    from pathlib import Path

    fs = get_fs()
    fs.write_text(Path(output), text, mode=0o600)
    ctx.logger.success(f"Inventory of {name} written to {output}")


@cli.command("inventory")
@click.argument("name")
@click.argument("file", type=click.Path(dir_okay=False, path_type=str))
@click.option(
    "--include-ssh",
    is_flag=True,
    help="Also apply how the other central reached the server (its wait, its bastion).",
)
@json_option("Print what was applied as JSON.")
@pass_context
def inventory_command(ctx: Context, name: str, file: str, include_ssh: bool) -> None:
    """
    Record, for NAME already added here, the inventory another central exported for it.

    The file is checked whole before anything is written, and what cannot be applied here is
    said with the reason.
    """
    manager = _manager()
    manager.get(name)
    document = _read_inventory(file)
    if ctx.dry_run:
        ctx.logger.info(f"would record the inventory in {file} for {name}")
        return
    report = _apply_inventory(manager, name, document, include_ssh=include_ssh)
    if ctx.json_output:
        click.echo(json.dumps(report))
        return
    _print_inventory(ctx, report)


def _wait_words(options: dict[str, object]) -> str:
    """
    Say how long the central waits for a node's connection.

    Args:
        options: :meth:`NodeManager.describe_ssh`.

    Returns:
        Such as ``10 seconds (the default)`` or ``45 seconds``.
    """
    seconds = options["effective_timeout"]
    unit = "second" if seconds == 1 else "seconds"
    return f"{seconds} {unit}" + (" (the default)" if options["connect_timeout"] is None else "")


@cli.command(
    "ssh",
    read_only=lambda params: (
        params["show"]
        or not (
            params["connect_timeout"] is not None
            or params["default_timeout"]
            or params["bastion"]
            or params["no_bastion"]
        )
    ),
)
@click.argument("name")
@click.option(
    "--timeout",
    "connect_timeout",
    type=click.IntRange(1, 300),
    default=None,
    help="Seconds to wait for the connection (1 to 300).",
)
@click.option("--default-timeout", is_flag=True, help="Go back to the default wait (10 seconds).")
@click.option(
    "--bastion",
    default=None,
    metavar="USER@HOST[:PORT]",
    help="Reach the node through this host. Needs --bastion-fingerprint or --bastion-host-key "
    "for a new one: a host key is never learnt on first use.",
)
@click.option("--bastion-fingerprint", default=None, help="SHA256:... of the bastion's host key.")
@click.option("--bastion-host-key", default=None, help="The bastion's ssh-ed25519 host key line.")
@click.option("--no-bastion", is_flag=True, help="Connect to the node directly.")
@click.option(
    "--no-test",
    is_flag=True,
    help="Keep the change without opening the tunnel first (a node that is down now).",
)
@click.option(
    "--show",
    is_flag=True,
    help="Print the authorized_keys line to add on the bastion: one stream to the node, no shell. "
    "With --bastion it is the line for that bastion, and nothing is changed: ask for it first, "
    "then change the bastion once it lets the central in.",
)
@json_option("Print the options as JSON.")
@pass_context
def ssh_command(
    ctx: Context,
    name: str,
    connect_timeout: int | None,
    default_timeout: bool,
    bastion: str | None,
    bastion_fingerprint: str | None,
    bastion_host_key: str | None,
    no_bastion: bool,
    no_test: bool,
    show: bool,
) -> None:
    """
    Show or change how this central connects to NAME: its wait and its bastion.

    With no option it shows them. A change closes the tunnel, opens it again with
    the new options and keeps them only when NAME answers; otherwise the old ones
    are put back. The bastion carries bytes it cannot read: NAME's own host key is
    still pinned end to end, and the bastion's is pinned too.
    """
    from noust.fleet.audit import audit
    from noust.fleet.ssh_options import UNSET

    if bastion and no_bastion:
        raise click.UsageError("Give --bastion or --no-bastion, not both")
    if connect_timeout is not None and default_timeout:
        raise click.UsageError("Give --timeout or --default-timeout, not both")
    manager = _manager()
    manager.get(name)
    if show and (connect_timeout is not None or default_timeout or no_bastion):
        raise click.UsageError("--show only prints a line; it changes nothing")
    changing = (
        connect_timeout is not None or default_timeout or bool(bastion) or no_bastion
    ) and not show
    if changing and ctx.dry_run:
        ctx.logger.info(f"would change how {name} is connected to")
        return
    if changing:
        try:
            chosen = UNSET
            if bastion:
                chosen = manager.resolve_bastion(
                    bastion, host_key=bastion_host_key, fingerprint=bastion_fingerprint
                )
            elif no_bastion:
                chosen = None
            manager.set_ssh_options(
                name,
                connect_timeout=None if default_timeout else (connect_timeout or UNSET),
                bastion=chosen,
                test=not no_test,
            )
        except NodeError as exc:
            audit("fleet.node.ssh", "failure", resource=f"node:{name}", detail=exc.message)
            raise
        audit("fleet.node.ssh", "success", resource=f"node:{name}")
    options = manager.describe_ssh(name)
    line: str | None = None
    if show and (bastion or options["bastion"]):
        proposed = (
            manager.resolve_bastion(
                bastion, host_key=bastion_host_key, fingerprint=bastion_fingerprint
            )
            if bastion
            else None
        )
        line = manager.bastion_authorization(name, bastion=proposed)
    if ctx.json_output:
        click.echo(json.dumps({**options, "authorized_key_line": line}))
        return
    logger = ctx.logger
    if show:
        if line is None:
            raise NodeError(
                f"{name} has no bastion",
                details=f"Ask for the line of the one you mean to set: noust node ssh {name} "
                "--bastion USER@HOST --bastion-fingerprint SHA256:... --show",
            )
        logger.info("Add this line to the authorized_keys of the account on the bastion:")
        click.echo(line)
        return
    logger.header(f"Connection to {name}")
    logger.key_value("Waits", _wait_words(options))
    found = options["bastion"]
    if found is None:
        logger.key_value("Bastion", "no bastion: connected directly")
    else:
        logger.key_value("Bastion", f"{found['address']} (host key {found['fingerprint']})")
        logger.info(f"Show the line the bastion needs with: noust node ssh {name} --show")
    if changing:
        logger.success(f"{name} answers with these options")


@cli.command("pause")
@click.argument("name")
@click.option("--reason", default=None, help="Why, in a line: shown beside the node.")
@pass_context
def pause_command(ctx: Context, name: str, reason: str | None) -> None:
    """
    Pause NAME's monitoring while you work on it.

    The central stops probing it, counting it as down and telling anyone it is;
    everything it last answered is still shown. 'noust node resume NAME' puts it back.
    """
    from noust.fleet.audit import audit
    from noust.fleet.liveness import Liveness
    from noust.fleet.nodes import cli_actor

    manager = _manager()
    manager.get(name)
    if ctx.dry_run:
        ctx.logger.info(f"would pause the monitoring of {name}")
        return
    Liveness(manager.store).pause(name, reason=reason, actor=cli_actor())
    audit("fleet.node.pause", "success", resource=f"node:{name}", detail=reason)
    ctx.logger.success(f"{name} is not monitored until you resume it: noust node resume {name}")


@cli.command("resume")
@click.argument("name")
@pass_context
def resume_command(ctx: Context, name: str) -> None:
    """Put NAME's monitoring back, with a clean count of silences."""
    from noust.fleet.audit import audit
    from noust.fleet.liveness import Liveness

    manager = _manager()
    manager.get(name)
    if ctx.dry_run:
        ctx.logger.info(f"would resume the monitoring of {name}")
        return
    Liveness(manager.store).resume(name)
    audit("fleet.node.resume", "success", resource=f"node:{name}")
    ctx.logger.success(f"{name} is monitored again")


# The fleet's views and bulk actions, and node labels, live in their own module
# (B5b) and attach themselves to this group; importing it here is what makes
# them part of 'noust node'.
from noust.cli.commands import fleet_views as _fleet_views  # noqa: E402,F401

# Servers behind NAT (noust node link ..., and noust fleet link/unlink on the server).
from noust.cli.commands import node_link as _node_link  # noqa: E402,F401
