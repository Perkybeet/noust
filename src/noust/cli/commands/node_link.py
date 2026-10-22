# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Servers behind NAT: ``noust node link`` on the central, ``noust fleet link`` on the server.

A server with no SSH port a central can reach dials the central instead
(:mod:`noust.fleet.link`). The two ends:

On the central, as root once, then for each server:

1. ``noust node link setup`` prepares the account servers dial in as and
   restricts it in sshd (``noust node link status`` says whether it is done);
2. ``noust node link invite web-5 --host central.example.com`` prints the
   command to run on web-5;
3. ``noust node link add web-5 --link-code -`` takes the code that prints.

On the server, as root (``noust fleet link ...`` is the command the invitation
printed; ``noust fleet unlink --name <central>`` undoes it).

Thin front ends over :class:`~noust.fleet.link.central.LinkManager`,
:class:`~noust.fleet.nodes.NodeManager` and :mod:`noust.fleet.link.node`, the same
ones ``/api/fleet/link`` uses.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.fleet import (
    Steps,
    _operator,
    _parse_access,
    _require_root,
    ensure_console_on_loopback,
)
from noust.cli.commands.fleet import cli as fleet_cli
from noust.cli.commands.node import cli as node_cli
from noust.core.exceptions import NodeError

if TYPE_CHECKING:
    from noust.fleet.link.central import LinkManager


@node_cli.group("link", cls=NoustGroup)
def link_cli() -> None:
    """Servers behind NAT that dial in to this central."""


def _links() -> LinkManager:
    """
    Build the link manager.

    Returns:
        A :class:`~noust.fleet.link.central.LinkManager`.
    """
    from noust.fleet.link.central import LinkManager

    return LinkManager()


def _read_link_code(value: str | None) -> str:
    """
    Get the link code without it ever appearing in argv or on the screen.

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
        return str(click.prompt("Link code", hide_input=True)).strip()
    return value


@link_cli.command("setup")
@json_option("Print what was done as JSON.")
@pass_context
def setup_command(ctx: Context) -> None:
    """
    Prepare this central for servers that dial in.

    Creates the account they log in as (noust-link: no shell, no home) and
    restricts it in sshd itself to listening on one loopback port: no command,
    no terminal, no other forward. Run it once, as root.
    """
    _require_root("link setup")
    result = _links().setup(dry_run=ctx.dry_run, actor=_operator())
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger = ctx.logger
    rehearsal = ctx.dry_run
    logger.success(
        f"This central {'would take' if rehearsal else 'takes'} servers that dial in "
        f"(account {result.account}{' created' if result.account_created else ''})"
    )
    logger.key_value("Restricted in", result.sshd_policy)
    logger.key_value("Keys", result.keys_file)
    logger.key_value("SSH port", str(result.ssh_port))
    logger.key_value("Host key", result.host_key_fingerprint)
    logger.info("Invite a server with: noust node link invite NAME --host <this central's address>")


@link_cli.command("status")
@json_option("Print the status as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say whether this central can take servers that dial in, and which do."""
    status = _links().status()
    if ctx.json_output:
        click.echo(json.dumps(status.to_dict()))
        return
    logger = ctx.logger
    if status.ready:
        logger.success("This central takes servers that dial in")
    else:
        logger.warning("This central is not ready for servers that dial in:")
        for problem in status.problems:
            logger.info(f"  {problem}")
    if status.host_key_fingerprint:
        logger.key_value("Host key", status.host_key_fingerprint)
    logger.key_value("SSH port", str(status.ssh_port))
    if not status.links:
        logger.info("No server dials in or was invited yet.")
        return
    logger.table(
        ["Server", "State", "Link port", "Invited"],
        [[row["node"], row["state"], str(row["port"]), row["created_at"]] for row in status.links],
    )


@link_cli.command("invite")
@click.argument("name")
@click.option(
    "--host",
    required=True,
    help="The address the server dials to reach this central: a name or an IP address.",
)
@click.option(
    "--ssh-port",
    type=int,
    default=None,
    help="The SSH port it dials. Default: the port sshd listens on here.",
)
@click.option(
    "--port",
    type=int,
    default=None,
    help="The loopback port its link listens on, on this machine. Default: a free one.",
)
@json_option("Print the invitation as JSON.")
@pass_context
def invite_command(
    ctx: Context, name: str, host: str, ssh_port: int | None, port: int | None
) -> None:
    """
    Invite the server NAME to dial in, and print the command to run on it.

    The command carries this central's SSH host key, which the server pins: it
    never trusts whatever answers the first connection. It prints a link code to
    paste into 'noust node link add'.
    """
    if ctx.dry_run:
        ctx.logger.info(f"would invite {name} to dial {host} and give it a loopback port")
        return
    invite = _links().invite(name, host=host, ssh_port=ssh_port, port=port, actor=_operator())
    if ctx.json_output:
        click.echo(json.dumps(invite.to_dict()))
        return
    logger = ctx.logger
    logger.success(
        f"{name} is invited to dial {invite.host}:{invite.ssh_port} (until {invite.expires_at})"
    )
    logger.blank()
    logger.info(f"On {name}, as root, run (Noust 3.4 or later):")
    logger.blank()
    click.echo(invite.command)
    logger.blank()
    logger.info(
        f"This central's host key: {invite.host_key_fingerprint} (compare it with "
        "'ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub' here)."
    )
    logger.info(f"It prints a link code. Then, here: noust node link add {name} --link-code -")


