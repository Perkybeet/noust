# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust fleet`` command group.

On a node: ``authorize`` a central (install its restricted key, keep the
console on loopback, issue its token, print the join code) and
``deauthorize`` it. On a central: ``status``, the whole fleet at a glance.
The work is in :mod:`noust.fleet`; these handlers only ask, print and audit.
"""

from __future__ import annotations

import json
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import NodeError
from noust.core.utils import check_root


@click.group("fleet", cls=NoustGroup)
def cli() -> None:
    """Enroll this server in a fleet, or see the fleet from its central."""


def _require_root(action: str) -> None:
    """
    Refuse an enrollment change that cannot work without root.

    Args:
        action: The command, for the message.

    Raises:
        NodeError: When not running as root.
    """
    if not check_root():
        raise NodeError(
            f"'noust fleet {action}' needs root",
            details="It edits an account's authorized_keys and this server's tokens: use sudo.",
        )


def _flag_value(argv: list[str], flag: str) -> str | None:
    """
    Read the value after a flag in an argv.

    Args:
        argv: The argv.
        flag: Such as ``--port``.

    Returns:
        The value, or None when the flag is absent or last.
    """
    if flag in argv and argv.index(flag) + 1 < len(argv):
        return argv[argv.index(flag) + 1]
    return None


def ensure_console_on_loopback(verbose: bool, dry_run: bool) -> int:
    """
    Make sure the console runs as ``noust-web.service`` on loopback only.

    The central reaches a node's console through its tunnel, which ends on
    127.0.0.1; nothing else should reach it. A console already running as a
    service on loopback is left as it is. One that is not running is enabled
    with ``noust web enable``'s own function, on 127.0.0.1. One bound to
    anything else, or serving TLS, is refused with the command that fixes it
    rather than changed behind the operator's back.

    Args:
        verbose: Log each step.
        dry_run: Rehearse.

    Returns:
        The console's port.

    Raises:
        NodeError: When the console is exposed beyond loopback, serves TLS, or
            could not be enabled.
    """
    from noust.cli.commands import web
    from noust.core.config import Config
    from noust.core.net import is_loopback_host
    from noust.managers.cron_manager import decode_exec_start

    unit = web._service_unit_path()
    host: str | None = None
    port: int | None = None
    tls = False
    if unit.exists():
        for line in unit.read_text(encoding="utf-8").splitlines():
            if not line.startswith("ExecStart="):
                continue
            argv = decode_exec_start(line.removeprefix("ExecStart=")) or []
            host = _flag_value(argv, "--host") or host
            port_text = _flag_value(argv, "--port")
            port = int(port_text) if port_text and port_text.isdigit() else port
            tls = tls or any(a in ("--self-signed", "--tls-cert", "--require-https") for a in argv)
        status = web._service_status(verbose)
        port = port or web.StartOptions().port
        fix = f"noust web enable --host 127.0.0.1 --port {port}"
        if host is not None and not is_loopback_host(host):
            raise NodeError(
                f"The console listens on {host}, not only on this machine",
                details=(
                    "A fleet node's console listens on 127.0.0.1 only: the central reaches it "
                    f"through its SSH tunnel, and nothing else should. Run '{fix}', then "
                    "authorize again."
                ),
            )
        if tls:
            raise NodeError(
                "The console serves TLS, which the central's tunnel does not expect",
                details=(
                    "On loopback the SSH tunnel already encrypts the connection. Run "
                    f"'{fix}', then authorize again."
                ),
            )
        if status is not None and status["active"]:
            return port
    configured = Config().get("web.port")
    port = port or (int(configured) if configured else web.StartOptions().port)
    code = web._enable(web.StartOptions(host="127.0.0.1", port=port), verbose, dry_run=dry_run)
    if code != 0:
        raise NodeError(
            "The console could not be started on loopback",
            details="See the messages above, fix them, and authorize again.",
        )
    return port


@cli.command("authorize")
@click.option(
    "--central-key",
    required=True,
    help="The central's public key line for this server, as 'noust node key' printed it.",
)
@click.option("--name", "central", required=True, help="The central's name.")
@click.option(
    "--ssh-user",
    default="root",
    show_default=True,
    help="Account the central's tunnel logs in as.",
)
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True, help="Replace an older token without asking."
)
@json_option("Print the result, join code included, as JSON.")
@pass_context
def authorize_command(
    ctx: Context, central_key: str, central: str, ssh_user: str, assume_yes: bool
) -> None:
    """
    Let a central manage this server, and print the join code for it.

    The central's key is installed in SSH_USER's authorized_keys restricted to
    forwarding this server's console port: it opens no shell and runs
    nothing. The console is kept on 127.0.0.1 as a service, a 'fleet' token
    is issued, and the join code printed carries this server's SSH host key,
    the SSH port, the console port and that token. Paste it into the central.
    """
    from noust.cli.web_state import token_manager
    from noust.fleet.audit import audit
    from noust.fleet.authorize import authorize

    _require_root("authorize")

    def confirm(names: list[str]) -> bool:
        if assume_yes:
            return True
        return click.confirm(
            f"Central {central} already holds {', '.join(names)} on this server. "
            "Revoke it and issue a new token?",
            default=False,
        )

    try:
        result = authorize(
            central_key=central_key,
            central=central,
            ssh_user=ssh_user,
            tokens=token_manager(),
            ensure_console=lambda: ensure_console_on_loopback(ctx.verbose, ctx.dry_run),
            confirm_replace=confirm,
        )
    except NodeError as exc:
        audit("fleet.authorize", "failure", resource=f"central:{central}", detail=exc.message)
        raise
    audit(
        "fleet.authorize",
        "success",
        resource=f"central:{result.central}",
        detail=(
            f"key {result.key_fingerprint} for {result.ssh_user} forwarding "
            f"127.0.0.1:{result.console_port}; token '{result.token_name}'"
            + (f"; revoked {', '.join(result.replaced_tokens)}" if result.replaced_tokens else "")
        ),
    )

    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger = ctx.logger
    logger.success(f"Central {result.central} is authorized on this server")
    logger.key_value(
        "SSH", f"{result.ssh_user} on port {result.ssh_port} ({result.authorized_keys})"
    )
    logger.key_value("Central key", result.key_fingerprint)
    logger.key_value("Host key", result.host_key_fingerprint)
    logger.key_value("Console", f"127.0.0.1:{result.console_port}")
    logger.key_value("Token", result.token_name)
    if result.replaced_tokens:
        logger.key_value("Revoked", ", ".join(result.replaced_tokens))
    logger.blank()
    logger.info("Join code (paste it into the central; it holds a token, shown only now):")
    click.echo(result.join_code)
    logger.blank()
    logger.info(
        f"On the central: noust node add {result.node_name} --ssh "
        f"{result.ssh_user}@<this server's address> --join-code -"
    )
    if ctx.dry_run:
        logger.warning("Rehearsal: this join code's token was not saved and will not work.")


@cli.command("deauthorize")
@click.option("--name", "central", required=True, help="The central's name.")
@click.option(
    "--ssh-user",
    default="root",
    show_default=True,
    help="Account the central's key was installed for.",
)
@json_option("Print what was removed as JSON.")
@pass_context
def deauthorize_command(ctx: Context, central: str, ssh_user: str) -> None:
    """
    Stop trusting a central: remove its SSH key line and revoke its tokens.

    Its requests stop authenticating at once; its tunnel cannot reopen.
    """
    from noust.cli.web_state import token_manager
    from noust.fleet.audit import audit
    from noust.fleet.authorize import deauthorize

    _require_root("deauthorize")
    result = deauthorize(central=central, ssh_user=ssh_user, tokens=token_manager())
    audit(
        "fleet.deauthorize",
        "success",
        resource=f"central:{result.central}",
        detail=(
            f"removed {result.removed_keys} key line(s) from {result.authorized_keys}; "
            f"revoked {', '.join(result.revoked_tokens) or 'no token'}"
        ),
    )

    if ctx.json_output:
        click.echo(json.dumps(result.to_dict()))
        return
    logger = ctx.logger
    if not result.removed_keys and not result.revoked_tokens:
        logger.info(f"Central {result.central} held nothing on this server")
        return
    logger.success(f"Central {result.central} is no longer authorized on this server")
    logger.key_value("Key lines removed", f"{result.removed_keys} ({result.authorized_keys})")
    logger.key_value("Tokens revoked", ", ".join(result.revoked_tokens) or "none")


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
