# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust fleet`` command group.

On a node: ``authorize`` a central (keep the console on loopback, prepare the
tunnel account, install the central's restricted key, issue its token, print
the join code), ``deauthorize`` it, and ``access``, the most any central may
do here. On a central: ``status``, the whole fleet at a glance. The work is
in :mod:`noust.fleet`; these handlers only ask, show progress, print and audit.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import click

from noust.cli.app import Context, json_option, pass_context

# The fleet's views and bulk actions, node labels (B5b), the guided enrollment (fleet_enroll) and
# 'fleet link' (node_link) live in their own modules and attach themselves to this group;
# importing them here is what makes them part of 'noust fleet'.
from noust.cli.commands import fleet_builder as _fleet_builder  # noqa: F401
from noust.cli.commands import fleet_cloud as _fleet_cloud  # noqa: F401
from noust.cli.commands import fleet_enroll as _fleet_enroll  # noqa: F401
from noust.cli.commands import fleet_placement as _fleet_placement  # noqa: F401
from noust.cli.commands import fleet_views as _fleet_views  # noqa: F401
from noust.cli.commands import node_link as _node_link  # noqa: F401
from noust.cli.commands.fleet_common import (
    ConsoleOutcome,
    Steps,
    _operator,
    _parse_access,
    _require_root,
    ensure_console_on_loopback,
)
from noust.cli.commands.fleet_group import cli
from noust.core.exceptions import NodeError

if TYPE_CHECKING:
    from noust.managers.console_service import DaemonConsole


