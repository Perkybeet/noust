# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust user`` command group: the people who sign in to the console.

A thin front end over :class:`~noust.core.accounts.AccountManager`, the manager
the console's account screens call, so an account created here signs in there
and every rule - password policy, separation of duties, single-use invitations
- is the same one. It runs as root and is not held to roles: it is the channel
through which the first account is created and access is recovered when no
security officer can sign in. What it does is on the audit trail with the
identity of whoever ran it.

Passwords are never taken from the command line, which every local user can
read in ``ps`` and which lands in shell history: they are typed at a hidden
prompt, or piped with ``--stdin``.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.accounts import ROLES, Account, AccountManager
from noust.core.accounts.manager import DEFAULT_INVITATION_HOURS
from noust.core.accounts.model import ROLE_ADMIN
from noust.core.exceptions import DependencyError, ValidationError


def _fmt(timestamp: float | None) -> str:
    """
    Render a UNIX timestamp for a table cell.

    Args:
        timestamp: The timestamp, or None.

    Returns:
        A human-readable local time, or a placeholder for None.
    """
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")


def _manager() -> AccountManager:
    """
    Returns:
        The account manager over this server's store.
    """
    return AccountManager()


def _record(event: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """
    Put an account change on the audit trail, as whoever runs this command.

    Args:
        event: The catalog name, such as ``user.create``.
        target: What it was done to, such as ``account:maria``.
        detail: Context, never a credential.
    """
    from noust.core.audit import record

    record(event, target=target, details=detail)


def _read_password(username: str, from_stdin: bool) -> str:
    """
    Read a password without it ever touching the command line.

    Args:
        username: The account it is for, for the prompt.
        from_stdin: Read one line from standard input instead of prompting.

    Returns:
        The password.

    Raises:
        ValidationError: When standard input is empty.
    """
    if from_stdin:
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            raise ValidationError(
                "No password on standard input",
                details="Pipe it in, such as: printf '%s\\n' \"$PASSWORD\" | noust user create ...",
            )
        return line
    return str(
        click.prompt(
            f"Password for {username}", hide_input=True, confirmation_prompt=True, type=str
        )
    )


def _console_tokens() -> Any | None:
    """
    The console's token manager, when the console's dependencies are installed.

    Returns:
        The manager, or None: sessions and tokens of an account also stop at
        their next use without it, because every request reads the account.
    """
    from noust.cli.web_state import token_manager

    try:
        return token_manager()
    except DependencyError:
        return None


def _end_access(account: Account) -> str:
    """
    End an account's sessions and tokens now, when the console is installed.

    Args:
        account: The account.

    Returns:
        A sentence saying what was ended, or empty.
    """
    manager = _console_tokens()
    if manager is None:
        return ""
    sessions, tokens = manager.end_account_access(account.id)
    manager.sessions.close()
    return f" Ended {sessions} session(s) and revoked {tokens} API token(s)."


def _adopt_tokens(account: Account) -> str:
    """
    Hand the tokens issued before accounts existed to the first admin account.

    Args:
        account: An account that was just created or given a role.

    Returns:
        A sentence saying how many were adopted, or empty.
    """
    if account.role != ROLE_ADMIN:
        return ""
    first = _manager().first_admin()
    manager = _console_tokens()
    if first is None or first.id != account.id or manager is None:
        return ""
    adopted = manager.adopt_unowned_tokens(account.id)
    manager.sessions.close()
    return f" It adopted {adopted} API token(s) issued before accounts existed." if adopted else ""


def _show(ctx: Context, account: Account, message: str) -> None:
    """
    Report the account after a change.

    Args:
        ctx: The CLI context.
        account: The account.
        message: What was done.
    """
    if ctx.json_output:
        click.echo(json.dumps({"account": account.to_dict(), "message": message}))
        return
    ctx.logger.success(message)


@click.group("user", cls=NoustGroup)
def cli() -> None:
    """Manage the accounts that sign in to the console, each with one role."""


@cli.command("list")
@json_option("Print the accounts as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List every account, its role, status and last sign-in."""
    manager = _manager()
    accounts = manager.list_all()
    conflicts = manager.separation_conflicts()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {"accounts": [account.to_dict() for account in accounts], "conflicts": conflicts}
            )
        )
        return
    logger = ctx.logger
    if not accounts:
        logger.info(
            "No accounts yet: the master token is the console's only credential. "
            "Create the first with 'noust user create NAME --role admin'."
        )
        return
    logger.table(
        ["Username", "Role", "Status", "2FA", "Last sign-in", "Failures", "Person"],
        [
            [
                account.username,
                account.role,
                account.effective_status(),
                "yes" if account.has_mfa else "no",
                _fmt(account.last_login_at),
                str(account.failures_since_login),
                account.person_ref or "-",
            ]
            for account in accounts
        ],
    )
    for conflict in conflicts:
        names = ", ".join(f"{item['username']} ({item['role']})" for item in conflict["accounts"])
        note = "documented exception" if conflict["exception"] else "no exception on record"
        logger.warning(f"{conflict['person_ref']} holds incompatible roles: {names} - {note}")


