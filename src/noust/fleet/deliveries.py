# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The record of deliveries of the whole fleet.

Every server delivers its own notifications and keeps the record of them in its own store, so
that they keep arriving, and keep being recorded, when the central is down. Whether a message
reached the place it was meant for is the question after an outage, and the answer is on the
server that sent it. These are the fleet views that ask all of them, like every other resource
of the fleet (:mod:`noust.fleet.aggregate`): ``deliveries``, the rows newest first, and
``delivery_counts``, how many there are in each state on each server. The counts are a view of
their own because a server's record is longer than the part of it that is asked for, and a count
of the rows shown would say less than there is. The central is one more server: its own record is
its three events about the fleet.

A state or a channel to filter by is the server's own filter, and so is every refusal.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any

from noust.fleet.aggregate import RESOURCES, NodeView, Part, Resource, href

__all__ = ["COUNTS", "DELIVERIES", "MAX_LIMIT", "PAGE", "PATH", "register"]

#: Where a server lists its deliveries.
PATH = "/api/notifications/deliveries"

#: The page of a server's console where a delivery can be sent again.
PAGE = "/settings/notifications"

#: The most deliveries one server is asked for, which is what it answers at most.
MAX_LIMIT = 500

#: How many deliveries each server is asked for when the view does not say.
DEFAULT_LIMIT = 100

#: What a view may filter by: the server's own filters.
_FILTERS = ("state", "channel")


def _params(params: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check the view's parameters.

    Args:
        params: ``limit`` (per server, 1 to 500, 100 by default), ``state`` and ``channel``.

    Returns:
        The checked parameters; anything else is dropped.
    """
    try:
        limit = int(params.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    checked: dict[str, Any] = {"limit": max(1, min(limit, MAX_LIMIT))}
    for name in _FILTERS:
        value = params.get(name)
        if isinstance(value, str) and value:
            checked[name] = value
    return checked


def _asked(params: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        params: The view's checked parameters.

    Returns:
        What each server is asked.
    """
    return {name: params[name] for name in ("limit", *_FILTERS) if name in params}


def _labelled(view: NodeView, fields: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        view: The server.
        fields: One of its rows.

    Returns:
        The row, with its server and its page there and through this central.
    """
    return {
        **fields,
        "node": view.name,
        "local": view.local,
        "page": PAGE,
        "href": href(view.name, view.local, PAGE),
    }


def _build(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per delivery, as its server recorded it.

    Args:
        view: The server.

    Returns:
        The rows.
    """
    answer = view.parts.get("deliveries")
    items = answer.get("items") if isinstance(answer, dict) else None
    return [
        _labelled(view, item)
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
    ]


def _newest_first(rows: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
    """Returns: The deliveries of every server together, the newest first."""
    return sorted(rows, key=lambda row: float(row.get("created_at") or 0.0), reverse=True)


def _build_counts(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per server: how many deliveries it has in each state.

    Args:
        view: The server.

    Returns:
        The row.
    """
    answer = view.parts.get("deliveries")
    counts = answer.get("counts") if isinstance(answer, dict) else None
    return [_labelled(view, {"counts": counts if isinstance(counts, dict) else {}})]


#: The deliveries of every server.
DELIVERIES = Resource(
    "deliveries",
    (Part("deliveries", PATH, params=_asked, title="Notification deliveries"),),
    ttl=15.0,
    max_stale=300.0,
    build=_build,
    sort=_newest_first,
    params=_params,
)

#: How many each server has in each state, from the whole of its record.
COUNTS = Resource(
    "delivery_counts",
    (Part("deliveries", PATH, params=lambda _: {"limit": 1}, title="Notification deliveries"),),
    ttl=15.0,
    max_stale=300.0,
    build=_build_counts,
)


def register() -> None:
    """Add the views to the aggregator's, once."""
    RESOURCES.setdefault(DELIVERIES.name, DELIVERIES)
    RESOURCES.setdefault(COUNTS.name, COUNTS)
