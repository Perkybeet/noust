# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet at a glance: every node's reachability, version, apps, units and certificates.

``noust fleet status`` is the ``summary`` view of the fleet aggregator
(:mod:`noust.fleet.aggregate`), the same one ``/api/fleet/summary`` answers,
shaped the way the command has always printed it. There is no second summary
here: each node is asked through its own API, in parallel, by the aggregator.
"""

from __future__ import annotations

from typing import Any

from noust.fleet.aggregate import Asker, NodeOutcome, gather
from noust.fleet.nodes import NodeManager, cli_actor

#: Seconds each request to a node may take: the terminal waits for everyone.
NODE_TIMEOUT = 20.0


def _summary(row: dict[str, Any], outcome: NodeOutcome | None) -> dict[str, Any]:
    """
    Shape one node's summary row as ``noust fleet status`` prints it.

    Args:
        row: The node's ``summary`` row.
        outcome: How it answered.

    Returns:
        ``name``, ``ssh``, ``status``, ``reachable``, ``version``,
        ``last_seen``, ``latency_ms``, ``apps``, ``units``,
        ``certificates_expiring``, ``error``, ``details`` and ``warnings``.
    """
    status = row.get("reachability") or "unknown"
    # The node's words only when it could not be used at all; a part that
    # could not be read is a warning beside what could.
    failed = status != "reachable" and outcome is not None
    return {
        "name": row["node"],
        "ssh": row.get("ssh"),
        "status": status,
        "reachable": status == "reachable",
        "version": row.get("version"),
        "last_seen": row.get("last_seen"),
        "latency_ms": row.get("latency_ms") if status == "reachable" else None,
        "apps": row.get("apps"),
        "units": row.get("units"),
        "certificates_expiring": row.get("certificates_expiring"),
        "error": outcome.message if failed and outcome else None,
        "details": (outcome.error_verbatim or outcome.hint) if failed and outcome else None,
        "warnings": list(outcome.warnings) if outcome else [],
    }


def fleet_status(
    manager: NodeManager | None = None,
    *,
    actor: str | None = None,
) -> list[dict[str, Any]]:
    """
    Summarise every node, asking them in parallel.

    Args:
        manager: The node registry; a default one when None.
        actor: Who asks, for the nodes' audit logs; :func:`cli_actor` when None.

    Returns:
        One summary per node, by name.
    """
    manager = manager or NodeManager()
    # A status poll has no human decision behind it: read is what it asks for,
    # so a node that narrowed its central to reading still answers it.
    result = gather(
        "summary",
        asker=Asker(actor=actor or cli_actor(), scope="read"),
        manager=manager,
        refresh=True,
        node_timeout=NODE_TIMEOUT,
        deadline=None,
    )
    return [
        _summary(row, result.outcome(row["node"])) for row in result.items if not row.get("local")
    ]