@cli.command("create")
@click.argument("username")
@click.option("--role", type=click.Choice(ROLES), required=True, help="The account's one role.")
@click.option("--display-name", default=None, help="How the console greets them.")
@click.option(
    "--person-ref",
    default=None,
    help="Who the account belongs to (an e-mail), for separation of duties.",
)
@click.option("--stdin", "from_stdin", is_flag=True, help="Read the password from standard input.")
@pass_context
def create_command(
    ctx: Context,
    username: str,
    role: str,
    display_name: str | None,
    person_ref: str | None,
    from_stdin: bool,
) -> None:
    """
    Create an account with a password, typed at a hidden prompt.

    The account enrols its authenticator at its first sign-in, before it can
    do anything else. To let the person choose their own password, use
    'noust user invite' instead.
    """
    password = _read_password(username, from_stdin)
    account = _manager().create(
        username,
        role,
        password=password,
        display_name=display_name,
        person_ref=person_ref,
        created_by="cli",
    )
    _record("user.create", f"account:{account.username}", {"role": account.role})
    adopted = _adopt_tokens(account)
    _show(ctx, account, f"Account created: {account.username} ({account.role}).{adopted}")


@cli.command("invite")
@click.argument("username")
@click.option("--role", type=click.Choice(ROLES), default=None, help="Role of a new account.")
@click.option("--display-name", default=None, help="Display name of a new account.")
@click.option("--person-ref", default=None, help="Person reference of a new account.")
@click.option(
    "--expires-hours",
    type=int,
    default=DEFAULT_INVITATION_HOURS,
    show_default=True,
    help="How long the invitation code is valid.",
)
@pass_context
def invite_command(
    ctx: Context,
    username: str,
    role: str | None,
    display_name: str | None,
    person_ref: str | None,
    expires_hours: int,
) -> None:
    """
    Invite a person: they set their own password and authenticator.

    For a new USERNAME, --role is required. For an existing account the
    invitation is a recovery: accepting it replaces the password and the
    authenticator. The code is printed once; only its digest is stored.
    """
    account, code = _manager().invite(
        username,
        role,
        display_name=display_name,
        person_ref=person_ref,
        created_by="cli",
        expires_hours=expires_hours,
    )
    _record("user.invite", f"account:{account.username}", {"expires_hours": expires_hours})
    if ctx.json_output:
        click.echo(json.dumps({"account": account.to_dict(), "code": code}))
        return
    logger = ctx.logger
    logger.success(f"Invitation issued for {account.username} ({account.role})")
    logger.blank()
    click.echo(f"Code: {code}")
    logger.blank()
    logger.info(
        f"Valid for {expires_hours} hours, once. The person opens the console's invitation "
        "page and pastes it there."
    )
    if ctx.dry_run:
        logger.warning("Rehearsal: this invitation was not saved and will not work.")


@cli.command("set-role")
@click.argument("username")
@click.argument("role", type=click.Choice(ROLES))
@pass_context
def set_role_command(ctx: Context, username: str, role: str) -> None:
    """Change an account's role; its sessions and tokens follow at their next request."""
    manager = _manager()
    previous = manager.require(username).role
    account = manager.set_role(username, role)
    _record("user.role_change", f"account:{account.username}", {"from": previous, "to": role})
    adopted = _adopt_tokens(account)
    _show(ctx, account, f"{account.username} is now {account.role} (was {previous}).{adopted}")


@cli.command("disable")
@click.argument("username")
@click.option("--reason", default=None, help="Why, for the record.")
@pass_context
def disable_command(ctx: Context, username: str, reason: str | None) -> None:
    """Disable an account: its sessions and tokens stop working at once."""
    account = _manager().disable(username, reason)
    ended = _end_access(account)
    _record("user.disable", f"account:{account.username}", {"reason": reason or ""})
    _show(ctx, account, f"Account disabled: {account.username}.{ended}")