@link_cli.command("add")
@click.argument("name")
@click.option(
    "--link-code",
    default=None,
    help="The code 'noust fleet link' printed on the server; '-' reads it from stdin. "
    "Omitted, it is asked for without echo.",
)
@json_option("Print the registered node as JSON.")
@pass_context
def add_command(ctx: Context, name: str, link_code: str | None) -> None:
    """
    Register NAME from the link code it printed, once it connects and answers.

    Needs two-factor sign-in enabled on this central. Nothing is kept unless the
    server connects and answers with its token; the same code can be pasted again.
    """
    from noust.fleet.audit import audit
    from noust.fleet.nodes import NodeManager

    if ctx.dry_run:
        ctx.logger.info(f"would register {name} from its link code")
        return
    code = _read_link_code(link_code)
    try:
        record = NodeManager().add_linked(name, link_code=code)
    except NodeError as exc:
        audit("fleet.node.add", "failure", resource=f"node:{name}", detail=exc.message)
        raise
    audit(
        "fleet.node.add",
        "success",
        resource=f"node:{name}",
        detail=f"dials in on 127.0.0.1:{record.ssh_port}, Noust {record.version}",
    )
    if ctx.json_output:
        click.echo(json.dumps(record.to_dict()))
        return
    ctx.logger.success(f"Node {name} added: Noust {record.version or 'unknown'}, reachable")


@link_cli.command("cancel")
@click.argument("name")
@pass_context
def cancel_command(ctx: Context, name: str) -> None:
    """Cancel the invitation of NAME, if it was never answered."""
    if ctx.dry_run:
        ctx.logger.info(f"would cancel the invitation of {name}")
        return
    _links().cancel(name, actor=_operator())
    ctx.logger.success(f"The invitation of {name} is cancelled")


# -- On the server ---------------------------------------------------------------------


