# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The routes a push of the fleet's settings may use on a node, stated once.

Both sides read this list. The central's reconciler declares them as the operations it needs of a
node (so a node that lacks one is skipped as ``unsupported``, and the ceiling is judged against
them), and the node admits a request signed as a push (:mod:`noust.web.auth.fleet_sync`) on
exactly these and on nothing else: the confirmation that stands for a person's sudo mode is the
central's to give for the fleet's settings, not for the rest of the node.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from noust.fleet.settings.kinds import KINDS

__all__ = [
    "MANAGED_ITEM",
    "MANAGED_LIST",
    "MANAGED_OPERATIONS",
    "MANAGED_PREFIX",
    "matches_sync_route",
    "sync_operations",
]

#: Where a node tells what it manages (:mod:`noust.web.api.managed_settings`).
MANAGED_PREFIX = "/api/managed-settings"
MANAGED_LIST = ("GET", MANAGED_PREFIX)
MANAGED_ITEM = MANAGED_PREFIX + "/{resource}/{name}"

#: What the central needs of a node to record and let go of what it applied.
MANAGED_OPERATIONS: tuple[tuple[str, str], ...] = (
    MANAGED_LIST,
    ("PUT", MANAGED_ITEM),
    ("DELETE", MANAGED_ITEM),
)


def sync_operations() -> tuple[tuple[str, str], ...]:
    """
    Returns:
        Every ``(METHOD, template)`` a push may use: the node's own route of each kind of
        setting, and the ones that record what was applied.
    """
    found: dict[tuple[str, str], None] = {}
    for operation in MANAGED_OPERATIONS:
        found.setdefault(operation)
    for kind in KINDS.values():
        for operation in kind.operations:
            found.setdefault(operation)
    return tuple(found)


def _pattern(template: str) -> re.Pattern[str]:
    """
    Args:
        template: A path template.

    Returns:
        A pattern for a path that fills each ``{placeholder}` with one segment.
    """
    pieces = re.split(r"\{[^/{}]+\}", template)
    return re.compile("[^/]+".join(re.escape(piece) for piece in pieces))


def matches_sync_route() -> Callable[[str, str], bool]:
    """
    Returns:
        A test of whether a method and a path are one of :func:`sync_operations`.
    """
    compiled = [(method, _pattern(template)) for method, template in sync_operations()]

    def matches(method: str, path: str) -> bool:
        verb = method.upper()
        return any(verb == m and pattern.fullmatch(path) for m, pattern in compiled)

    return matches
