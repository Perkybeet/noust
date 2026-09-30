# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ``noust approval`` command group: four-eyes requests, from the terminal.

A front end over :class:`~noust.core.accounts.approvals.ApprovalManager`, the
manager the console's guard and ``/api/approvals`` call. Root at the terminal
may decide a request: the CLI is the emergency channel, not held to the
console's roles, which is also why every decision taken here is on the audit
trail with the operating system identity of whoever ran it (the login uid the
kernel keeps through ``sudo``). That is how a server with a single security
officer, or none yet, still gets a second pair of eyes on a change.
"""

from __future__ import annotations

import json
from datetime import datetime

import click

from noust.cli.app import Context, NoustGroup, json_option, pass_context
from noust.core.accounts.approvals import (
    STATES,
    ApprovalActor,
    ApprovalManager,
    ApprovalRequest,
)


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
            "or with the ENS profile (security.profile: ens-medium)."
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
