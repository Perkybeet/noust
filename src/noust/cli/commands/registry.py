# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust registry``: the accounts Noust pulls and builds private images with.

``add`` keeps a registry account (Docker Hub, GHCR, GitLab, Amazon ECR, Azure
ACR, Google Artifact Registry or any host) with its password in the secret
store; ``test`` tries it in a private Docker configuration that is removed
straight after; ``attach`` says which applications use it, and from then on
every ``docker`` command Noust runs for them has the account logged in for
that command only. Each is :mod:`noust.managers.registry_logins`, which
``/api/registries`` calls too. This module only parses and presents.

The password is read from standard input (``--password-stdin``) or from the
``NOUST_REGISTRY_PASSWORD`` variable, never an argument: every local user
could read an argument.
"""

from __future__ import annotations

import json
import os
import sys

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.registry_store import KINDS, RegistryLogin
from noust.managers.registry_logins import RegistryManager
from noust.validators.domain import validate_domain

PASSWORD_ENV = "NOUST_REGISTRY_PASSWORD"  # noqa: S105 - the name of a variable, not a credential


def _actor() -> str:
    """
    Name whoever runs this command, for the records.

    Returns:
        The audit's label for the operating system identity.
    """
    from noust.core.audit.actor import cli_actor

    return cli_actor().label


def _password(from_stdin: bool, required: bool) -> str | None:
    """
    Read the password from where it may come from.

    Args:
        from_stdin: ``--password-stdin`` was given.
        required: Whether a missing password is an error.

    Returns:
        The password, or None when none was given and none is needed.

    Raises:
        ValidationError: One is needed and none was given.
    """
    if from_stdin:
        return sys.stdin.read().rstrip("\r\n")
    value = os.environ.get(PASSWORD_ENV)
    if value:
        return value
    if required:
        raise ValidationError(
            "A password is needed",
            details=f"Pipe it in with --password-stdin, or set {PASSWORD_ENV}. It is never an "
            "argument: every local user could read it.",
            field="password",
        )
    return None


def _show(ctx: Context, login: RegistryLogin) -> None:
    """
    Print one account, for a person or as JSON.

    Args:
        ctx: The CLI context.
        login: The account.
    """
    if ctx.json_output:
        click.echo(json.dumps(login.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Login", login.name)
    logger.key_value("Registry", f"{login.server} ({login.kind})")
    logger.key_value("User", login.username)
    logger.key_value(
        "Last test",
        f"{login.last_status} at {login.last_tested_at}" if login.last_status else "never tried",
    )
    if login.last_error:
        logger.warning(login.last_error)
    logger.key_value("Used by", ", ".join(login.apps) or "no application")


@click.group("registry", cls=NoustGroup)
def cli() -> None:
    """The container registry accounts used to pull and build private images."""


@cli.command("list", read_only=True)
@json_option("Print the accounts as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the registry accounts, without their passwords."""
    logins = RegistryManager().accounts.list()
    if ctx.json_output:
        click.echo(json.dumps([login.to_dict() for login in logins]))
        return
    if not logins:
        ctx.logger.info("No registry logins. Add one with: noust registry add NAME --kind ghcr ...")
        return
    for login in logins:
        status = login.last_status or "untested"
        ctx.logger.key_value(
            login.name,
            f"{login.server}  {login.username}  {status}  "
            f"{', '.join(login.apps) or 'no application'}",
        )


@cli.command("show", read_only=True)
@click.argument("name")
@json_option("Print the account as JSON.")
@pass_context
def show_command(ctx: Context, name: str) -> None:
    """Show one account."""
    login = RegistryManager().accounts.get(name)
    if login is None:
        raise click.UsageError(f"No registry login named {name}")
    _show(ctx, login)


