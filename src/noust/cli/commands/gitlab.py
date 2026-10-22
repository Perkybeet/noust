# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``noust gitlab``: GitLab (gitlab.com or self-hosted) as a source.

``connect`` keeps an access token (read from standard input or ``NOUST_GITLAB_TOKEN``, never an
argument) as a connection; ``list``, ``test`` and ``remove`` manage connections; ``projects``,
``groups`` and ``branches`` are what the console's picker lists; ``oauth-app`` records the OAuth
application that lets people connect from the console without a token. A connection also clones
the private projects of its host that have no credential of their own. Each is
:mod:`noust.integrations.gitlab.service`, which ``/api/integrations/gitlab`` calls too. This module
only parses and presents.
"""

from __future__ import annotations

import json
import os
import sys

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.exceptions import ValidationError
from noust.core.gitlab_store import GitLabConnection
from noust.integrations.gitlab.service import GitLabService

TOKEN_ENV = "NOUST_GITLAB_TOKEN"  # noqa: S105 - the name of a variable, not a credential
SECRET_ENV = "NOUST_GITLAB_CLIENT_SECRET"  # noqa: S105 - the name of a variable, not a credential


def _secret(from_stdin: bool, variable: str, what: str) -> str:
    """
    Read a secret from where it may come from: standard input or the environment.

    Args:
        from_stdin: ``--...-stdin`` was given.
        variable: The environment variable to fall back on.
        what: What it is, for the message.

    Returns:
        The secret.

    Raises:
        ValidationError: None was given.
    """
    if from_stdin:
        value = sys.stdin.read().rstrip("\r\n")
    else:
        value = os.environ.get(variable, "")
    if not value:
        raise ValidationError(
            f"{what} is needed",
            details=f"Pipe it in (--{'token' if 'token' in what else 'client-secret'}-stdin) or "
            f"set {variable}. It is never an argument: every local user could read it.",
            field="token",
        )
    return value


def _show(ctx: Context, connection: GitLabConnection) -> None:
    """
    Print one connection, for a person or as JSON.

    Args:
        ctx: The CLI context.
        connection: The connection.
    """
    if ctx.json_output:
        click.echo(json.dumps(connection.to_dict()))
        return
    logger = ctx.logger
    logger.key_value("Connection", connection.name)
    logger.key_value("GitLab", f"{connection.host} ({connection.kind})")
    logger.key_value("User", connection.username or "unknown")
    logger.key_value(
        "Last check",
        f"{connection.last_status} at {connection.last_checked_at}"
        if connection.last_status
        else "never",
    )
    if connection.last_error:
        logger.warning(connection.last_error)


@click.group("gitlab", cls=NoustGroup)
def cli() -> None:
    """GitLab connections: a project and branch picker, and private clones."""


@cli.command("connect")
@click.argument("name")
@click.option("--host", required=True, help="The GitLab host: gitlab.com or git.example.com.")
@click.option(
    "--token-stdin",
    is_flag=True,
    help=f"Read the access token from standard input (else {TOKEN_ENV}).",
)
@json_option("Print the connection as JSON.")
@pass_context
def connect_command(ctx: Context, name: str, host: str, token_stdin: bool) -> None:
    """
    Add a connection that signs in with an access token.

    Create the token in GitLab (Preferences, Access tokens, or a group's or project's) with the
    scopes read_api and read_repository. GitLab is asked who it belongs to before it is kept.
    """
    if ctx.dry_run:
        ctx.logger.info(f"Would connect {name} to {host}")
        return
    token = _secret(token_stdin, TOKEN_ENV, "The access token")
    connection = GitLabService().connect_with_token(name, host, token)
    if ctx.json_output:
        click.echo(json.dumps(connection.to_dict()))
        return
    ctx.logger.success(f"Connected {connection.name} to {connection.host} as {connection.username}")


@cli.command("list", read_only=True)
@json_option("Print the connections as JSON.")
@pass_context
def list_command(ctx: Context) -> None:
    """List the GitLab connections and OAuth applications."""
    service = GitLabService()
    items = service.connections.list()
    applications = service.connections.oauth_applications()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "connections": [item.to_dict() for item in items],
                    "oauth_apps": [item.to_dict() for item in applications],
                }
            )
        )
        return
    if not items:
        ctx.logger.info("There are no GitLab connections. Add one: noust gitlab connect")
    for item in items:
        ctx.logger.list_item(
            f"{item.name}  {item.host}  {item.kind}  {item.username or '-'}  "
            f"{item.last_status or 'unchecked'}"
        )
    for application in applications:
        ctx.logger.info(f"OAuth application of {application.host}: {application.client_id}")


@cli.command("test")
@click.argument("name")
@json_option("Print the connection as JSON.")
@pass_context
def test_command(ctx: Context, name: str) -> None:
    """Ask GitLab who a connection is, and record whether it still works."""
    if ctx.dry_run:
        ctx.logger.info(f"Would ask GitLab who {name} is")
        return
    connection = GitLabService().check(name)
    _show(ctx, connection)


@cli.command("remove")
@click.argument("name")
@pass_context
def remove_command(ctx: Context, name: str) -> None:
    """Forget a connection and its token."""
    if ctx.dry_run:
        ctx.logger.info(f"Would remove the GitLab connection {name}")
        return
    if GitLabService().remove(name):
        ctx.logger.success(f"Removed {name}")
    else:
        ctx.logger.info(f"There is no GitLab connection named {name}")


@cli.command("groups", read_only=True)
@click.argument("name")
@json_option("Print the groups as JSON.")
@pass_context
def groups_command(ctx: Context, name: str) -> None:
    """List the groups a connection's user belongs to."""
    groups = GitLabService().groups(name)
    if ctx.json_output:
        click.echo(json.dumps({"items": groups}))
        return
    for group in groups:
        ctx.logger.list_item(f"{group['full_path']}  (id {group['id']})")


