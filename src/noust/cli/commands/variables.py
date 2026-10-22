# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust variables`` command group: variables shared by a scope.

A variable defined once for the server, a team, a project or an environment and
used by reference from the variables of an application:

    noust variables set project DATABASE_URL --team acme --project shop --stdin
    noust env set shop.acme.com 'DATABASE_URL={{project.DATABASE_URL}}'

It is never given to an application that does not name it, so a team's database
password does not reach every application of the team just because they share a
project. ``noust env`` is an application's own variables.
"""

from __future__ import annotations

import json
import sys

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.cli.commands.team_common import record, say, teams
from noust.core.config import REDACTED
from noust.core.exceptions import ValidationError
from noust.core.secret_detection import classify
from noust.core.teams import DEFAULT_SLUG
from noust.core.variables.model import (
    AUDIENCES,
    BOTH,
    PROJECT,
    REFERENCE_SCOPES,
    SERVER,
    TEAM,
)
from noust.core.variables.store import VariableStore
from noust.deployers.helpers.env_manager import redact_url_credentials

_SCOPE_ARGUMENT = click.argument("scope", type=click.Choice(REFERENCE_SCOPES))


def _owner(
    scope: str, team: str | None, project: str | None, environment: str | None
) -> tuple[int, str]:
    """
    Find whose variables a command is about.

    Args:
        scope: ``server``, ``team``, ``project`` or ``environment``.
        team: The team's slug (default: the default team).
        project: The project's slug (default: the default project).
        environment: The environment's slug (default: production).

    Returns:
        The owner's id (0 for the server) and a label for messages.

    Raises:
        TeamNotFoundError: Nothing has that name.
    """
    if scope == SERVER:
        return 0, "the server"
    store = teams()
    chosen_team = team or DEFAULT_SLUG
    if scope == TEAM:
        found = store.require_team(chosen_team)
        return found.id, f"team {found.slug}"
    chosen_project = project or DEFAULT_SLUG
    if scope == PROJECT:
        owner = store.require_project(chosen_team, chosen_project)
        return owner.id, f"project {owner.team}/{owner.slug}"
    stage = store.require_environment(chosen_team, chosen_project, environment or "production")
    return stage.id, f"environment {chosen_team}/{chosen_project}/{stage.slug}"


def _where(command):
    """Add the options that say which team, project and environment."""
    for option in (
        click.option("--environment", "environment", default=None, help="The environment."),
        click.option("--project", "project", default=None, help="The project."),
        click.option("--team", "team", default=None, help="The team."),
    ):
        command = option(command)
    return command


@click.group("variables", cls=NoustGroup)
def cli() -> None:
    """Variables shared by the server, a team, a project or an environment."""


@cli.command("list", read_only=True)
@_SCOPE_ARGUMENT
@_where
@click.option("--unmask", is_flag=True, default=False, help="Print secret values in clear.")
@json_option("Print the variables as JSON.")
@pass_context
def list_command(
    ctx: Context,
    scope: str,
    team: str | None,
    project: str | None,
    environment: str | None,
    unmask: bool,
) -> None:
    """List the variables of a scope, secrets hidden."""
    owner, label = _owner(scope, team, project, environment)
    variables = VariableStore().list(scope, owner)
    marks = {v.name: v.secret for v in variables if v.secret is not None}
    rows = []
    for variable in variables:
        verdict = classify(variable.name, variable.value, marks)
        shown = variable.value
        if verdict.secret and not unmask:
            shown = (
                redact_url_credentials(variable.value)
                if verdict.reason == "url credentials"
                else REDACTED
            )
        rows.append({"name": variable.name, "value": shown, "audience": variable.audience})
    if ctx.json_output:
        click.echo(json.dumps({"scope": scope, "label": label, "variables": rows}))
        return
    if not rows:
        ctx.logger.info(f"{label} has no shared variables")
        return
    ctx.logger.table(
        ["Name", "Value", "Audience"], [[r["name"], r["value"], r["audience"]] for r in rows]
    )
    ctx.logger.info(f"An application uses one with {{{{{scope}.NAME}}}} in its own variables.")


@cli.command("set")
@_SCOPE_ARGUMENT
@click.argument("name")
@click.argument("value", required=False)
@_where
@click.option(
    "--stdin", "from_stdin", is_flag=True, help="Read the value from standard input (not argv)."
)
@click.option("--secret/--not-secret", "secret", default=None, help="Say whether it is a secret.")
@click.option(
    "--audience",
    type=click.Choice(AUDIENCES),
    default=BOTH,
    show_default=True,
    help="Which part of a deployment an application that uses it hands it to.",
)
@pass_context
def set_command(
    ctx: Context,
    scope: str,
    name: str,
    value: str | None,
    team: str | None,
    project: str | None,
    environment: str | None,
    from_stdin: bool,
    secret: bool | None,
    audience: str,
) -> None:
    """
    Create or change a shared variable.

    The value may hold references to other scopes. Pass a secret with --stdin:
    everything on a command line is visible to every user of this machine.
    """
    if from_stdin == (value is not None):
        raise click.UsageError("Give the value as an argument, or with --stdin, not both.")
    text = sys.stdin.read().rstrip("\n") if from_stdin else (value or "")
    owner, label = _owner(scope, team, project, environment)
    VariableStore().set(scope, owner, name, text, secret=secret, audience=audience, actor="cli")
    record("variables.set", f"variables:{scope}:{label}", {"variables": [name]})
    say(ctx, {"scope": scope, "name": name}, f"{name} is set for {label}")


@cli.command("unset")
@_SCOPE_ARGUMENT
@click.argument("names", nargs=-1, required=True)
@_where
@pass_context
def unset_command(
    ctx: Context,
    scope: str,
    names: tuple[str, ...],
    team: str | None,
    project: str | None,
    environment: str | None,
) -> None:
    """Remove shared variables."""
    owner, label = _owner(scope, team, project, environment)
    store = VariableStore()
    removed = [name for name in names if store.delete(scope, owner, name)]
    missing = sorted(set(names) - set(removed))
    if missing:
        raise ValidationError(f"{label} has no variable {', '.join(missing)}")
    record("variables.delete", f"variables:{scope}:{label}", {"variables": removed})
    for name in removed:
        users = store.referencing_apps(scope, owner, name)
        if users:
            ctx.logger.warning(
                f"{name} is still named by {', '.join(users)}: their next deploy fails "
                "until the reference is changed"
            )
    say(ctx, {"scope": scope, "removed": removed}, f"Removed {', '.join(removed)} from {label}")


@cli.command("used-by", read_only=True)
@_SCOPE_ARGUMENT
@click.argument("name")
@_where
@json_option("Print the applications as JSON.")
@pass_context
def used_by_command(
    ctx: Context,
    scope: str,
    name: str,
    team: str | None,
    project: str | None,
    environment: str | None,
) -> None:
    """List the applications whose variables refer to a shared variable."""
    owner, label = _owner(scope, team, project, environment)
    users = VariableStore().referencing_apps(scope, owner, name)
    if ctx.json_output:
        click.echo(json.dumps({"name": name, "applications": users}))
        return
    if not users:
        ctx.logger.info(f"No application refers to {name} of {label}")
        return
    ctx.logger.table(["Application"], [[domain] for domain in users])
