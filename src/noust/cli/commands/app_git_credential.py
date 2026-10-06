# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust app git-credential``: the user and token a private https repository needs.

The token is kept in the secret store and handed to git in its environment,
instead of living in the source URL. It is read from a file, from standard
input or from a prompt that does not echo, never from an argument: everything
on a command line is visible in ``ps``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from noust.cli.app import Context, NoustGroup, global_flags, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.fs import is_rehearsal
from noust.managers.source_credentials import (
    MAX_SECRET,
    CredentialView,
    GitCredentialManager,
    dumps,
)


def _read_secret(path: str | None) -> str:
    """
    Read the token from a file, from standard input, or from a prompt.

    Args:
        path: A file, ``-`` for standard input, or None to ask without echo.

    Returns:
        The token as read.

    Raises:
        ValidationError: When the file cannot be read.
    """
    if path is None:
        return str(click.prompt("Token", hide_input=True))
    try:
        if path == "-":
            return sys.stdin.read(MAX_SECRET + 1)
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValidationError(
            f"Cannot read the token from {path}", details=str(exc), field="secret"
        ) from exc


def _describe(ctx: Context, view: CredentialView) -> None:
    """
    Print an application's credential for a person.

    Args:
        ctx: The command's context.
        view: What the manager found.
    """
    logger = ctx.logger
    logger.key_value("Application", view.domain)
    logger.key_value("Repository", view.repository or "not a git repository")
    if view.configured:
        logger.key_value("Credential", f"stored, signs in as {view.username}")
    else:
        logger.key_value("Credential", "none")
        logger.info(f"Set one with: noust app git-credential set {view.domain} --username USER")
    if view.configured and not view.source_is_https:
        logger.warning(
            f"{view.domain} is not cloned over https, which is what a credential signs in to: "
            "an ssh source uses its deploy key or the server-wide key."
        )


@click.group("git-credential", cls=NoustGroup)
def git_credential() -> None:
    """The user and token a private https repository needs, kept as a secret."""


@git_credential.command("show", read_only=True)
@click.argument("domain")
@global_flags
@json_option("Print the state (never the token) as JSON.")
@pass_context
def show_command(ctx: Context, domain: str) -> None:
    """Say whether the application's repository has a credential, and as whom."""
    view = GitCredentialManager().view(domain)
    if ctx.json_output:
        click.echo(dumps(view))
        return
    _describe(ctx, view)


@git_credential.command("set")
@click.argument("domain")
@click.option("--username", "-u", required=True, help="The user to sign in as.")
@click.option(
    "--secret-file",
    help="File with the token, or - for standard input. Without it the token is asked for "
    "without echo.",
)
@global_flags
@pass_context
def set_command(ctx: Context, domain: str, username: str, secret_file: str | None) -> None:
    """
    Keep the user and token git signs in to this application's repository with.

    For Bitbucket Cloud: your username or x-bitbucket-api-token-auth with an API
    token, or x-token-auth with a repository or workspace access token. App
    passwords no longer work there. A deploy key is the alternative that needs
    no token: noust app deploy-key generate.
    """
    manager = GitCredentialManager()
    token = _read_secret(secret_file)
    if is_rehearsal():
        ctx.logger.info(f"Would keep a credential for the repository of {domain}")
        return
    view = manager.put(domain, username, token)
    ctx.logger.success(f"Credential stored for {view.repository}, signing in as {view.username}")
    if not view.source_is_https:
        ctx.logger.warning(
            f"{domain} is not cloned over https; deploy it from the https URL of the repository "
            "for the credential to be used."
        )


@git_credential.command("remove")
@click.argument("domain")
@click.option("--yes", "-y", is_flag=True, help="Do not ask for confirmation.")
@global_flags
@pass_context
def remove_command(ctx: Context, domain: str, yes: bool) -> None:
    """Forget the credential of the application's repository."""
    manager = GitCredentialManager()
    view = manager.view(domain)
    if not view.configured:
        ctx.logger.info(f"{domain} has no stored credential")
        return
    if not yes and not ctx.dry_run:
        click.confirm(
            f"Forget the credential of {view.repository}? A deploy that needs it fails until "
            "another is in place",
            abort=True,
        )
    if is_rehearsal():
        ctx.logger.info(f"Would forget the credential of {view.repository}")
        return
    manager.remove(domain)
    ctx.logger.success(f"Forgot the credential of {view.repository}")
    ctx.logger.info("Revoke the token at the code host too: it still works there.")
