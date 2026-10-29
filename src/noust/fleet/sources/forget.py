# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central forgets about a server that leaves the fleet.

The leases it made (the token the server holds ends within the hour on its own and is not renewed),
the events it still owed, and the deployments and previews it was following there: all of it is
about a server the central no longer manages, and none of it can be finished.
"""

from __future__ import annotations

from noust.core.store import NoustStore
from noust.fleet.sources.follows import Follows
from noust.fleet.sources.forwards import Forwards
from noust.fleet.sources.grants import LeaseGrants

__all__ = ["forget_node"]


def forget_node(store: NoustStore, node: str) -> None:
    """
    Drop what is kept about a server that left the fleet.

    Args:
        store: The central's store.
        node: The server.
    """
    LeaseGrants(store).forget(node)
    Forwards(store).forget(node)
    Follows(store).forget(node)
