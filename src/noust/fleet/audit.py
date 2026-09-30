# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Record fleet actions taken at a terminal in the same audit log the console writes.

Authorizing a central hands it a credential to this server; adding or
removing a node changes what a central can reach. Each is recorded next to
the console's own entries, so one log answers "who gave access to whom".

The entry goes through :mod:`noust.core.audit` like every other event, as the
operating system identity of whoever ran the command (the login uid the
kernel keeps through ``sudo``), linked to the command's own ``cli.command``
events by its correlation id.
"""

from __future__ import annotations

from noust.core import audit as audit_trail


def audit(
    action: str, result: str, *, resource: str | None = None, detail: str | None = None
) -> None:
    """
    Append one entry to the audit log, as the operator at this terminal.

    Never pass a credential in ``detail``.

    Args:
        action: Such as ``fleet.authorize``.
        result: ``success`` or ``failure``.
        resource: What was acted on, such as ``central:nas``.
        detail: One sentence of context.
    """
    audit_trail.get_log().append(
        action,
        actor=audit_trail.cli_actor(),
        target=resource,
        outcome=result,
        detail=detail,
    )