@cli.command("add")
@click.argument("name")
@click.option(
    "--kind",
    type=click.Choice(list(KINDS)),
    required=True,
    help="Which registry it is: it decides the host and how a login is made.",
)
@click.option(
    "--username", "-u", required=True, help="The account (for ECR, the AWS access key id)."
)
@click.option(
    "--server",
    metavar="HOST",
    help="The registry host. Needed for ecr, acr, gar and custom; the other kinds have their own.",
)
@click.option(
    "--password-stdin", is_flag=True, help="Read the password or token from standard input."
)
@json_option("Print the account as JSON.")
@pass_context
def add_command(
    ctx: Context, name: str, kind: str, username: str, server: str | None, password_stdin: bool
) -> None:
    """
    Add a registry account.

    The password (for ECR, the AWS secret access key) comes from standard input
    or NOUST_REGISTRY_PASSWORD. It is kept in the secret store, 0600, and never
    printed.
    """
    password = _password(password_stdin, required=True) or ""
    if ctx.dry_run:
        ctx.logger.info(f"Would add the registry login {name} ({kind})")
        return
    login = RegistryManager().add(name, kind, username, password, server=server, actor=_actor())
    if ctx.json_output:
        click.echo(json.dumps(login.to_dict()))
        return
    ctx.logger.success(f"Added the registry login {name}")
    ctx.logger.info(f"Try it with: noust registry test {name}")


@cli.command("update")
@click.argument("name")
@click.option("--kind", type=click.Choice(list(KINDS)))
@click.option("--username", "-u")
@click.option("--server", metavar="HOST")
@click.option("--password-stdin", is_flag=True, help="Replace the password from standard input.")
@json_option("Print the account as JSON.")
@pass_context
def update_command(
    ctx: Context,
    name: str,
    kind: str | None,
    username: str | None,
    server: str | None,
    password_stdin: bool,
) -> None:
    """Change an account's kind, host, user or password; a change forgets the last test."""
    password = _password(password_stdin, required=False)
    if kind is None and username is None and server is None and password is None:
        raise click.UsageError(
            "Give something to change: --kind, --username, --server or the password"
        )
    if ctx.dry_run:
        ctx.logger.info(f"Would change the registry login {name}")
        return
    login = RegistryManager().update(
        name, kind=kind, server=server, username=username, password=password
    )
    if ctx.json_output:
        click.echo(json.dumps(login.to_dict()))
        return
    ctx.logger.success(f"Changed the registry login {name}")


@cli.command("remove")
@click.argument("name")
@click.option("--yes", "-y", is_flag=True, help="Remove it without asking.")
@pass_context
def remove_command(ctx: Context, name: str, yes: bool) -> None:
    """Remove an account, its password and which applications use it."""
    manager = RegistryManager()
    login = manager.accounts.get(name)
    if login is None:
        raise click.UsageError(f"No registry login named {name}")
    if ctx.dry_run:
        ctx.logger.info(f"Would remove the registry login {name}")
        return
    if login.apps and not yes:
        click.confirm(
            f"{name} is used by {', '.join(login.apps)}, which will pull without it. Remove it?",
            abort=True,
        )
    manager.remove(name)
    ctx.logger.success(f"Removed the registry login {name}")


@cli.command("test")
@click.argument("name")
@json_option("Print the account, with how the test went, as JSON.")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """
    Try an account against its registry.

    It logs in to a Docker configuration of its own, removed straight after, so
    nothing of the login stays on the server.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would test the registry login {name}")
        return
    login = RegistryManager().test(name)
    if ctx.json_output:
        click.echo(json.dumps(login.to_dict()))
    elif login.last_status == "ok":
        ctx.logger.success(f"{login.server} accepted {login.username}")
    else:
        ctx.logger.error(login.last_error or "The registry did not accept the account")
    if login.last_status != "ok":
        raise SystemExit(1)


@cli.command("attach")
@click.argument("domain")
@click.argument("names", nargs=-1)
@click.option("--none", "clear", is_flag=True, help="Use no account: pull anonymously again.")
@pass_context
def attach_command(ctx: Context, domain: str, names: tuple[str, ...], clear: bool) -> None:
    """
    Say which registry accounts an application pulls and builds with.

    Replaces the application's accounts with NAMES; it takes effect on the next
    deployment or update.
    """
    validated = validate_domain(domain)
    if not names and not clear:
        raise click.UsageError("Name the accounts, or --none to use none")
    if names and clear:
        raise click.UsageError("Name the accounts, or --none, not both")
    if ctx.dry_run:
        ctx.logger.info(
            f"Would set the registry logins of {validated} to {', '.join(names) or 'none'}"
        )
        return
    RegistryManager().attach(validated, list(names), actor=_actor())
    ctx.logger.success(
        f"{validated} uses {', '.join(names)}" if names else f"{validated} uses no registry account"
    )
