# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The Docker zone of the whole fleet: every server's containers, images and the rest in one list.

Docker is the machine's, so each server answers for its own (``GET /api/docker/...``, which applies its
own permissions, its ceiling for centrals and its own idea of whose each object is) and the central only
puts the answers side by side with the server as a column, like every other resource of the fleet
(:mod:`noust.fleet.aggregate`). It re-implements nothing a node does (rule 3).

The views: ``docker_summary`` (one row per server: its engine, its counters, its disk and what asks for
attention there), ``docker_attention`` (what asks for attention on every server, in one list, worst first,
with the server on each), ``docker_engine`` (one row per server: which Docker it has and what does not fit
in it), and ``docker_stacks``, ``docker_containers``, ``docker_images``, ``docker_volumes`` and
``docker_networks`` (the rows each server lists).

Two things a server can be that are not "it answered":

- **It runs a Noust before 4.3**, which does not count Docker. Its answer is a 404, which the aggregator
  reads as ``unsupported`` with the code ``not_offered`` and its version, and the page says so with one
  sentence; it never turns the others' rows into an error.
- **It has no Docker, or one below what Noust speaks.** It answers 200 with ``available: false`` and the
  doctor's report. It has no rows, and a note on the view says why (``notes`` of its outcome).

A listing is the first page of each server (``limit``, 200 by default, 1000 at most); a server with more
says so in a note.
"""

from __future__ import annotations

import builtins
from collections.abc import Mapping
from typing import Any

from noust.core.docker.summary import ORDER
from noust.fleet.aggregate import RESOURCES, NodeView, Part, Resource, href

__all__ = [
    "ATTENTION",
    "DEFAULT_LIMIT",
    "ENGINE",
    "LISTINGS",
    "MAX_LIMIT",
    "PAGES",
    "SUMMARY",
    "register",
]

#: Each server's pages for this, on its own console.
PAGES = {
    "summary": "/server/docker",
    "stacks": "/server/docker/stacks",
    "containers": "/server/docker/containers",
    "images": "/server/docker/images",
    "volumes": "/server/docker/volumes",
    "networks": "/server/docker/networks",
    "engine": "/server/docker/engine",
}

#: The most rows one server is asked for, which is what it answers at most.
MAX_LIMIT = 1000

#: How many rows each server is asked for when the view does not say.
DEFAULT_LIMIT = 200

#: What a view may filter by, each a server's own filter.
_FILTERS: dict[str, tuple[str, ...]] = {
    "stacks": ("owner", "search"),
    "containers": ("owner", "app", "state", "project", "search"),
    "images": ("owner", "search"),
    "volumes": ("owner", "search"),
    "networks": ("owner", "search"),
}


def _path(kind: str) -> str:
    """
    Args:
        kind: ``summary``, ``engine`` or a listing.

    Returns:
        Where a server answers it.
    """
    return f"/api/docker/{kind}"


def _params(kind: str, params: Mapping[str, Any]) -> dict[str, Any]:
    """
    Check the parameters of a listing.

    Args:
        kind: Which listing.
        params: ``limit`` (per server, 1 to 1000, 200 by default) and the filters the listing has.

    Returns:
        The checked parameters; anything else is dropped.
    """
    try:
        limit = int(params.get("limit", DEFAULT_LIMIT))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    checked: dict[str, Any] = {"limit": max(1, min(limit, MAX_LIMIT))}
    for name in _FILTERS[kind]:
        value = params.get(name)
        if isinstance(value, str) and value:
            checked[name] = value
    return checked


def _labelled(view: NodeView, kind: str, fields: Mapping[str, Any]) -> dict[str, Any]:
    """
    Args:
        view: The server.
        kind: Which page it is a row of.
        fields: One of its rows.

    Returns:
        The row, with its server and its page there and through this central.
    """
    page = PAGES[kind]
    return {
        **fields,
        "node": view.name,
        "local": view.local,
        "page": page,
        "href": href(view.name, view.local, page),
    }


def _answer(view: NodeView, kind: str) -> dict[str, Any] | None:
    """
    Args:
        view: The server.
        kind: Which part.

    Returns:
        What it answered, or None when it did not (it is down, or it does not offer Docker).
    """
    body = view.parts.get(kind)
    return body if isinstance(body, dict) else None


def _unavailable(view: NodeView, body: Mapping[str, Any]) -> None:
    """
    Say on the view that a server has no Docker to look at.

    Args:
        view: The server.
        body: What it answered.
    """
    engine = body.get("engine")
    state = engine.get("state") if isinstance(engine, dict) else None
    view.notes.append(f"{view.name} has no Docker to look at" + (f" ({state})." if state else "."))


def _list_builder(kind: str) -> Any:
    """
    Args:
        kind: Which listing.

    Returns:
        The builder of its rows: one per item, as the server lists it.
    """

    def build(view: NodeView) -> builtins.list[dict[str, Any]]:
        body = _answer(view, kind)
        if body is None:
            return []
        if body.get("available") is False:
            _unavailable(view, body)
            return []
        items = body.get("items")
        rows = [
            _labelled(view, kind, item)
            for item in (items if isinstance(items, list) else [])
            if isinstance(item, dict)
        ]
        total = body.get("total")
        if isinstance(total, int) and total > len(rows):
            view.notes.append(f"{view.name} has {total} of these and {len(rows)} are shown.")
        return rows

    return build


def _short_engine(engine: Any) -> dict[str, Any]:
    """
    Args:
        engine: A server's ``engine`` object.

    Returns:
        What a row says of an engine: its state and what the pages of the fleet draw of it.
    """
    body: dict[str, Any] = engine if isinstance(engine, dict) else {}
    found = body.get("info")
    info: dict[str, Any] = found if isinstance(found, dict) else {}
    return {
        "state": body.get("state"),
        "usable": body.get("usable"),
        "ok": body.get("ok"),
        "version": info.get("server_version") or None,
        "api": info.get("api") or None,
        "compose": info.get("compose_version") or None,
        "image_store": info.get("image_store") or None,
        "rootless": info.get("rootless"),
        "os": info.get("os") or None,
        "limits": [
            limit.get("code") for limit in body.get("limits") or [] if isinstance(limit, dict)
        ],
    }


def _build_summary(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per server: its engine, its counters, its disk and what asks for attention there.

    Args:
        view: The server.

    Returns:
        The row. A server that did not answer, or that has no Docker, has one too, without numbers:
        the page says what each is from ``outcome`` and ``available``.
    """
    body = _answer(view, "summary")
    available = body.get("available") if body is not None else None
    row: dict[str, Any] = {
        "name": view.name,
        "outcome": view.outcome.status,
        "available": available if isinstance(available, bool) else None,
        "engine": _short_engine(body.get("engine")) if body else None,
        "counts": body.get("counts") if body and available else {},
        "attention": body.get("attention") if body and available else [],
        "disk": body.get("disk") if body and available else None,
        "can_manage": bool(body.get("can_manage")) if body else False,
    }
    if body is not None and available is False:
        _unavailable(view, body)
    return [_labelled(view, "summary", row)]


