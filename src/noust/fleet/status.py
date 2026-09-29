# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The fleet at a glance: every node's reachability, version, apps, units and certificates.

Each node is asked through its own API - the same endpoints its console reads
(``/api/system/version``, ``/api/system/machine``, ``/api/certs``) - so a
node's summary here can never disagree with what its own console shows. Nodes
are asked in parallel, a few at a time, so one slow or unreachable node costs
its own timeout, not the sum of everyone's.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from noust.core.exceptions import NodeError, NodeRefusedError
from noust.core.store import NodeRecord
from noust.fleet.nodes import VERSION_PATH, NodeManager, cli_actor

#: Nodes asked at once.
MAX_WORKERS = 4

#: Seconds each request to a node may take.
NODE_TIMEOUT = 20.0

#: A certificate this close to expiry is counted as expiring. The same
#: threshold as ``noust health``'s warning, so both say the same thing.
CERT_EXPIRING_DAYS = 30

MACHINE_PATH = "/api/system/machine"
CERTS_PATH = "/api/certs"


def _counts(value: Any, keys: tuple[str, ...]) -> dict[str, int] | None:
    """
    Read a block of counters from a node's machine snapshot.

    Args:
        value: The block, such as ``{"running": 3, "failed": 0, ...}``.
        keys: The counters to keep.

    Returns:
        The counters, or None when the block is not what a node sends.
    """
    if not isinstance(value, dict):
        return None
    counts: dict[str, int] = {}
    for key in keys:
        number = value.get(key)
        if not isinstance(number, int) or isinstance(number, bool):
            return None
        counts[key] = number
    return counts


def node_summary(
    manager: NodeManager, record: NodeRecord, actor: str | None = None
) -> dict[str, Any]:
    """
    Ask one node how it is, and record whether it answered.

    Args:
        manager: The node registry.
        record: The node.
        actor: Who asks, for the node's audit log; :func:`cli_actor` when None.

    Returns:
        ``name``, ``ssh``, ``status``, ``reachable``, ``version``,
        ``latency_ms``, ``apps`` (running/failed/stopped/static),
        ``units`` (running/failed/stopped), ``certificates_expiring``,
        ``error`` and ``details`` (ssh's or the node's own words, verbatim),
        and ``warnings`` for the parts that could not be read.
    """
    summary: dict[str, Any] = {
        "name": record.name,
        "ssh": f"{record.ssh_user}@{record.ssh_host}:{record.ssh_port}",
        "status": record.status,
        "reachable": False,
        "version": record.version,
        "last_seen": record.last_seen,
        "latency_ms": None,
        "apps": None,
        "units": None,
        "certificates_expiring": None,
        "error": None,
        "details": None,
        "warnings": [],
    }
    client = manager.client(record.name, timeout=NODE_TIMEOUT)
    actor = actor or cli_actor()
    started = time.monotonic()
    try:
        info = client.get_json(VERSION_PATH, actor=actor)
    except NodeRefusedError as exc:
        manager.store.set_node_status(record.name, "refused")
        summary.update(status="refused", error=exc.message, details=exc.details or None)
        return summary
    except NodeError as exc:
        manager.store.set_node_status(record.name, "unreachable")
        summary.update(status="unreachable", error=exc.message, details=exc.details or None)
        return summary
    summary["latency_ms"] = round((time.monotonic() - started) * 1000, 1)
    version = info.get("current_version") if isinstance(info, dict) else None
    if isinstance(version, str):
        summary["version"] = version
    manager.store.set_node_status(
        record.name, "reachable", version=version if isinstance(version, str) else None
    )
    summary.update(status="reachable", reachable=True)

    try:
        machine = client.get_json(MACHINE_PATH, actor=actor)
    except NodeError as exc:
        summary["warnings"].append(f"Machine snapshot: {exc.message}")
    else:
        if isinstance(machine, dict):
            summary["apps"] = _counts(
                machine.get("apps"), ("running", "failed", "stopped", "static")
            )
            summary["units"] = _counts(machine.get("units"), ("running", "failed", "stopped"))

    try:
        certs = client.get_json(CERTS_PATH, actor=actor)
    except NodeError as exc:
        summary["warnings"].append(f"Certificates: {exc.message}")
    else:
        entries = certs.get("certificates") if isinstance(certs, dict) else None
        if isinstance(entries, list):
            summary["certificates_expiring"] = sum(
                1
                for cert in entries
                if isinstance(cert, dict)
                and isinstance(cert.get("days_remaining"), int)
                and cert["days_remaining"] < CERT_EXPIRING_DAYS
            )
    return summary


def fleet_status(
    manager: NodeManager | None = None,
    *,
    max_workers: int = MAX_WORKERS,
    actor: str | None = None,
) -> list[dict[str, Any]]:
    """
    Summarise every node, asking them in parallel.

    Args:
        manager: The node registry; a default one when None.
        max_workers: Nodes asked at once.
        actor: Who asks, for the nodes' audit logs; :func:`cli_actor` when None.

    Returns:
        One :func:`node_summary` per node, by name.
    """
    manager = manager or NodeManager()
    records = manager.list()
    if not records:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(records)))) as pool:
        return list(pool.map(lambda record: node_summary(manager, record, actor), records))
