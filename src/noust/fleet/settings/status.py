# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where each server stands on each setting of the fleet, for the screen that shows it.

Every server is asked what it manages (``GET /api/managed-settings``, through the aggregator, so
the answers are cached for a few seconds, parallel and partial like every other fleet view) and
the answer is compared with what the central wants. Two facts are told apart, because the screen
draws them apart:

- **Where the value comes from** (``origin``): ``fleet`` (the server follows the fleet's value),
  ``central_exception`` (a person set a value for this server on the central), ``local_exception``
  (the server's own operator changed it, and the server rules on its own machine) or
  ``independent`` (the server is left out of the fleet's settings).
- **Whether the server holds it** (``sync``): ``in_sync``, ``pending`` (not yet, or an older
  version), ``diverged`` (its operator changed it), ``release_pending`` (it is no longer the
  fleet's and the server still treats it as such), ``not_applicable``, or why the server could not
  be read: ``unreachable``, ``unsupported`` (an older Noust), ``refused`` or ``unknown``.

Nothing secret is read from a server: the state is the version it applied and what it says of it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from noust.fleet.aggregate import (
    RESOURCES,
    Asker,
    FleetResult,
    NodeView,
    Part,
    Resource,
    gather,
)
from noust.fleet.settings.desired import DesiredSetting, FleetSettings
from noust.fleet.settings.routes import MANAGED_PREFIX

if TYPE_CHECKING:
    from noust.fleet.nodes import NodeManager

__all__ = ["ORIGINS", "SYNC_STATES", "compose", "read"]

#: Where a server's value comes from.
ORIGINS = ("fleet", "central_exception", "local_exception", "independent", "none")

#: Whether a server holds it.
SYNC_STATES = (
    "in_sync",
    "pending",
    "diverged",
    "release_pending",
    "not_applicable",
    "unreachable",
    "unsupported",
    "refused",
    "unknown",
)

#: How a server that could not be read stands, by how it answered.
_UNREADABLE = {
    "unreachable": "unreachable",
    "stale": "unreachable",
    "unsupported": "unsupported",
    "forbidden": "refused",
    "error": "unknown",
}


def _build(view: NodeView) -> list[dict[str, Any]]:
    """
    One row per setting a server says it manages.

    Args:
        view: The server.

    Returns:
        The rows, each labelled with the server.
    """
    answer = view.parts.get("managed")
    items = answer.get("items") if isinstance(answer, dict) else None
    return [
        {**item, "node": view.name}
        for item in items or []
        if isinstance(item, dict) and "resource" in item and "name" in item
    ]


#: What each server manages, as a fleet view. Not cached for long and never served stale: the
#: screen that shows it must not say a server is in sync because it was a minute ago.
MANAGED_VIEW = Resource(
    "managed_settings",
    (Part("managed", MANAGED_PREFIX, title="Managed settings"),),
    ttl=5.0,
    max_stale=0.0,
    build=_build,
)


def _register() -> None:
    """Add the view to the aggregator's, once."""
    RESOURCES.setdefault(MANAGED_VIEW.name, MANAGED_VIEW)


def _cell(
    node: str,
    wanted: DesiredSetting | None,
    row: Mapping[str, Any] | None,
    *,
    mode: str,
    status: str | None,
) -> dict[str, Any]:
    """
    Say where one server stands on one setting.

    Args:
        node: The server.
        wanted: What the central wants of it: its exception, else the fleet's; None for none.
        row: What the server says it manages of it.
        mode: ``managed`` or ``independent``.
        status: How the server answered, when it was asked.

    Returns:
        The cell.
    """
    origin = "none"
    if wanted is not None:
        origin = "central_exception" if wanted.node else "fleet"
    sync = "not_applicable"
    if mode == "independent":
        origin = "independent"
        sync = "release_pending" if row is not None else "not_applicable"
    elif wanted is None:
        sync = "release_pending" if row is not None else "not_applicable"
    elif status != "ok":
        sync = _UNREADABLE.get(status or "", "unknown")
    elif row is None:
        sync = "pending"
    elif row.get("state") == "overridden":
        origin, sync = "local_exception", "diverged"
    elif row.get("version") == wanted.version:
        sync = "in_sync"
    else:
        sync = "pending"
    held = row if status == "ok" and row is not None else None
    return {
        "node": node,
        "origin": origin,
        "sync": sync,
        "wanted_version": wanted.version if wanted is not None and mode != "independent" else None,
        "applied_version": held.get("version") if held is not None else None,
        "state": held.get("state") if held is not None else None,
        "applied_by": held.get("applied_by") if held is not None else None,
        "applied_at": held.get("applied_at") if held is not None else None,
        "change": held.get("change") if held is not None else None,
        "restore_requested": bool(held.get("restore_requested_at")) if held is not None else False,
    }


def compose(
    settings: FleetSettings, result: FleetResult, *, nodes: Sequence[str] | None = None
) -> dict[str, Any]:
    """
    Put what the central wants beside what each server says.

    Args:
        settings: What the central wants.
        result: What each server said it manages (the ``managed_settings`` view).
        nodes: The servers, in order; every registered one by default.

    Returns:
        ``servers`` (how each answered, its mode and whether a look is waiting), ``settings``
        (each with its fleet row, its exceptions and a cell per server), ``released`` (what some
        server still manages that the central no longer wants), ``generated_at`` and ``partial``.
    """
    names = list(nodes) if nodes is not None else [n.name for n in settings.store.list_nodes()]
    outcomes = {outcome.name: outcome for outcome in result.nodes}
    modes = settings.modes()
    waiting = set(settings.pending_resync())
    rows: dict[str, dict[tuple[str, str], Mapping[str, Any]]] = {}
    for item in result.items:
        rows.setdefault(str(item["node"]), {})[(str(item["resource"]), str(item["name"]))] = item

    fleet: dict[tuple[str, str], DesiredSetting] = {}
    exceptions: dict[tuple[str, str], dict[str, DesiredSetting]] = {}
    for setting in settings.list():
        key = (setting.resource, setting.name)
        if setting.node is None:
            fleet[key] = setting
        else:
            exceptions.setdefault(key, {})[setting.node] = setting

    entries = []
    for key in sorted(set(fleet) | set(exceptions)):
        cells = []
        for node in names:
            wanted = exceptions.get(key, {}).get(node) or fleet.get(key)
            status = outcomes[node].status if node in outcomes else None
            cells.append(
                _cell(
                    node,
                    wanted,
                    rows.get(node, {}).get(key),
                    mode=modes.get(node, "managed"),
                    status=status,
                )
            )
        row = fleet.get(key)
        entries.append(
            {
                "resource": key[0],
                "name": key[1],
                "version": row.version if row else None,
                "change": row.change_id if row else None,
                "changed_by": row.changed_by if row else None,
                "changed_at": row.changed_at if row else None,
                "exceptions": sorted(exceptions.get(key, {})),
                "servers": cells,
            }
        )

    wanted_keys = set(fleet) | set(exceptions)
    left: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for node in names:
        for key, held in rows.get(node, {}).items():
            if key not in wanted_keys:
                left.setdefault(key, []).append(
                    {
                        "node": node,
                        "state": held.get("state"),
                        "applied_version": held.get("version"),
                        "central": held.get("central"),
                    }
                )
    return {
        "generated_at": result.generated_at,
        "partial": result.partial,
        "servers": [
            {
                "node": node,
                "mode": modes.get(node, "managed"),
                "outcome": outcomes[node].status if node in outcomes else "unknown",
                "message": outcomes[node].message if node in outcomes else None,
                "code": outcomes[node].code if node in outcomes else None,
                "hint": outcomes[node].hint if node in outcomes else None,
                "pending_look": node in waiting,
            }
            for node in names
        ],
        "settings": entries,
        "released": [
            {"resource": key[0], "name": key[1], "servers": servers}
            for key, servers in sorted(left.items())
        ],
    }


def read(
    settings: FleetSettings,
    *,
    asker: Asker,
    manager: NodeManager | None = None,
    nodes: Sequence[str] | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """
    Ask every server what it manages and say where each stands.

    Args:
        settings: What the central wants.
        asker: Who asks: each server answers for them.
        manager: The node registry; a default one by default.
        nodes: Only these servers.
        refresh: Ask even a server whose answer is a few seconds old.

    Returns:
        :func:`compose`'s answer.

    Raises:
        SealError: This central is locked; no server can be reached.
    """
    from noust.fleet.nodes import NodeManager

    _register()
    manager = manager or NodeManager()
    result = gather(MANAGED_VIEW.name, asker=asker, manager=manager, nodes=nodes, refresh=refresh)
    return compose(settings, result, nodes=[n.name for n in result.nodes if not n.local])
