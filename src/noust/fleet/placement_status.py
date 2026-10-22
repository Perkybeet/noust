# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application placed on several servers looks like right now.

The central asks each member for its applications (the fleet's ``apps`` view, so the answers
are cached, bounded in time and shown with their age like every other fleet view) and compares
them: which commit each one serves, which one most of them agree on (the reference), and who is
not there, not answering or on another commit.

It reads and changes nothing. Whether a deployment is "behind" is not known from here (commits
have no order the central can see without a clone), so a server on another commit is "differs",
and the operator decides which way to move it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from noust.fleet.aggregate import Asker, gather
from noust.fleet.placements import Placement, Placements

if TYPE_CHECKING:
    from noust.fleet.nodes import NodeManager

__all__ = ["HEALTHY_STATES", "placement_status"]

#: What an application's state is when it is serving.
HEALTHY_STATES = frozenset({"running", "static"})


def _last_deployment(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """
    Read an application's newest deployment out of a row of the apps view.

    Args:
        row: The row.

    Returns:
        The deployment, empty when the application has none.
    """
    last = row.get("last_deployment")
    return last if isinstance(last, dict) else {}


def _reference(commits: Mapping[str, tuple[str, str]]) -> str | None:
    """
    Choose the commit the placement is measured against.

    The one most servers serve; on a tie, the one deployed last (the operator has
    just rolled it out to some of them), and then the smaller, so the answer is the
    same every time it is asked.

    Args:
        commits: Server name to its commit and when it was deployed.

    Returns:
        The commit, or None when no server reports one.
    """
    if not commits:
        return None
    counts = Counter(commit for commit, _ in commits.values())
    newest: dict[str, str] = {}
    for commit, finished in commits.values():
        newest[commit] = max(newest.get(commit, ""), finished)
    # Stable sorts, least important key first: the smaller commit, then the newest, then the most.
    ordered = sorted(counts)
    ordered.sort(key=lambda commit: newest[commit], reverse=True)
    ordered.sort(key=lambda commit: counts[commit], reverse=True)
    return ordered[0]


def placement_status(
    placement: Placement,
    *,
    manager: NodeManager,
    asker: Asker,
    refresh: bool = False,
) -> dict[str, Any]:
    """
    Compare an application across the servers it runs on.

    Args:
        placement: The placement.
        manager: The node registry.
        asker: Who asks; each server answers for them.
        refresh: Ask even the servers whose last answer is fresh.

    Returns:
        ``domain``, ``members`` (the servers' names), ``reference_commit``, ``in_sync``
        (every member there, answering, running and on the reference) and ``servers``: one
        row per member with ``node``, ``reachable``, ``present``, ``status``, ``commit``,
        ``deployed_at``, ``branch``, ``layout``, ``sync`` (``current``, ``differs``,
        ``unknown``, ``missing`` or ``unreachable``), ``stale``, ``message`` and ``href``.
    """
    members = Placements(manager.store).members(placement, manager.list())
    view = (
        gather("apps", asker=asker, nodes=members, manager=manager, refresh=refresh)
        if members
        else None
    )
    present: dict[str, dict[str, Any]] = {}
    if view is not None:
        for item in view.items:
            if str(item.get("domain") or item.get("name") or "") == placement.domain:
                present[str(item["node"])] = item
    servers: list[dict[str, Any]] = []
    commits: dict[str, tuple[str, str]] = {}
    for name in members:
        outcome = view.outcome(name) if view is not None else None
        reachable = outcome is not None and outcome.status in ("ok", "stale")
        row = present.get(name)
        last = _last_deployment(row) if row else {}
        commit = last.get("git_commit") if isinstance(last.get("git_commit"), str) else None
        deployed_at = last.get("finished_at") if isinstance(last.get("finished_at"), str) else None
        if reachable and row is not None and commit:
            commits[name] = (commit, deployed_at or "")
        message = None
        if not reachable:
            message = (outcome.message if outcome else None) or f"{name} did not answer"
        elif row is None:
            message = (
                f"{placement.domain} is not installed on {name}: "
                f"noust fleet placement copy {placement.domain} --from <server> --to {name}"
            )
        servers.append(
            {
                "node": name,
                "reachable": reachable,
                "present": row is not None,
                "status": str(row.get("status")) if row and row.get("status") else None,
                "commit": commit,
                "deployed_at": deployed_at,
                "branch": row.get("branch") if row else None,
                "layout": row.get("layout") if row else None,
                "sync": "unreachable"
                if not reachable
                else ("missing" if row is None else "unknown"),
                "stale": bool(outcome and outcome.status == "stale"),
                "message": message,
                "href": (f"/n/{quote(name, safe='')}/apps/{quote(placement.domain, safe='')}"),
            }
        )
    reference = _reference(commits)
    for server in servers:
        if server["sync"] == "unknown" and server["commit"]:
            server["sync"] = "current" if server["commit"] == reference else "differs"
    in_sync = bool(servers) and all(
        server["sync"] == "current" and server["status"] in HEALTHY_STATES for server in servers
    )
    return {
        "domain": placement.domain,
        "members": members,
        "reference_commit": reference,
        "in_sync": in_sync,
        "servers": servers,
    }