@cli.command("projects", read_only=True)
@click.argument("name")
@click.option("--group", help="Only the projects of this group (its full path) and subgroups.")
@click.option("--search", help="Only projects whose name contains this.")
@click.option("--limit", type=click.IntRange(1, 2000), default=200, show_default=True)
@json_option("Print the projects as JSON.")
@pass_context
def projects_command(
    ctx: Context, name: str, group: str | None, search: str | None, limit: int
) -> None:
    """List the projects a connection's user can read, most recently active first."""
    projects = GitLabService().projects(name, group=group, search=search, limit=limit)
    if ctx.json_output:
        click.echo(json.dumps({"items": projects}))
        return
    for project in projects:
        ctx.logger.list_item(
            f"{project['path']}  [{project['default_branch'] or '-'}]  {project['clone_url']}"
        )
    if not projects:
        ctx.logger.info("No project matches.")


@cli.command("branches", read_only=True)
@click.argument("name")
@click.argument("project")
@json_option("Print the branches as JSON.")
@pass_context
def branches_command(ctx: Context, name: str, project: str) -> None:
    """List a project's branches (PROJECT is its id or its path, group/project)."""
    branches = GitLabService().branches(name, project)
    if ctx.json_output:
        click.echo(json.dumps({"items": branches}))
        return
    for branch in branches:
        ctx.logger.list_item(f"{branch['name']}{'  (default)' if branch['default'] else ''}")


@cli.group("oauth-app")
def oauth_app_group() -> None:
    """The OAuth application registered in GitLab, so people can connect without a token."""


@oauth_app_group.command("set")
@click.option("--host", required=True, help="The GitLab host it is registered on.")
@click.option("--client-id", required=True, help="The application's id.")
@click.option(
    "--client-secret-stdin",
    is_flag=True,
    help=f"Read the application's secret from standard input (else {SECRET_ENV}).",
)
@pass_context
def oauth_app_set_command(
    ctx: Context, host: str, client_id: str, client_secret_stdin: bool
) -> None:
    """
    Record an OAuth application.

    Register it in GitLab (Admin area, Applications) with the scopes read_api and
    read_repository, not confidential unticked, and the redirect URI this prints.
    """
    service = GitLabService()
    if ctx.dry_run:
        ctx.logger.info(f"Would record the OAuth application of {host}")
        return
    secret = _secret(client_secret_stdin, SECRET_ENV, "The application's secret")
    application = service.save_oauth_application(host, client_id, secret)
    ctx.logger.success(f"Recorded the OAuth application of {application.host}")
    try:
        ctx.logger.key_value("Redirect URI", service.redirect_uri())
    except ValidationError as exc:
        ctx.logger.warning(f"{exc.message}. {exc.details}")


@oauth_app_group.command("remove")
@click.option("--host", required=True)
@pass_context
def oauth_app_remove_command(ctx: Context, host: str) -> None:
    """Forget the OAuth application of a host."""
    if ctx.dry_run:
        ctx.logger.info(f"Would forget the OAuth application of {host}")
        return
    removed = GitLabService().remove_oauth_application(host)
    ctx.logger.success("Removed") if removed else ctx.logger.info("There was none")
