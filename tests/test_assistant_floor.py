# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The floor of the built-in assistant: what its token never holds, pinned (item 171).

A closed list in one light module, so that the policy of approval can read it without loading the
assistant. It is not the MCP's list of what a client outside is kept from: the assistant is asked to
propose what Noust knows how to do, so creating an application or restoring a copy is not in it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from noust.core.assistant_floor import ASSISTANT_FLOOR
from noust.core.permission_catalog import ALL_PERMISSIONS, Permission
from noust.web.mcp.tools import FORBIDDEN_PERMISSIONS
from tests.support import import_graph_support as graph

P = Permission
PACKAGE = Path(__file__).resolve().parent.parent / "src" / "noust"

#: The list, written out here so that a change to it is a change to this test too.
PINNED = {
    "secrets.reveal",
    "root_equivalent",
    "server.host_access",
    "server.terminal",
    "apps.terminal",
    "accounts.manage",
    "accounts.read",
    "audit.manage",
    "audit.read",
    "security.manage",
    "settings.manage",
    "fleet.manage",
    "teams.manage",
}


@cache
def _closure() -> dict[str, str | None]:
    return graph.reach(
        graph.read_edges(PACKAGE),
        ("noust.core.assistant_floor",),
        frozenset({graph.MODULE}),
        packages=graph.package_modules(PACKAGE),
    )


def test_the_floor_is_exactly_this_list() -> None:
    assert ASSISTANT_FLOOR == PINNED


def test_it_is_a_set_of_permissions_that_exist() -> None:
    assert ASSISTANT_FLOOR <= ALL_PERMISSIONS
    assert isinstance(ASSISTANT_FLOOR, frozenset)


def test_it_is_smaller_than_the_mcps_and_inside_it() -> None:
    assert ASSISTANT_FLOOR < FORBIDDEN_PERMISSIONS
    assert FORBIDDEN_PERMISSIONS - ASSISTANT_FLOOR == {
        P.APPS_MANAGE,
        P.DATABASES_WRITE,
        P.DATABASES_MANAGE,
        P.BACKUPS_MANAGE,
        P.SERVER_MANAGE,
    }


def test_the_assistants_own_settings_and_the_ways_data_leaves_are_in_it() -> None:
    # settings.manage is where its configuration, the policy of approval and the channels of
    # notification (a way out for data) live.
    assert P.SETTINGS_MANAGE in ASSISTANT_FLOOR
    assert P.SECRETS_REVEAL in ASSISTANT_FLOOR


def test_reading_it_loads_nothing_but_the_catalogue_of_permissions() -> None:
    loaded = {m for m in _closure() if m.startswith("noust.")}
    assert loaded <= {
        "noust",
        "noust.core",
        "noust.core.assistant_floor",
        "noust.core.lazy_exports",  # what ``noust.core``'s own __init__ loads
        "noust.core.permission_catalog",
    }, sorted(loaded)
