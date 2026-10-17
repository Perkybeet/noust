# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the ``noust auth`` command groups have in common: one way to record a change, to read the
group mapping options and to say what a mapping means, whichever identity source they configure.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import datetime
from typing import Any, TypeVar

import click

from noust.core.accounts.oidc_model import GroupMapping, mapping_from_line
from noust.core.exceptions import ValidationError

F = TypeVar("F", bound=Callable[..., Any])


def record_audit(event: str, target: str, detail: dict[str, Any] | None = None) -> None:
    """
    Put a change on the audit trail, as whoever runs this command.

    Args:
        event: The catalog name.
        target: What it was done to.
        detail: Context, never a credential.
    """
    from noust.core.audit import record

    record(event, target=target, details=detail)


def format_time(timestamp: float | None) -> str:
    """
    Args:
        timestamp: UNIX seconds, or None.

    Returns:
        Local time for a table cell, or ``-``.
    """
    if timestamp is None:
        return "-"
    return datetime.fromtimestamp(timestamp).isoformat(sep=" ", timespec="seconds")


def either_flag(given: tuple[bool, bool], what: str) -> bool | None:
    """
    Args:
        given: Whether the "on" flag and the "off" flag were passed.
        what: The setting, for the message.

    Returns:
        True, False, or None when neither was passed.

    Raises:
        ValidationError: When both were.
    """
    on, off = given
    if on and off:
        raise ValidationError(f"Pass only one of the two {what} flags")
    return True if on else False if off else None


def mappings_from_options(
    roles: tuple[str, ...], teams: tuple[str, ...], bindings: tuple[str, ...] = ()
) -> list[dict[str, str]]:
    """
    Read the ``--map-role``, ``--map-team`` and ``--map-binding`` options.

    Args:
        roles: ``GROUP=ROLE`` each.
        teams: ``GROUP=TEAM:ROLE`` each.
        bindings: ``GROUP=ROLE@TEAM[/PROJECT[/ENVIRONMENT]]`` each.

    Returns:
        The mapping, roles first (the first role mapping that matches decides).

    Raises:
        ValidationError: When one is not of its shape.
    """
    found: list[dict[str, str]] = []
    for text in roles:
        line = mapping_from_line(text)
        if "role" not in line:
            raise ValidationError(
                f"Not GROUP=ROLE: {text!r}", details="Such as --map-role noust-admins=admin."
            )
        found.append(line)
    for text in teams:
        line = mapping_from_line(text if ":" in text else f"{text}:viewer")
        if "team_role" not in line:
            raise ValidationError(
                f"Not GROUP=TEAM:ROLE: {text!r}",
                details="Such as --map-team acme-ops=acme:operator.",
            )
        found.append(line)
    for text in bindings:
        line = mapping_from_line(text)
        if "bind" not in line:
            raise ValidationError(
                f"Not GROUP=ROLE@TEAM/PROJECT: {text!r}",
                details="Such as --map-binding acme-release=release-manager@acme/web.",
            )
        found.append(line)
    return found


def describe_mapping(mapping: GroupMapping) -> str:
    """
    Args:
        mapping: One line of a group mapping.

    Returns:
        What the group means, for ``show``.
    """
    if mapping.role:
        return f"role {mapping.role}"
    if mapping.bind:
        where = "/".join(p for p in (mapping.team, mapping.project, mapping.environment) if p)
        return f"role {mapping.bind} on {where}"
    return f"team {mapping.team} as {mapping.team_role}"


def read_secret(what: str, name: str, from_stdin: bool, prompt: bool, example: str) -> str | None:
    """
    Read a secret without it ever touching the command line, which every local user can read in
    ``ps`` and which lands in shell history.

    Args:
        what: What it is, for the prompt and the message ("client secret", "bind password").
        name: The source it is for.
        from_stdin: Read one line from standard input.
        prompt: Ask at a hidden prompt.
        example: The command line to show when standard input is empty.

    Returns:
        The secret, or None when neither was asked for.

    Raises:
        ValidationError: When standard input is empty.
    """
    if from_stdin:
        line = sys.stdin.readline().rstrip("\r\n")
        if not line:
            raise ValidationError(
                f"No {what} on standard input",
                details=f"Pipe it in, such as: printf '%s\\n' \"$SECRET\" | {example}",
            )
        return line
    if prompt:
        return str(click.prompt(f"{what.capitalize()} for {name}", hide_input=True, type=str))
    return None


def group_options(command: F) -> F:
    """
    Add the options every identity source takes for what its groups mean.

    Args:
        command: A click command function.

    Returns:
        The command with ``--group-sync``, ``--no-group-sync``, ``--map-role``,
        ``--map-team``, ``--map-binding`` and ``--clear-mappings``, which
        :func:`group_changes` reads.
    """
    options = [
        click.option(
            "--group-sync",
            "sync_on",
            is_flag=True,
            help="Let groups decide roles and teams.",
        ),
        click.option(
            "--no-group-sync", "sync_off", is_flag=True, help="Stop letting groups decide."
        ),
        click.option(
            "--map-role",
            "map_roles",
            multiple=True,
            help="GROUP=ROLE: members get this role (repeat).",
        ),
        click.option(
            "--map-team",
            "map_teams",
            multiple=True,
            help="GROUP=TEAM:ROLE: members join this team (repeat).",
        ),
        click.option(
            "--map-binding",
            "map_bindings",
            multiple=True,
            help=(
                "GROUP=ROLE@TEAM[/PROJECT[/ENVIRONMENT]]: members hold this role there, a "
                "built-in one on a project or a custom one anywhere (repeat)."
            ),
        ),
        click.option("--clear-mappings", is_flag=True, help="Forget every group mapping."),
    ]
    for option in reversed(options):
        command = option(command)
    return command


def group_changes(
    sync_on: bool,
    sync_off: bool,
    map_roles: tuple[str, ...],
    map_teams: tuple[str, ...],
    map_bindings: tuple[str, ...],
    clear_mappings: bool,
) -> dict[str, Any]:
    """
    Read the options :func:`group_options` added.

    Args:
        sync_on: ``--group-sync`` was given.
        sync_off: ``--no-group-sync`` was given.
        map_roles: ``--map-role`` values.
        map_teams: ``--map-team`` values.
        map_bindings: ``--map-binding`` values.
        clear_mappings: ``--clear-mappings`` was given.

    Returns:
        The settings to change: ``group_sync`` and ``group_mappings``, when asked for.

    Raises:
        ValidationError: When a flag is given both ways or a mapping is not of its shape.
    """
    changes: dict[str, Any] = {}
    sync = either_flag((sync_on, sync_off), "--group-sync/--no-group-sync")
    if sync is not None:
        changes["group_sync"] = sync
    if map_roles or map_teams or map_bindings or clear_mappings:
        changes["group_mappings"] = mappings_from_options(map_roles, map_teams, map_bindings)
    return changes
