# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust fleet enroll``: bring a server into the fleet from the central, with a credential used once.

The manual way (``noust node key``, ``noust fleet authorize`` on the server,
``noust node add``) needs a terminal on each machine. This needs only an address
and a way in: the central connects to the host key the operator confirms,
installs Noust there if it is missing, authorizes itself with the restricted
tunnel account and joins the server, exactly as the manual way does. The
password or the key is read here, without echo, handed to the enrollment and
forgotten: it is never an argument, never kept.

The work is :class:`noust.fleet.enroll.NodeEnroller`, also behind
``POST /api/fleet/enroll``; this handler only asks, shows progress and prints.
The command is attached to the ``noust fleet`` group, which imports this module.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from noust.cli.app import Context, json_option, pass_context
from noust.cli.commands.fleet import cli as fleet_cli
from noust.core.exceptions import NodeError

#: How the enrollment's log levels read on a terminal.
_LEVELS = {"success": "success", "warning": "warning", "error": "error"}


def _interactive() -> bool:
    """
    Report whether someone is at the terminal to be asked.

    Returns:
        True when standard input is a terminal.
    """
    return sys.stdin.isatty()


def _read_key(path: Path, ask_passphrase: bool, host: str) -> tuple[str, str | None]:
    """
    Read a private key file, and its passphrase when it has one.

    Args:
        path: The file.
        ask_passphrase: Ask for the passphrase, without echo.
        host: The server, for the prompt.

    Returns:
        The key text and the passphrase.

    Raises:
        NodeError: When the file cannot be read.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NodeError(f"Could not read {path}", details=exc.strerror or str(exc)) from exc
    passphrase = (
        click.prompt(f"Passphrase of {path.name} (for {host})", hide_input=True)
        if ask_passphrase
        else None
    )
    return text, passphrase


def _read_password(from_stdin: bool, user: str, host: str) -> str:
    """
    Read the password without it ever being an argument.

    Args:
        from_stdin: Read one line from standard input instead of asking.
        user: The account, for the prompt.
        host: The server, for the prompt.

    Returns:
        The password.

    Raises:
        NodeError: When none can be had (standard input is not a terminal and
            ``--password-stdin`` was not given).
    """
    if from_stdin:
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            raise NodeError("--password-stdin read an empty line", field="credential")
        return line
    if not _interactive():
        raise NodeError(
            "There is no terminal to ask the password on",
            details="Pass --password-stdin and give it on standard input, or use --key-file.",
            field="credential",
        )
    return str(click.prompt(f"Password of {user}@{host}", hide_input=True))


@fleet_cli.command("enroll")
@click.argument("name")
@click.option("--host", required=True, metavar="ADDRESS", help="The server's address or name.")
@click.option("--port", default=22, show_default=True, type=int, help="Where its sshd listens.")
@click.option(
    "--ssh-user",
    default="root",
    show_default=True,
    help="The account the credential is for: root, or an account with passwordless sudo.",
)
@click.option(
    "--key-file",
    type=click.Path(path_type=Path, exists=True, dir_okay=False),
    default=None,
    help="A private key to log in with instead of a password. It is read, used once and forgotten.",
)
@click.option(
    "--ask-passphrase", is_flag=True, help="The key has a passphrase: ask for it, without echo."
)
@click.option(
    "--password-stdin",
    is_flag=True,
    help="Read the password from one line of standard input instead of asking for it.",
)
@click.option(
    "--host-key-fingerprint",
    default=None,
    metavar="SHA256:...",
    help="The server's ed25519 host key fingerprint, confirmed some other way (the provider's "
    "console, ssh-keygen -lf on the server). Without it the fingerprint is shown and asked.",
)
@click.option(
    "--access",
    type=click.Choice(["read", "deploy", "admin"]),
    default="admin",
    show_default=True,
    help="The most this central may do on the server.",
)
@click.option(
    "--allow-host-access/--no-host-access",
    default=False,
    help="Also let this central reach the host itself there (SSH keys, sshd, the firewall).",
)
@click.option(
    "--no-install",
    is_flag=True,
    help="Do not install Noust on the server: fail when it is not there.",
)
@click.option(
    "--replace-token",
    is_flag=True,
    help="The server may already trust a token of this central: revoke it and issue a new one.",
)
@json_option("Print the registered node as JSON.")
@pass_context
def enroll_command(
    ctx: Context,
    name: str,
    host: str,
    port: int,
    ssh_user: str,
    key_file: Path | None,
    ask_passphrase: bool,
    password_stdin: bool,
    host_key_fingerprint: str | None,
    access: str,
    allow_host_access: bool,
    no_install: bool,
    replace_token: bool,
) -> None:
    """
    Enroll the server at --host as NAME: install Noust if needed, authorize this central, join it.

    The central connects once with a password or a key, to the host key you
    confirm, and keeps neither. Afterwards it reaches the server only through
    its restricted tunnel account, as if you had run 'noust fleet authorize'
    there yourself and pasted the join code here. Needs two-factor sign-in on
    this central, like 'noust node add'.
    """
    from noust.fleet.enroll import Credential, EnrollRequest, NodeEnroller
    from noust.fleet.nodes import cli_actor

    logger = ctx.logger
    if ctx.dry_run:
        logger.info(
            f"would connect to {ssh_user}@{host}:{port}, confirm its host key, install Noust "
            f"there if missing, authorize this central (access {access}) and add it as {name}"
        )
        return
    enroller = NodeEnroller()
    scan = enroller.scan_host_key(host, port)
    if host_key_fingerprint is None:
        logger.info(f"{host}:{port} presents the ed25519 host key {scan.fingerprint}")
        logger.info(
            "Compare it with the server's own: ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub "
            "(or your provider's console)."
        )
        if not _interactive() or not click.confirm(
            f"Is that the host key of {name}?", default=False
        ):
            raise NodeError(
                "The host key was not confirmed",
                details="Run it again and confirm the fingerprint, or pass it with "
                "--host-key-fingerprint. Nothing was sent.",
            )
        host_key_fingerprint = scan.fingerprint

    if key_file is not None:
        text, passphrase = _read_key(key_file, ask_passphrase, host)
        credential = Credential(private_key=text, passphrase=passphrase)
    else:
        credential = Credential(password=_read_password(password_stdin, ssh_user, host))

    def log(message: str, level: str = "info") -> None:
        if ctx.json_output:
            return
        method = getattr(logger, _LEVELS.get(level, "info"))
        method(message)

    node = enroller.enroll(
        EnrollRequest(
            name=name,
            host=host,
            port=port,
            ssh_user=ssh_user,
            host_key_fingerprint=host_key_fingerprint,
            credential=credential,
            access=access,
            host_access=allow_host_access,
            install=not no_install,
            replace_token=replace_token,
        ),
        actor=cli_actor(),
        log=log,
    )
    if ctx.json_output:
        click.echo(json.dumps(node.to_dict()))
        return
    logger.success(f"Node {name} added: Noust {node.version or 'unknown'}, {node.status}")
    logger.info(
        "The password or key was used once and is gone from this central. The server is reached "
        "through its restricted tunnel account from now on."
    )
