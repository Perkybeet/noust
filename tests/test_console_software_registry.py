# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's Software page and the server's registry say the same things (item 118).

The server's registry (``managers/server/components.py``) is the one authority on what the software of a
server is and on who may install or remove each piece; the page draws it and offers a button only to whoever
the server would let press it. The console keeps two small tables of its own, because the API does not say
them (the order the pieces are listed in, and the permissions an install takes), in
``panel/src/features/server/software/registry.ts``. This holds those tables to the server's, so a component
added or a permission changed on one side cannot leave the other offering what would be refused.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from noust.managers.server.components import COMPONENTS
from noust.web.permissions.routes_server_software import (
    DEFAULT_INSTALL_PERMISSION,
    INSTALL_PERMISSIONS,
    REMOVE_PERMISSIONS,
    ROUTES,
)

_REGISTRY = (
    Path(__file__).resolve().parent.parent / "panel/src/features/server/software/registry.ts"
)

_INSTALL_ROUTE = ("POST", "/api/server/software/{component}/install")
_REMOVE_ROUTE = ("POST", "/api/server/software/{component}/remove")


@pytest.fixture(scope="module")
def console() -> str:
    """The console's registry module, as text."""
    return _REGISTRY.read_text(encoding="utf-8")


def _strings(text: str) -> list[str]:
    """
    Args:
        text: A piece of TypeScript.

    Returns:
        The double-quoted strings in it, in order.
    """
    return re.findall(r'"([^"]+)"', text)


def _block(console: str, pattern: str) -> str:
    """
    Args:
        console: The registry's source.
        pattern: A regular expression with one group, which is what is wanted.

    Returns:
        What the group matched.
    """
    found = re.search(pattern, console, re.S)
    assert found is not None, f"{pattern} is not in the console's registry"
    return found.group(1)


def test_the_console_lists_the_components_the_server_registers_in_the_same_order(
    console: str,
) -> None:
    listed = _strings(_block(console, r"const KNOWN = \[(.*?)\] as const"))
    assert listed == [component.id for component in COMPONENTS]


def test_the_console_offers_install_to_whoever_the_server_asks_it_of(console: str) -> None:
    """The route asks for its own permission; the handler asks for the component's on top."""
    table_text = _block(console, r"const INSTALL[^=]*=\s*\{(.*?)\n\};")
    table = {
        match.group(1): _strings(match.group(2))
        for match in re.finditer(r"(\w+):\s*\[([^\]]*)\]", table_text)
    }
    default = _strings(_block(console, r"INSTALL\[id\]\s*\?\?\s*\[([^\]]*)\]"))
    floor = str(ROUTES[_INSTALL_ROUTE])
    for component in COMPONENTS:
        own = str(INSTALL_PERMISSIONS.get(component.id, DEFAULT_INSTALL_PERMISSION))
        assert table.get(component.id, default) == list(dict.fromkeys([floor, own])), component.id


def test_the_console_offers_removal_to_whoever_the_server_asks_it_of(console: str) -> None:
    """Removing takes the route's permission for any piece, and the databases' beside it for an engine."""
    engines = set(_strings(_block(console, r"const ENGINES[^=]*=\s*new Set\(\[(.*?)\]\)")))
    assert engines == set(REMOVE_PERMISSIONS)
    floor = str(ROUTES[_REMOVE_ROUTE])
    assert re.search(
        rf'ENGINES\.has\(id\)\s*\?\s*\["{floor}", "{set(REMOVE_PERMISSIONS.values()).pop()}"\]\s*:\s*\["{floor}"\]',
        console,
    )