@cli.command("authorize", gates_json=True)
@click.option(
    "--central-key",
    required=True,
    help="The central's public key line for this server, as 'noust node key' printed it.",
)
@click.option("--name", "central", required=True, help="The central's name.")
@click.option(
    "--ssh-user",
    default="noust-tunnel",
    show_default=True,
    help="Account the central's tunnel logs in as. The default is created and "
    "restricted in sshd; root needs --i-understand.",
)
@click.option(
    "--i-understand",
    "allow_root",
    is_flag=True,
    help="Confirm --ssh-user root: a key in root's authorized_keys can create Unix "
    "sockets as root, however it is restricted.",
)
@click.option(
    "--replace-root-key",
    default=None,
    metavar="KEY",
    help="A central's old public key to take out of root's authorized_keys once the new "
    "line is in place, whatever its comment (a 0600 copy of the file is kept). 'noust node "
    "migrate-tunnel' prints it for a node a 3.0 central authorized as root.",
)
@click.option(
    "--access",
    "level",
    type=click.Choice(["read", "deploy", "admin"]),
    default=None,
    help="The most any central may do on this server: read, deploy (operate and update "
    "applications) or admin. Unchanged when omitted (admin on a server that never set it).",
)
@click.option(
    "--allow-host-access/--no-host-access",
    "host_access",
    default=None,
    help="Let centrals reach the host itself: change how this server is reached (SSH keys, "
    "sshd, firewall, accounts) and make root-equivalent changes (raw units, cron commands, "
    "backup hooks, raw site configuration), on top of --access admin. Off unless set.",
)
@click.option(
    "--allow-self",
    is_flag=True,
    help="Authorize even though this server looks like the central that issued the key "
    "(testing only).",
)
@click.option(
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help="Replace an older token and move a background console to a service without asking.",
)
@json_option("Print the result, join code included, as JSON.")
@pass_context
def authorize_command(
    ctx: Context,
    central_key: str,
    central: str,
    ssh_user: str,
    allow_root: bool,
    replace_root_key: str | None,
    level: str | None,
    host_access: bool | None,
    allow_self: bool,
    assume_yes: bool,
) -> None:
    """
    Let a central manage this server, and print the join code for it.

    The central's tunnel logs in as the unprivileged account noust-tunnel,
    created here if missing and restricted in sshd itself to forwarding this
    server's console port: no shell, no command, no other forward. The
    console is kept on 127.0.0.1 as a service (a background console is moved
    to it), a 'fleet' token is issued, and the join code printed carries this
    server's SSH host key, the SSH user and port, the console port and that
    token. Paste it into the central. Your console's access token keeps working.
    """
    from noust.cli.web_state import token_manager
    from noust.fleet.audit import audit
    from noust.fleet.authorize import authorize, issued_here

    _require_root("authorize")
    if ssh_user == "root" and allow_root and not ctx.json_output:
        ctx.logger.warning(
            "Authorizing as root: this key can make sshd create Unix sockets as root "
            "(ssh -R /path:...), which the tunnel account cannot. Prefer the default."
        )
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

    def confirm_adopt(daemon: DaemonConsole, changes: list[str]) -> bool:
        if assume_yes:
            return True
        also = f" It will no longer do this: {'; '.join(changes)}." if changes else ""
        return ask(
            f"A console runs in the background (PID {daemon.pid}) on port "
            f"{daemon.options.port}. Stop it and run it as a service on 127.0.0.1:"
            f"{daemon.options.port} instead? Your access token keeps working.{also}"
        )

    console: dict[str, ConsoleOutcome] = {}

    def ensure_console() -> int:
        outcome = ensure_console_on_loopback(
            ctx.verbose, ctx.dry_run, confirm_adopt=confirm_adopt, note=steps.note
        )
        console["outcome"] = outcome
        return outcome.port

    succeeded = False
    try:
        result = authorize(
            central_key=central_key,
            central=central,
            ssh_user=ssh_user,
            allow_root=allow_root,
            replace_root_key=replace_root_key,
            access=access,
            tokens=token_manager(),
            ensure_console=ensure_console,
            confirm_replace=confirm,
            self_check=None if allow_self else issued_here,
            progress=steps,
            actor=_operator(),
            dry_run=ctx.dry_run,
        )
        succeeded = True
    except NodeError as exc:
        audit("fleet.authorize", "failure", resource=f"central:{central}", detail=exc.message)
        raise
    finally:
        # Whatever ended the command, the spinner stops before anything else writes.
        steps.finish(ok=succeeded)
    outcome = console.get("outcome")
    audit(
        "fleet.authorize",
        "success",
        resource=f"central:{result.central}",
        detail=(
            f"key {result.key_fingerprint} for {result.ssh_user} forwarding "
            f"127.0.0.1:{result.console_port}; token '{result.token_name}'; access "
            f"{result.access.get('level')}"
            + (" with host access" if result.access.get("host_access") else "")
            + (f"; revoked {', '.join(result.replaced_tokens)}" if result.replaced_tokens else "")
            + (
                f"; removed its line from {', '.join(result.moved_from)}"
                if result.moved_from
                else ""
            )
        ),
    )
    token = outcome.token if outcome else None

    if ctx.json_output:
        payload = result.to_dict()
        payload["console_token"] = token
        payload["console_adopted_pid"] = outcome.adopted if outcome else None
        click.echo(json.dumps(payload))
        return
    _print_summary(ctx, result, outcome)
    logger = ctx.logger
    logger.blank()
    logger.info("Join code (paste it into the central; it holds a token, shown only now):")
    click.echo(result.join_code)
    logger.blank()
    logger.info(
        f"On the central: noust node add {result.node_name} --ssh "
        f"<this server's address> --join-code -"
    )
    if token is not None:
        logger.blank()
        logger.info("Console access token for this server (it had none; shown only now):")
        click.echo(token)
    if ctx.dry_run:
        logger.warning("Rehearsal: this join code's token was not saved and will not work.")


