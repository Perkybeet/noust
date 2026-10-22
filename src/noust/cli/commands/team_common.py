# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the ``noust team``, ``project``, ``environment`` and ``app move`` commands share.

The commands are a thin front end over :class:`~noust.core.teams.TeamStore`,
the store the console's team screens call, so a team made here is the one the
console shows and every rule (slugs, the last owner, what a team that still
holds resources may not do) is the same one. They run as root and are not held
to a team's scope, like every command: they are how an administrator arranges
the teams, and what they do is on the audit trail with whoever ran them.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any, TypeVar

import click

from noust.cli.app import Context
from noust.core.teams import Scope, TeamStore, ownership_target

_F = TypeVar("_F", bound=Callable[..., Any])


def teams() -> TeamStore:
    """
    Returns:
        The team store over this server's store.
    """
    return TeamStore()


def record(event: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """
    Put a change on the audit trail, as whoever runs the command.

    Args:
        event: The catalog name, such as ``team.create``.
        target: What it was done to, such as ``team:acme``.
        detail: Context, never a credential.
    """
    from noust.core.audit import record as audit_record

    audit_record(event, target=target, details=detail)


def owner_options(command: _F) -> _F:
    """
    Add ``--team``, ``--project`` and ``--env`` to a list command.

    Args:
        command: A Click command function.

    Returns:
        The command, taking the three filters as keyword arguments
        ``team``, ``project`` and ``env``.
    """
    command = click.option(
        "--env",
        "env",
        default=None,
        metavar="ENVIRONMENT",
        help="Only what lives in the environments with this name.",
    )(command)
    command = click.option(
        "--project", default=None, help="Only what lives in the projects with this name."
    )(command)
    command = click.option("--team", default=None, help="Only this team's.")(command)
    return command


def say(ctx: Context, payload: dict[str, Any], message: str) -> None:
    """
    Report the result of a change, as text or as JSON.

    Args:
        ctx: The CLI context.
        payload: What ``--json`` prints.
        message: What the person reads.
    """
    if ctx.json_output:
        click.echo(json.dumps({**payload, "message": message}))
        return
    ctx.logger.success(message)


def placed_in_a_project(command: _F) -> _F:
    """
    Let a command that creates things say which project they belong to.

    Adds ``--team``, ``--project`` and ``--environment`` (``--env`` is taken
    by the variables of ``noust create``) and runs the command with what it
    creates owned by that project and environment. Left out, they go to the
    default team's default project, as they always did.

    Args:
        command: A Click command function; it never sees the three options.

    Returns:
        The command, taking them.
    """

    @functools.wraps(command)
    def run(*args: Any, team: str | None, project: str | None, environment: str | None, **kw: Any):
        if not (team or project or environment):
            return command(*args, **kw)
        target = TeamStore().resolve_target(
            Scope.everything(), team=team, project=project, environment=environment
        )
        with ownership_target(target.project_id, target.environment_id):
            return command(*args, **kw)

    wrapped: Any = run
    wrapped = click.option(
        "--environment", default=None, help="The environment it belongs to (default: production)."
    )(wrapped)
    wrapped = click.option(
        "--project", default=None, help="The project it belongs to (default: the team's default)."
    )(wrapped)
    wrapped = click.option(
        "--team", default=None, help="The team it belongs to (default: the default team)."
    )(wrapped)
    return wrapped  # type: ignore[no-any-return]
