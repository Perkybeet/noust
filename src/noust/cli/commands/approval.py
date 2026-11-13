# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust approval`` command group: four-eyes requests, from the terminal, and the policy for what is
not a person.

A front end over :class:`~noust.core.accounts.approvals.ApprovalManager`, the
manager the console's guard and ``/api/approvals`` call. Root at the terminal
may decide a request: the CLI is the emergency channel, not held to the
console's roles, which is also why every decision taken here is on the audit
trail with the operating system identity of whoever ran it (the login uid the
kernel keeps through ``sudo``). That is how a server with a single security
officer, or none yet, still gets a second pair of eyes on a change.
"""

from __future__ import annotations

import builtins
import json
from datetime import datetime
from typing import Any

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.accounts.approvals import (
    STATES,
    ApprovalActor,
    ApprovalManager,
    ApprovalRequest,
)
from noust.core.exceptions import ValidationError


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


def _decider() -> ApprovalActor:
    """
    Returns:
        Whoever runs this command, as the operating system knows them.
    """
    from noust.core.audit import cli_actor

    return ApprovalActor.from_audit_actor(cli_actor())


def _show(ctx: Context, request: ApprovalRequest, message: str) -> None:
    """
    Report a request after a decision.

    Args:
        ctx: The CLI context.
        request: The request.
        message: What was done.
    """
    if ctx.json_output:
        click.echo(json.dumps({"approval": request.to_dict(), "message": message}))
        return
    ctx.logger.success(message)


@click.group("approval", cls=NoustGroup)
def cli() -> None:
    """Four-eyes requests: list them, and approve or reject them as root."""


@cli.command("list", read_only=True)
@click.option(
    "--state",
    type=click.Choice([*STATES, "all"]),
    default="requested",
    show_default=True,
    help="Which requests to list.",
)
@json_option("Print the requests as JSON.")
@pass_context
def list_command(ctx: Context, state: str) -> None:
    """List approval requests, those waiting for a decision by default."""
    manager = ApprovalManager()
    found = manager.list(state=None if state == "all" else state)
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "policy": manager.policy().to_dict(),
                    "approvals": [item.to_dict() for item in found],
                }
            )
        )
        return
    if not manager.policy().enabled:
        ctx.logger.info(
            "Approvals are off: turn them on with 'noust config set approval.enabled true', "
            "or with a framework that asks for it, such as ENS (noust compliance enable ens-media)."
        )
    if not found:
        ctx.logger.info("No approval requests" + ("" if state == "all" else f" {state}") + ".")
        return
    ctx.logger.table(
        ["ID", "State", "Action", "Call", "Requested by", "Reason", "Asked", "Expires"],
        [
            [
                str(item.id),
                item.state,
                item.action,
                f"{item.method} {item.path}",
                item.requester.name,
                (item.reason or "-")[:40],
                _fmt(item.created_at),
                _fmt(item.execute_by if item.state == "approved" else item.expires_at),
            ]
            for item in found
        ],
    )


@cli.command("show", read_only=True)
@click.argument("approval_id", type=int)
@json_option("Print the request as JSON.")
@pass_context
def show_command(ctx: Context, approval_id: int) -> None:
    """Show one request with the call it snapshots, secrets redacted."""
    request = ApprovalManager().get(approval_id)
    if ctx.json_output:
        click.echo(json.dumps(request.to_dict()))
        return
    logger = ctx.logger
    logger.info(f"Request {request.id}: {request.description}")
    logger.info(f"State: {request.state}")
    logger.info(f"Call: {request.method} {request.path}")
    logger.info(
        f"Requested by: {request.requester.name} ({request.requester.role or request.requester.kind})"
    )
    logger.info(f"Reason: {request.reason or '-'}")
    if request.decider is not None:
        logger.info(
            f"Decided by: {request.decider.name} on {_fmt(request.decided_at)}"
            + (f": {request.decision_comment}" if request.decision_comment else "")
        )
    logger.blank()
    click.echo(json.dumps(request.parameters, indent=2, ensure_ascii=False))


@cli.command("approve")
@click.argument("approval_id", type=int)
@click.option("--comment", default=None, help="What you say to the requester.")
@pass_context
def approve_command(ctx: Context, approval_id: int, comment: str | None) -> None:
    """Approve a request: its requester may make that exact call once."""
    request = ApprovalManager().approve(approval_id, _decider(), comment)
    minutes = int(ApprovalManager().policy().execute_minutes)
    _show(
        ctx,
        request,
        f"Approved request {request.id} ({request.method} {request.path}) of "
        f"{request.requester.name}; they have {minutes} minutes to run it.",
    )


@cli.command("reject")
@click.argument("approval_id", type=int)
@click.option("--comment", default=None, help="Why, shown to the requester.")
@pass_context
def reject_command(ctx: Context, approval_id: int, comment: str | None) -> None:
    """Reject a request, or withdraw an approval that was not used yet."""
    request = ApprovalManager().reject(approval_id, _decider(), comment)
    _show(ctx, request, f"Rejected request {request.id} of {request.requester.name}.")


# ------------------------------------------------------ the policy for who is not a person


def _cells_of(
    caller: str, environment: str, permission: str
) -> builtins.list[tuple[str, str, str]]:
    """
    Args:
        caller: ``token`` or ``assistant``.
        environment: ``production``, ``other`` or ``both``.
        permission: A permission that changes something.

    Returns:
        The cells the command names: one per environment asked for.
    """
    from noust.core.caller_policy import ENVIRONMENTS

    chosen = ENVIRONMENTS if environment == "both" else (environment,)
    return [(caller, name, permission) for name in chosen]


def _value_word(cell: Any) -> str:
    """
    Args:
        cell: A cell of the policy.

    Returns:
        Its value, starred when the operator chose it, and marked when the floor fixes it.
    """
    word = str(cell.value) + ("*" if cell.chosen is not None else "")
    return word + (" (fixed)" if cell.fixed else "")


@cli.group("policy", cls=NoustGroup)
def policy_group() -> None:
    """What a token and the built-in assistant may do alone, propose or never."""


@policy_group.command("show", read_only=True)
@click.option(
    "--caller",
    type=click.Choice(["token", "assistant"]),
    default=None,
    help="Only this class of caller.",
)
@click.option("--changed", is_flag=True, help="Only the cells somebody chose.")
@json_option("Print the table as JSON.")
@pass_context
def policy_show(ctx: Context, caller: str | None, changed: bool) -> None:
    """
    Show the policy: for each permission that changes something, in production and elsewhere.

    ``alone`` does it, ``propose`` waits for a person to approve it in the console, ``never`` refuses it.
    A value followed by * was chosen by somebody; the rest is what this release ships.
    """
    from noust.core.caller_policy import CallerPolicy

    policy = CallerPolicy()
    rows = policy.rows()
    if ctx.json_output:
        click.echo(
            json.dumps(
                {
                    "token_production_approval": policy.floor_on,
                    "cells": [
                        {
                            "caller": cell.caller,
                            "environment": cell.environment,
                            "permission": cell.permission,
                            "value": cell.value,
                            "default": cell.default,
                            "chosen": cell.chosen,
                            "offered": list(cell.offered),
                            "fixed": cell.fixed,
                            "reason": cell.reason,
                        }
                        for row in rows
                        for cell in row.cells
                        if (caller is None or cell.caller == caller)
                        and (not changed or cell.chosen is not None)
                    ],
                }
            )
        )
        return
    lines = []
    for row in rows:
        for who in ("token", "assistant") if caller is None else (caller,):
            cells = {c.environment: c for c in row.cells if c.caller == who}
            if changed and all(c.chosen is None for c in cells.values()):
                continue
            lines.append(
                [
                    row.permission,
                    who,
                    _value_word(cells["production"]),
                    _value_word(cells["other"]),
                ]
            )
    if not lines:
        ctx.logger.info("Nobody chose a cell: this server keeps what the release ships.")
        return
    ctx.logger.table(["Permission", "Caller", "Production", "Other"], lines)
    if policy.floor_on:
        ctx.logger.info(
            "The security profile of this server asks that what a token changes in production "
            "waits for a person, so a token's cells there are propose or never."
        )


@policy_group.command("set")
@click.argument("permission")
@click.argument("caller", type=click.Choice(["token", "assistant"]))
@click.argument("environment", type=click.Choice(["production", "other", "both"]))
@click.argument("value", type=click.Choice(["alone", "propose", "never"]))
@pass_context
def policy_set(ctx: Context, permission: str, caller: str, environment: str, value: str) -> None:
    """
    Give a class of caller a value for a permission that changes something, where it applies.

    ``noust approvals policy set apps.deploy assistant production propose``. A cell the policy cannot allow
    says why and which values it may have: what the built-in assistant never does is fixed, and where most
    of what a permission does asks you to confirm it is you, ``alone`` is not offered. Root at the terminal
    is not asked for a second person: the change is on the audit trail with the operating system identity of
    whoever made it.
    """
    from noust.core.audit import record
    from noust.core.caller_policy import CallerPolicy

    policy = CallerPolicy()
    cells = [(c, e, p, value) for c, e, p in _cells_of(caller, environment, permission)]
    try:
        written = policy.set_cells(cells, by=_decider().name)
    except ValidationError as exc:
        raise click.ClickException(f"{exc.message}. {exc.details}".strip()) from exc
    record(
        "approval.policy",
        target="approval-policy",
        details={
            "cells": [
                {
                    "caller": c.caller,
                    "environment": c.environment,
                    "permission": c.permission,
                    "value": c.value,
                }
                for c in written
            ],
            "reset": 0,
        },
    )
    ctx.logger.success(
        f"{permission} is {value} for {caller} in "
        + ("production and every other environment" if environment == "both" else environment)
        + "."
    )


@policy_group.command("reset")
@click.argument("permission")
@click.argument("caller", type=click.Choice(["token", "assistant"]))
@click.argument("environment", type=click.Choice(["production", "other", "both"]))
@pass_context
def policy_reset(ctx: Context, permission: str, caller: str, environment: str) -> None:
    """Put a permission back to what this release ships for a class of caller, where it applies."""
    from noust.core.audit import record
    from noust.core.caller_policy import CallerPolicy

    try:
        removed = CallerPolicy().reset_cells(_cells_of(caller, environment, permission))
    except ValidationError as exc:
        raise click.ClickException(f"{exc.message}. {exc.details}".strip()) from exc
    record(
        "approval.policy",
        target="approval-policy",
        details={"cells": [], "reset": removed},
    )
    ctx.logger.success(
        f"{permission} is back to what the release ships for {caller}"
        if removed
        else f"Nobody had chosen {permission} for {caller}: nothing to put back."
    )