def _print_summary(ctx: Context, result: Any, outcome: ConsoleOutcome | None) -> None:
    """
    Print what ``authorize`` did, in a few lines.

    Args:
        ctx: The command's context.
        result: :class:`~noust.fleet.authorize.AuthorizeResult`.
        outcome: How the console was made to run, when it was asked.
    """
    from noust.fleet.policy import FleetAccess

    logger = ctx.logger
    rehearsal = ctx.dry_run
    logger.success(
        f"Central {result.central} {'would be' if rehearsal else 'is'} authorized on this server"
    )
    account = result.ssh_user + (" (created)" if result.tunnel_account_created else "")
    logger.key_value("SSH", f"{account} on port {result.ssh_port} ({result.authorized_keys})")
    if result.sshd_policy:
        logger.key_value("Restricted in", result.sshd_policy)
    logger.key_value("Central key", result.key_fingerprint)
    logger.key_value("Host key", result.host_key_fingerprint)
    console = f"127.0.0.1:{result.console_port}"
    if outcome is not None and outcome.adopted is not None:
        console += f" (the background console, PID {outcome.adopted}, now runs as a service)"
    logger.key_value("Console", console)
    logger.key_value("Token", result.token_name)
    access = FleetAccess.from_dict(result.access)
    logger.key_value("Access", f"{access.describe()} (change it with 'noust fleet access')")
    if access.level == "admin" and not access.host_access:
        logger.info(
            "Without host access a central cannot change SSH, the firewall or system "
            "accounts, nor make root-equivalent changes (raw units, cron commands, backup "
            "hooks, raw site configuration)."
        )
    if result.replaced_tokens:
        logger.key_value(
            "Would revoke" if rehearsal else "Revoked", ", ".join(result.replaced_tokens)
        )
    if result.moved_from:
        logger.key_value(
            "Old line would be removed from" if rehearsal else "Old line removed from",
            ", ".join(result.moved_from),
        )
    if result.root_key_backup:
        logger.key_value("Copy of it before", result.root_key_backup)
    for warning in result.warnings:
        logger.warning(warning)


@cli.command("deauthorize")
@click.option("--name", "central", required=True, help="The central's name.")
@click.option(
    "--ssh-user",
    default=None,
    help="Only this account's key file. By default every file Noust writes: the "
    "tunnel account's and root's.",
)
@json_option("Print what was removed as JSON.")
@pass_context
def deauthorize_command(ctx: Context, central: str, ssh_user: str | None) -> None:
    """
    Stop trusting a central: remove its SSH key lines and revoke its tokens.

    Its requests stop authenticating at once; its tunnel cannot reopen.
    """
    from noust.cli.web_state import token_manager
    from noust.fleet.audit import audit
    from noust.fleet.authorize import deauthorize

    _require_root("deauthorize")
    result = deauthorize(
        central=central, ssh_user=ssh_user, tokens=token_manager(), dry_run=ctx.dry_run
    )
    files = ", ".join(result.authorized_keys) or "no key file"
    audit(
        "fleet.deauthorize",
        "success",
        resource=f"central:{result.central}",
        detail=(
            ("would remove" if ctx.dry_run else "removed")
            + f" {result.removed_keys} key line(s) from {files}; "
            + ("would revoke " if ctx.dry_run else "revoked ")
            + (", ".join(result.revoked_tokens) or "no token")
        ),
    )

    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger = ctx.logger
    if not result.removed_keys and not result.revoked_tokens:
        logger.info(f"Central {result.central} held nothing on this server")
        return
    if ctx.dry_run:
        logger.info(f"Rehearsal: nothing was changed for central {result.central}")
        logger.key_value("Key lines that would be removed", f"{result.removed_keys}")
        logger.key_value("Tokens that would be revoked", ", ".join(result.revoked_tokens) or "none")
        return
    logger.success(f"Central {result.central} is no longer authorized on this server")
    logger.key_value("Key lines removed", f"{result.removed_keys} ({files})")
    logger.key_value("Tokens revoked", ", ".join(result.revoked_tokens) or "none")