def _build_attention(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per thing that asks for attention on a server.

    Args:
        view: The server.

    Returns:
        The rows, each with its server and the page where it is looked into.
    """
    body = _answer(view, "summary")
    if body is None:
        return []
    if body.get("available") is False:
        _unavailable(view, body)
        return []
    items = body.get("attention")
    return [
        _labelled(view, "containers" if item.get("subject") else "summary", item)
        for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict)
    ]


def _worst_first(rows: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
    """
    Args:
        rows: What asks for attention on every server.

    Returns:
        The rows in the order of how much it hurts (:data:`noust.core.docker.summary.ORDER`); within
        a kind, as each server said them and the servers in the order they were asked.
    """

    def rank(row: Mapping[str, Any]) -> int:
        kind = row.get("kind")
        return ORDER.index(kind) if kind in ORDER else len(ORDER)

    return sorted(rows, key=rank)


def _build_engine(view: NodeView) -> builtins.list[dict[str, Any]]:
    """
    One row per server: which Docker it has and what does not fit in it.

    Args:
        view: The server.

    Returns:
        The row, with the doctor's sentences as the server said them.
    """
    body = _answer(view, "engine")
    row: dict[str, Any] = {
        "name": view.name,
        "outcome": view.outcome.status,
        "engine": _short_engine(body) if body is not None else None,
        "facts": body.get("facts") if body else [],
        "limits": body.get("limits") if body else [],
        "info": body.get("info") if body else None,
    }
    return [_labelled(view, "engine", row)]


def _listing(kind: str) -> Resource:
    """
    Args:
        kind: Which listing.

    Returns:
        Its view: each server asked for its own page of it.
    """

    def asked(params: Mapping[str, Any]) -> dict[str, Any]:
        return {name: value for name, value in params.items() if name in ("limit", *_FILTERS[kind])}

    return Resource(
        f"docker_{kind}",
        (Part(kind, _path(kind), params=asked, title=f"Docker {kind}"),),
        ttl=10.0,
        max_stale=300.0,
        build=_list_builder(kind),
        params=lambda params: _params(kind, params),
    )


#: One row per server: its Docker in short.
SUMMARY = Resource(
    "docker_summary",
    (Part("summary", _path("summary"), title="Docker summary"),),
    ttl=10.0,
    max_stale=300.0,
    build=_build_summary,
    per_node=True,
)

#: What asks for attention on every server, worst first.
ATTENTION = Resource(
    "docker_attention",
    (Part("summary", _path("summary"), title="Docker summary"),),
    ttl=10.0,
    max_stale=300.0,
    build=_build_attention,
    sort=_worst_first,
)

#: One row per server: which Docker it has.
ENGINE = Resource(
    "docker_engine",
    (Part("engine", _path("engine"), title="Docker engine"),),
    ttl=30.0,
    max_stale=900.0,
    build=_build_engine,
    per_node=True,
)

#: The rows each server lists, by kind.
LISTINGS: dict[str, Resource] = {kind: _listing(kind) for kind in _FILTERS}


def register() -> None:
    """Add the views to the aggregator's, once."""
    for resource in (SUMMARY, ATTENTION, ENGINE, *LISTINGS.values()):
        RESOURCES.setdefault(resource.name, resource)
