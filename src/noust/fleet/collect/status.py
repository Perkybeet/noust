# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How collecting is going, in one answer for the console and the command line (item 195).

One function builds it so that ``noust fleet collect status`` and ``GET /api/central-backups/status``
never disagree, and so that the rule of what a person sees is in one place: a person of a team sees
the servers assigned to their teams and what is held of them, and not the central's disk, its folder
or its knobs, which are the instance's.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

from noust.core.store import NoustStore, get_store
from noust.fleet.collect.board import Board, get_board
from noust.fleet.collect.layout import Layout
from noust.fleet.collect.records import CollectedStore, NodeStates
from noust.fleet.collect.settings import Follows, SettingsStore
from noust.fleet.collect.space import space_of

__all__ = ["status_of"]


def status_of(
    *,
    visible: Collection[str] | None = None,
    instance: bool = True,
    store: NoustStore | None = None,
    layout: Layout | None = None,
    board: Board | None = None,
) -> dict[str, Any]:
    """
    Say how collecting is going.

    Args:
        visible: The servers the caller sees; every one when None.
        instance: Whether the caller sees what belongs to the central itself (its knobs, its disk,
            its folder): the instance's, never a team's.
        store: The store; the process-wide one by default.
        layout: Where the copies are; the central's backup directory by default.
        board: What is moving; the process-wide board by default.

    Returns:
        ``following`` (``following`` of ``of``, and the exceptions), ``nodes`` (one row per
        server: its own answer, whether it is followed, how its last pass went, what the central
        holds of it and what is moving now), and, for the instance, ``settings``, ``space`` and
        ``directory``.
    """
    target = store or get_store()
    knobs = SettingsStore(target).get()
    follows = Follows(target)
    names = sorted(
        node.name for node in target.list_nodes() if visible is None or node.name in visible
    )
    states = NodeStates(target).all()
    held = CollectedStore(target)
    moving = (board or get_board()).snapshot()
    own = follows.own()
    rows: list[dict[str, Any]] = []
    for name in names:
        state = states.get(name)
        copies = held.list(node=name)
        rows.append(
            {
                "node": name,
                "follow": own.get(name),
                "following": own.get(name, knobs.enabled),
                "last_pass_at": state.last_pass_at if state else None,
                "last_ok_at": state.last_ok_at if state else None,
                "last_error": state.last_error if state else None,
                "last_error_code": state.last_error_code if state else None,
                "failures": state.failures if state else 0,
                "support": dict(state.support) if state else {},
                "held": {"count": len(copies), "bytes": sum(c.size for c in copies)},
                "newest": copies[0].created_at if copies else None,
                "moving": moving.get(name),
            }
        )
    count = follows.counts(names, knobs)
    answer: dict[str, Any] = {
        "following": {
            "following": count.following,
            "of": count.of,
            "exceptions": list(count.exceptions),
        },
        "nodes": rows,
        "settings": None,
        "space": None,
        "directory": None,
    }
    if instance:
        folder = (layout or Layout.default()).root
        answer["settings"] = knobs.to_dict()
        answer["directory"] = str(folder)
        answer["space"] = space_of(
            folder, used=sum(held.usage().values()), min_free=knobs.min_free_bytes
        ).to_dict()
    return answer
