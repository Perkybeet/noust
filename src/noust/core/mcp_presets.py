# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What each kind of client of the tools needs, as the groups of tools it uses, said once.

``noust mcp token --preset deploy`` makes a token with exactly the permissions the routes behind
the ``inspect`` and ``deploy`` tools need, and the built-in assistant's token is made from the same
presets (the intersection of a person's permissions and one of them): one definition of "what a
client that deploys may do" (rule 3). The groups are the names in ``noust.web.mcp.tools``, spelled
here because that module is the console's and the command line must not import it to answer
``--help``; the function that turns a preset into permissions takes the catalogue as an argument
for the same reason.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

__all__ = ["PRESETS", "SELF_PERMISSION", "permissions_of_preset"]

#: What each kind of client needs, as the groups it uses: a deploying client also reads.
PRESETS: dict[str, tuple[str, ...]] = {
    "inspect": ("inspect",),
    "deploy": ("inspect", "deploy"),
    "control": ("inspect", "deploy", "control"),
}

#: The permission every session holds for itself, which every token keeps.
SELF_PERMISSION = "self"


def permissions_of_preset(
    preset: str,
    tools: Iterable[Any],
    permission_for_route: Callable[[str, str], str | None],
) -> list[str]:
    """
    Say which permissions the tools of a preset need.

    Args:
        preset: ``inspect``, ``deploy`` or ``control``.
        tools: The catalogue of tools (:data:`noust.web.mcp.tools.TOOLS`).
        permission_for_route: What says which permission a route declares.

    Returns:
        The permissions the routes behind that kind of client's tools need, and nothing more.

    Raises:
        KeyError: The preset is not one of :data:`PRESETS`.
    """
    groups = PRESETS[preset]
    needed = {
        permission_for_route(tool.method, tool.path) or "" for tool in tools if tool.group in groups
    }
    needed.discard("")
    needed.add(SELF_PERMISSION)
    return sorted(needed)