@cli.command("access")
@click.option(
    "--level",
    type=click.Choice(["read", "deploy", "admin"]),
    default=None,
    help="The most any central may do here: read, deploy (operate and update "
    "applications) or admin.",
)
@click.option(
    "--host-access",
    type=click.Choice(["on", "off"]),
    default=None,
    help="Whether centrals may reach the host itself: SSH keys, sshd, firewall, accounts, "
    "and root-equivalent changes (raw units, cron commands, backup hooks, raw site "
    "configuration); only counts with admin.",
)
@json_option("Print the ceiling as JSON.")
@pass_context
def access_command(ctx: Context, level: str | None, host_access: str | None) -> None:
    """
    Show or set the most any central may do on this server.

    Enforced here, whatever a central claims for its operator: a central
    held to read can only read, even if it is compromised. Without options,
    shows the ceiling in force. Takes effect on the next request; a central
    can lower what it shows but never raise it.
    """
    from noust.fleet.audit import audit
    from noust.fleet.authorize import ceiling_in_force
    from noust.fleet.policy import set_access

    before = ceiling_in_force()
    wanted = _parse_access(level, None if host_access is None else host_access == "on")
    if wanted is not None and wanted != before:
        _require_root("access")
        if ctx.dry_run:
            ctx.logger.info(f"would set this server's fleet access to {wanted.describe()}")
            return
        after = set_access(wanted, actor=_operator())
        audit(
            "fleet.access",
            "success",
            resource="fleet:access",
            detail=f"{before.describe()} -> {after.describe()}",
        )
    else:
        after = before
    if ctx.json_output:
        click.echo(json.dumps(after.to_dict()))
        return
    verb = "is now" if wanted is not None and wanted != before else "is"
    ctx.logger.info(f"Centrals' access to this server {verb}: {after.describe()}")
    if after.level == "read":
        ctx.logger.info("They can read everything and change nothing.")
    elif after.level == "deploy":
        ctx.logger.info(
            "They can read, operate, update and roll back the applications here, and "
            "take backups; not create, delete or configure anything."
        )
    else:
        ctx.logger.info(
            "They can do everything but manage this server's accounts, security "
            "settings and audit log"
            + (", including how it is reached." if after.host_access else ".")
        )


def _cell_counts(counts: dict[str, int] | None, *keys: str) -> str:
    """
    Show a node's counters in a table cell.

    Args:
        counts: The counters, or None when unknown.
        keys: Which ones, in order.

    Returns:
        Such as ``3 / 1``, or ``-``.
    """
    if counts is None:
        return "-"
    return " / ".join(str(counts.get(key, 0)) for key in keys)


@cli.command("status")
@json_option("Print every node's summary as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """
    Show every node this central manages: reachability, version, apps, units, certificates.

    Nodes are asked in parallel through their tunnels; an unreachable node is
    shown with ssh's own words.
    """
    from noust.fleet.status import fleet_status

    summaries: list[dict[str, Any]] = fleet_status()
    if ctx.json_output:
        click.echo(json.dumps({"nodes": summaries}))
        return
    logger = ctx.logger
    if not summaries:
        logger.info("This central manages no nodes. Add one with 'noust node key NAME'.")
        return
    logger.table(
        ["Node", "Status", "Version", "Apps run/fail", "Units fail", "Certs expiring"],
        [
            [
                summary["name"],
                summary["status"],
                summary["version"] or "-",
                _cell_counts(summary["apps"], "running", "failed"),
                _cell_counts(summary["units"], "failed"),
                "-"
                if summary["certificates_expiring"] is None
                else str(summary["certificates_expiring"]),
            ]
            for summary in summaries
        ],
    )
    for summary in summaries:
        if summary["error"]:
            logger.blank()
            logger.error(f"{summary['name']}: {summary['error']}")
            if summary["details"]:
                click.echo(summary["details"])
        for warning in summary["warnings"]:
            logger.warning(f"{summary['name']}: {warning}")
