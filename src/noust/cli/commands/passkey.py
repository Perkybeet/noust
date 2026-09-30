# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust passkey`` command group: the passkeys people sign in to the console with.

A front end over :class:`~noust.core.accounts.passkeys.PasskeyManager`, the
manager the console's passkey endpoints call. It lists and removes, and it is
root's recovery lever (``reset``) when every passkey of an account, or of the
master token, is lost. It cannot register one: that takes a browser and the
authenticator in the person's hand, which is the point of a passkey.

Everything it changes is on the audit trail with the operating system
identity of whoever ran it.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.accounts import AccountManager
from noust.core.accounts.passkeys import Passkey, PasskeyManager
from noust.core.exceptions import DependencyError, ValidationError


def _fmt(timestamp: float | None) -> str:
    """
    Args:
        timestamp: UNIX seconds, or None.

    Returns:
        Local time for a table cell, or ``-``.
    """
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")


def _record(event: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """
    Put a change on the audit trail, as whoever runs this command.

    Args:
        event: The catalog name.
        target: What it was done to.
        detail: Context, never a credential.
    """
    from noust.core.audit import record

    record(event, target=target, details=detail)


def _owner(username: str | None, master: bool) -> tuple[int | None, str]:
    """
    Resolve whose passkeys a command is about.

    Args:
        username: An account's name.
        master: The master token's instead.

    Returns:
        The account id (None for the master token) and a label for messages.

    Raises:
        ValidationError: When both or neither were given.
        AccountNotFoundError: When no account has that name.
    """
    if master == bool(username):
        raise ValidationError(
            "Name an account, or pass --master, but not both",
            details="Such as: noust passkey reset maria, or noust passkey reset --master.",
        )
    if master:
        return None, "the master token"
    account = AccountManager().require(str(username))
    return account.id, account.username


def _end_sessions(account_id: int | None) -> str:
    """
    End an account's sessions, when the console is installed.

    Args:
        account_id: The account, or None for the master token.

    Returns:
        A sentence saying what was ended, or what to do.
    """
    if account_id is None:
        return (
            " Sessions the master token has open stay open; rotate it with "
            "'noust web token --new' if it may be known to someone else."
        )
    from noust.cli.web_state import token_manager

    try:
        manager = token_manager()
    except DependencyError:
        return ""
    ended = manager.sessions.revoke_account(account_id)
    manager.sessions.close()
    return f" Ended {ended} session(s)."


def _row(passkey: Passkey) -> list[str]:
    """
    Args:
        passkey: A passkey.

    Returns:
        Its table row.
    """
    return [
        str(passkey.id),
        passkey.to_dict()["owner"],
        passkey.name,
        "synced" if passkey.synced else "device-bound",
        passkey.rp_id,
        passkey.algorithm,
        _fmt(passkey.created_at),
        _fmt(passkey.last_used_at),
    ]


@click.group("passkey", cls=NoustGroup)
def cli() -> None:
    """List and remove the passkeys that sign in to the console; recover when all are lost."""


@cli.command("list", read_only=True)
@click.argument("username", required=False)
@click.option("--master", is_flag=True, help="Only the master token's own passkeys.")
@json_option("Print the passkeys as JSON.")
@pass_context
def list_command(ctx: Context, username: str | None, master: bool) -> None:
    """List every passkey on this server, or those of one account (or --master)."""
    manager = PasskeyManager()
    if username or master:
        account_id, _label = _owner(username, master)
        found = manager.list(account_id)
    else:
        found = manager.list_all()
    if ctx.json_output:
        click.echo(json.dumps({"passkeys": [item.to_dict() for item in found]}))
        return
    if not found:
        ctx.logger.info("No passkeys. They are added from the console, in Settings, Security.")
        return
    ctx.logger.table(
        ["ID", "Owner", "Name", "Kind", "Registered under", "Algorithm", "Added", "Last used"],
        [_row(item) for item in found],
    )
    for item in found:
        if item.clone_warning_at is not None:
            ctx.logger.warning(
                f"Passkey {item.id} ({item.name}) reported a signature counter that went back on "
                f"{_fmt(item.clone_warning_at)}: it may have been copied. Remove it."
            )


@cli.command("remove")
@click.argument("passkey_id", type=int)
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, passkey_id: int, force: bool) -> None:
    """Remove one passkey, whoever it belongs to."""
    manager = PasskeyManager()
    passkey = manager.get(passkey_id)
    if passkey is None:
        raise ValidationError(
            f"No passkey with id {passkey_id}", details="List them with 'noust passkey list'."
        )
    owner = passkey.to_dict()["owner"]
    if not force and not click.confirm(
        f"Remove the passkey '{passkey.name}' of {owner}?", default=False
    ):
        ctx.logger.info("Cancelled")
        return
    manager.remove(passkey_id, account_id=None, force=True)
    _record("auth.passkey.remove", f"passkey:{passkey.id}", {"owner": owner, "name": passkey.name})
    if ctx.json_output:
        click.echo(json.dumps({"removed": passkey.to_dict()}))
        return
    ctx.logger.success(f"Removed the passkey '{passkey.name}' of {owner}.")
    if (
        passkey.account_id is not None
        and not AccountManager().require_id(passkey.account_id).has_mfa
    ):
        ctx.logger.warning(f"{owner} has no second factor left: it enrols one at its next sign-in.")


@cli.command("reset")
@click.argument("username", required=False)
@click.option("--master", is_flag=True, help="The master token's own passkeys.")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def reset_command(ctx: Context, username: str | None, master: bool, force: bool) -> None:
    """
    Remove every passkey of one account, or of the master token (--master).

    The recovery lever when they are all lost. An account with no second
    factor left enrols one at its next sign-in, and its sessions end now; for
    an account, 'noust user reset-mfa' also removes its authenticator.
    """
    account_id, label = _owner(username, master)
    manager = PasskeyManager()
    count = manager.count(account_id)
    if count == 0:
        ctx.logger.info(f"{label[0].upper()}{label[1:]} has no passkeys.")
        return
    if not force and not click.confirm(f"Remove all {count} passkey(s) of {label}?", default=False):
        ctx.logger.info("Cancelled")
        return
    removed = manager.reset(account_id)
    _record(
        "auth.passkey.reset",
        f"account:{label}" if account_id is not None else "master",
        {"removed": removed},
    )
    ended = _end_sessions(account_id)
    if ctx.json_output:
        click.echo(json.dumps({"owner": label, "removed": removed}))
        return
    ctx.logger.success(f"Removed {removed} passkey(s) of {label}.{ended}")
