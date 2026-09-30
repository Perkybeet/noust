# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust incident`` command group: freeze the evidence, lock the console, lift it.

A front end over :mod:`noust.core.ens.incident` (ENS op.exp.7.r2, op.exp.9):

- ``freeze`` takes the evidence package (audit log and its verification,
  journal excerpts, configuration without secrets, a store snapshot, running
  units, listening sockets, the console's sessions and tokens), hashes every
  file into a manifest whose hash goes to the audit log, and locks the console
  down: only the master token signs in until ``unfreeze``.
- ``unfreeze`` lifts the lockdown.
- ``status`` says whether the console is locked down, and since when.

``freeze`` and ``unfreeze`` need ``--reason``: the incident reference is what
ties the package, the lockdown and the audit events together.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.audit import cli_actor
from noust.core.ens import incident
from noust.core.exceptions import DependencyError, SecurityError


def _require_reason(ctx: Context, what: str) -> str:
    """
    Args:
        ctx: The CLI context.
        what: The command, for the message.

    Returns:
        The reason given with ``--reason``.

    Raises:
        click.UsageError: None was given.
    """
    if not ctx.reason:
        raise click.UsageError(
            f"'noust incident {what}' needs --reason \"<incident reference>\": it ties the "
            "evidence, the lockdown and the audit events together."
        )
    return ctx.reason


def _console_lists() -> tuple[Any, Any, Any]:
    """
    Reach the console's sessions and tokens, when its dependencies are installed.

    Returns:
        ``(sessions, tokens, revoke)`` callables, or three Nones.
    """
    from noust.cli.web_state import token_manager

    try:
        manager = token_manager()
    except (DependencyError, SecurityError, OSError):
        # No console here (or its state cannot be opened): the package goes
        # without its sessions, which the manifest's file list shows.
        return None, None, None

    def revoke() -> int:
        count = len(manager.list_sessions())
        manager.revoke_all_sessions()
        return count

    return manager.list_sessions, manager.list_api_tokens, revoke


@click.group("incident", cls=NoustGroup)
def cli() -> None:
    """Freeze the evidence of a security incident and lock the console down."""


@cli.command("freeze")
@click.option(
    "--no-lock",
    is_flag=True,
    help="Take the evidence only; leave the console open for sign-in.",
)
@click.option(
    "--revoke-sessions",
    is_flag=True,
    help="Also end every console session open now (after listing them in the package).",
)
@click.option(
    "--hours",
    type=click.IntRange(1, 24 * 90),
    default=24,
    show_default=True,
    help="How far back the journal excerpts go.",
)
@click.option(
    "--output",
    type=click.Path(file_okay=False, path_type=Path),
    help="Directory to create the package in (default: incidents/ beside the store).",
)
@json_option("Print the result as JSON.")
@pass_context
def freeze_command(
    ctx: Context, no_lock: bool, revoke_sessions: bool, hours: int, output: Path | None
) -> None:
    """
    Take an evidence package with a SHA-256 manifest and lock the console down.

    The package (owner-only) holds the audit log and its verification,
    journal excerpts, the configuration without secrets, a snapshot of the
    store, the running units and the listening sockets. The manifest's hash
    is written to the audit log, which is shipped off the machine. Until
    'noust incident unfreeze', only the master token signs in to the console.
    """
    reason = _require_reason(ctx, "freeze")
    if ctx.dry_run:
        ctx.logger.info(
            "would take an incident evidence package"
            + ("" if no_lock else " and lock the console down")
        )
        return
    sessions, tokens, revoke = _console_lists()
    result = incident.freeze(
        reason=reason,
        actor=cli_actor().label,
        lock=not no_lock,
        journal_hours=hours,
        output_root=output,
        sessions=sessions,
        tokens=tokens,
        revoke=revoke if revoke_sessions else None,
    )
    if ctx.json_output:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return
    logger = ctx.logger
    logger.success(f"Evidence package: {result.directory}")
    logger.key_value("Files", str(len(result.files)))
    logger.key_value("Manifest SHA-256", result.manifest_sha256)
    for problem in result.problems:
        logger.warning(problem)
    if result.locked:
        logger.warning(
            "The console is locked down: only the master token signs in. "
            'Lift it with: noust incident unfreeze --reason "..."'
        )
    if result.sessions_revoked is not None:
        logger.info(f"Ended {result.sessions_revoked} console session(s).")
    logger.info(
        f"Check the package any time with: cd {result.directory} && sha256sum -c "
        f"{incident.MANIFEST_SHA256}"
    )


@cli.command("unfreeze")
@json_option("Print the lockdown that was lifted as JSON.")
@pass_context
def unfreeze_command(ctx: Context) -> None:
    """Lift the console lockdown a freeze put in place."""
    reason = _require_reason(ctx, "unfreeze")
    if ctx.dry_run:
        ctx.logger.info("would lift the console lockdown")
        return
    lifted = incident.lift_lockdown(actor=cli_actor().label, reason=reason)
    if ctx.json_output:
        click.echo(json.dumps({"lifted": lifted}, indent=2))
        return
    if lifted is None:
        ctx.logger.info("The console was not locked down.")
        return
    ctx.logger.success(
        f"Lockdown lifted (it was in place since {lifted.get('since')}, by {lifted.get('by')})."
    )


@cli.command("status", read_only=True)
@json_option("Print the lockdown as JSON.")
@pass_context
def status_command(ctx: Context) -> None:
    """Say whether the console is locked down for an incident."""
    state = incident.lockdown_state()
    if ctx.json_output:
        click.echo(json.dumps({"locked": state is not None, "lockdown": state}, indent=2))
        return
    if state is None:
        ctx.logger.info("The console is not locked down.")
        return
    ctx.logger.warning("The console is locked down: only the master token signs in.")
    for key in ("since", "by", "reason", "package"):
        if state.get(key):
            ctx.logger.key_value(key.capitalize(), str(state[key]))
