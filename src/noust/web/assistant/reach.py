# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which server a question is about, and which servers the person reaches (item 171).

The assistant belongs to the console the person works in. With a central it works on the servers of the
fleet, and the person says which one they are looking at: a server of theirs, all of them, or (where Noust
deploys applications itself) the server it runs on. What a person reaches of the fleet is what
``GET /api/nodes`` lists for them (every server for the instance, those assigned to their teams for a member
of a team), so a team reaches only its own servers here too.
"""

from __future__ import annotations

from noust.core.exceptions import ValidationError

__all__ = ["resolve_reach", "servers_of_the_person"]


def servers_of_the_person() -> tuple[str, ...]:
    """
    Returns:
        The names of the servers of the fleet the caller reaches, as ``GET /api/nodes`` lists them, under
        the scope bound for the caller. None when this Noust has no fleet to speak of (its dependencies are
        not installed).
    """
    try:
        from noust.core.exceptions import NodeError
        from noust.web.api import nodes as nodes_api
        from noust.web.api.deps import scoped_store

        scoped = scoped_store()
        visible = None if scoped.scope.sees_all else {node.name for node in scoped.list_nodes()}
        return tuple(
            sorted(
                node.name
                for node in nodes_api.node_manager().list()
                if visible is None or node.name in visible
            )
        )
    except (ImportError, NodeError):
        return ()


def resolve_reach(requested: str | None) -> tuple[str, tuple[str, ...]]:
    """
    Args:
        requested: The server the person says they are looking at.

    Returns:
        ``(server, servers)``: ``*`` for all of them, a server's name, or ``""`` for this one, and the
        names of the servers the person reaches. A central that deploys nothing itself has no "this one":
        without a choice it is all of them.

    Raises:
        ValidationError: A server that is not one of the person's.
    """
    from noust.central import role

    names = servers_of_the_person()
    asked = (requested or "").strip()
    if asked == "*":
        return "*", names
    if asked:
        if asked not in names:
            raise ValidationError(
                "There is no such server",
                details=f"Choose one of: {', '.join(names) or 'none: this central has no servers yet'}.",
                field="server",
            )
        return asked, names
    if role() == "hub":
        return "*", names
    return "", names