@fleet_cli.command("link")
@click.option("--name", "central", required=True, help="The central's name.")
@click.option(
    "--as",
    "node_name",
    default=None,
    help="The name this server goes by on the central. Default: its host name.",
)
@click.option("--host", required=True, help="The address to dial to reach the central.")
@click.option("--ssh-port", type=int, default=22, show_default=True, help="The central's SSH port.")
@click.option(
    "--host-key",
    required=True,
    help="The central's SSH host key line, as its invitation printed it. It is pinned.",
)
@click.option(
    "--remote-port",
    type=int,
    required=True,
    help="The loopback port the link listens on at the central.",
)
@click.option("--invite", "nonce", required=True, help="The invitation's nonce.")
@click.option(
    "--access",
    "level",
    type=click.Choice(["read", "deploy", "admin"]),
    default=None,
    help="The most any central may do on this server: read, deploy or admin. Unchanged "
    "when omitted (admin on a server that never set it).",
)
@click.option(
    "--allow-host-access/--no-host-access",
    "host_access",
    default=None,
    help="Let centrals reach the host itself (SSH keys, sshd, firewall); off unless set.",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Replace an older token and move a background console to a service without asking.",
)
@json_option("Print the result, link code included, as JSON.")
@pass_context
def link_command(
    ctx: Context,
    central: str,
    node_name: str | None,
    host: str,
    ssh_port: int,
    host_key: str,
    remote_port: int,
    nonce: str,
    level: str | None,
    host_access: bool | None,
    assume_yes: bool,
) -> None:
    """
    Dial a central that cannot reach this server, and print the link code for it.

    Keeps an SSH connection to the central open (a service, noust-link-<central>)
    that forwards this server's console to one loopback port there, and nothing
    else: the central gets no shell here and this server gets none there. The
    central's host key is pinned. Run the command the central's invitation
    printed; paste the code it prints into the central.
    """
    from noust.cli.web_state import token_manager
    from noust.fleet.audit import audit
    from noust.fleet.link.node import link

    _require_root("link")
    access = _parse_access(level, host_access)
    steps = Steps(animate=None if not ctx.json_output else False)

    def ask(question: str) -> bool:
        with steps.paused():
            return bool(click.confirm(question, default=False))

    def confirm(names: list[str]) -> bool:
        if assume_yes:
            return True
        return ask(
            f"Central {central} already holds {', '.join(names)} on this server. "
            "Revoke it and issue a new token?"
        )

    def confirm_adopt(daemon: object, changes: list[str]) -> bool:
        if assume_yes:
            return True
        port = getattr(getattr(daemon, "options", None), "port", "?")
        pid = getattr(daemon, "pid", "?")
        return ask(
            f"A console runs in the background (PID {pid}) on port {port}. Stop it and run "
            f"it as a service on 127.0.0.1:{port} instead? Your access token keeps working."
        )

    succeeded = False
    try:
        result = link(
            central=central,
            host=host,
            ssh_port=ssh_port,
            host_key=host_key,
            remote_port=remote_port,
            nonce=nonce,
            node_name=node_name,
            tokens=token_manager(),
            ensure_console=lambda: (
                ensure_console_on_loopback(
                    ctx.verbose, ctx.dry_run, confirm_adopt=confirm_adopt, note=steps.note
                ).port
            ),
            confirm_replace=confirm,
            access=access,
            progress=steps,
            actor=_operator(),
            dry_run=ctx.dry_run,
        )
        succeeded = True
    except NodeError as exc:
        audit("fleet.link", "failure", resource=f"central:{central}", detail=exc.message)
        raise
    finally:
        steps.finish(ok=succeeded)
    audit(
        "fleet.link",
        "success",
        resource=f"central:{result.central}",
        detail=(
            f"{'would dial' if ctx.dry_run else 'dials'} {result.host}:{result.ssh_port}, "
            f"forwarding 127.0.0.1:{result.console_port} to its port {result.remote_port}; "
            f"token '{result.token_name}'"
        ),
    )
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger = ctx.logger
    rehearsal = ctx.dry_run
    logger.success(
        f"This server {'would dial' if rehearsal else 'dials'} {result.central} at "
        f"{result.host}:{result.ssh_port}"
    )
    logger.key_value("Service", result.unit)
    logger.key_value("Console", f"127.0.0.1:{result.console_port} -> its port {result.remote_port}")
    logger.key_value("Central host key", result.host_key_fingerprint)
    if result.key_fingerprint:
        logger.key_value("Link key", result.key_fingerprint)
    logger.key_value("Token", result.token_name)
    if result.replaced_tokens:
        logger.key_value("Revoked", ", ".join(result.replaced_tokens))
    if rehearsal:
        logger.warning("Rehearsal: nothing was written, started or issued.")
        return
    logger.blank()
    logger.info("Link code (paste it into the central; it holds a token, shown only now):")
    click.echo(result.link_code)
    logger.blank()
    logger.info(f"On the central: noust node link add {result.node_name} --link-code -")
    if not result.active:
        logger.warning(
            f"{result.unit} is not running: see 'journalctl -u {result.unit} -n 30' for why."
        )
    else:
        logger.info(
            "It connects once the central has the key: until then 'journalctl -u "
            f"{result.unit}' shows it being refused."
        )


@fleet_cli.command("unlink")
@click.option("--name", "central", required=True, help="The central's name.")
@json_option("Print what was removed as JSON.")
@pass_context
def unlink_command(ctx: Context, central: str) -> None:
    """
    Stop dialling a central: stop the link, remove its key and unit, revoke its tokens.

    The central loses the way in at once; its requests stop authenticating.
    """
    from noust.cli.web_state import token_manager
    from noust.fleet.audit import audit
    from noust.fleet.link.node import unlink

    _require_root("unlink")
    result = unlink(central=central, tokens=token_manager(), dry_run=ctx.dry_run)
    audit(
        "fleet.unlink",
        "success",
        resource=f"central:{result.central}",
        detail=(
            ("would stop " if ctx.dry_run else "stopped ")
            + result.unit
            + "; "
            + ("would revoke " if ctx.dry_run else "revoked ")
            + (", ".join(result.revoked_tokens) or "no token")
        ),
    )
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger = ctx.logger
    if not result.stopped and not result.removed_files and not result.revoked_tokens:
        logger.info(f"This server holds no link to {result.central}")
        return
    if ctx.dry_run:
        logger.info(f"Rehearsal: nothing was changed for {result.central}")
        logger.key_value("Would stop", result.unit)
        logger.key_value("Tokens that would be revoked", ", ".join(result.revoked_tokens) or "none")
        return
    logger.success(f"This server no longer dials {result.central}")
    logger.key_value("Stopped", result.unit if result.stopped else "no unit was installed")
    logger.key_value("Tokens revoked", ", ".join(result.revoked_tokens) or "none")