@cli.command("enable")
@click.argument("username")
@pass_context
def enable_command(ctx: Context, username: str) -> None:
    """Enable a disabled account again."""
    account = _manager().enable(username)
    _record("user.enable", f"account:{account.username}")
    _show(ctx, account, f"Account enabled: {account.username} ({account.effective_status()}).")


@cli.command("unlock")
@click.argument("username")
@pass_context
def unlock_command(ctx: Context, username: str) -> None:
    """Lift an account's lockout before it runs out."""
    account = _manager().unlock(username)
    _record("user.unlock", f"account:{account.username}")
    _show(ctx, account, f"Account unlocked: {account.username}.")


@cli.command("reset-mfa")
@click.argument("username")
@pass_context
def reset_mfa_command(ctx: Context, username: str) -> None:
    """Remove an account's authenticator; it enrols a new one at its next sign-in."""
    account = _manager().reset_mfa(username)
    manager = _console_tokens()
    if manager is not None:
        manager.sessions.revoke_account(account.id)
        manager.sessions.close()
    _record("auth.mfa.reset", f"account:{account.username}")
    _show(ctx, account, f"Second factor reset for {account.username}; it enrols a new one next.")


@cli.command("remove")
@click.argument("username")
@click.option("-f", "--force", is_flag=True, help="Do not ask for confirmation.")
@pass_context
def remove_command(ctx: Context, username: str, force: bool) -> None:
    """
    Remove an account. Prefer 'disable': a removed account's name in the
    audit log no longer names a record.
    """
    manager = _manager()
    account = manager.require(username)
    if not force and not click.confirm(
        f"Remove the account {account.username}? Its sessions and tokens end now", default=False
    ):
        ctx.logger.info("Cancelled")
        return
    ended = _end_access(account)
    manager.remove(username)
    _record("user.remove", f"account:{account.username}")
    _show(ctx, account, f"Account removed: {account.username}.{ended}")


@cli.group("exception", cls=NoustGroup)
def exception_group() -> None:
    """Documented exceptions to the separation of duties, with a reason and an end."""


@exception_group.command("add")
@click.argument("person_ref")
@click.option("--reason", required=True, help="Why; what an auditor reads.")
@click.option("--days", type=int, default=90, show_default=True, help="How long it applies.")
@pass_context
def exception_add_command(ctx: Context, person_ref: str, reason: str, days: int) -> None:
    """Let PERSON_REF hold incompatible roles, for a documented reason and a time."""
    exception = _manager().add_exception(person_ref, reason, days=days, created_by="cli")
    _record(
        "user.sod_exception",
        f"person:{exception.person_ref}",
        {"days": days, "reason": exception.reason[:200]},
    )
    if ctx.json_output:
        click.echo(json.dumps(exception.to_dict()))
        return
    ctx.logger.success(
        f"Exception {exception.id} recorded for {exception.person_ref} until "
        f"{_fmt(exception.expires_at)}"
    )


@exception_group.command("list")
@json_option("Print the exceptions as JSON.")
@pass_context
def exception_list_command(ctx: Context) -> None:
    """List every separation-of-duties exception ever recorded."""
    exceptions = _manager().list_exceptions()
    if ctx.json_output:
        click.echo(json.dumps({"exceptions": [item.to_dict() for item in exceptions]}))
        return
    if not exceptions:
        ctx.logger.info("No separation-of-duties exceptions have been recorded.")
        return
    ctx.logger.table(
        ["ID", "Person", "Until", "In force", "Reason"],
        [
            [
                str(item.id),
                item.person_ref,
                _fmt(item.expires_at),
                "yes" if item.in_force() else "no",
                item.reason,
            ]
            for item in exceptions
        ],
    )


@exception_group.command("revoke")
@click.argument("exception_id", type=int)
@pass_context
def exception_revoke_command(ctx: Context, exception_id: int) -> None:
    """Withdraw an exception before it runs out."""
    if not _manager().revoke_exception(exception_id):
        ctx.logger.error(f"No exception in force with id {exception_id}")
        raise SystemExit(1)
    _record("user.sod_exception", f"exception:{exception_id}", {"withdrawn": True})
    ctx.logger.success(f"Exception {exception_id} withdrawn")
