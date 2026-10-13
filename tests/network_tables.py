# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The networking tables, for the hand-written stand-ins of the store.

Writing a site reads the policy and the certificate an application is served with, and
listing certificates reads the ones uploaded. A test that replaces the store with a
small fake of its own would have to learn each of those reads; mixing this in gives it
the tables, empty, and the two reads the web server manager makes, exactly as the real
store answers them.
"""

from __future__ import annotations

import sqlite3

from noust.core import schema_v14
from noust.core.store import NoustStore


class NetworkTables:
    """Empty networking tables for a fake store."""

    _network_connection: sqlite3.Connection | None = None

    def _get_connection(self) -> sqlite3.Connection:
        """
        Returns:
            An in-memory database holding the networking tables, empty.
        """
        if self._network_connection is None:
            connection = sqlite3.connect(":memory:")
            connection.row_factory = sqlite3.Row
            connection.executescript(schema_v14.NETWORK_SQL)
            self._network_connection = connection
        return self._network_connection

    get_site_policy_document = NoustStore.get_site_policy_document
    get_served_certificate = NoustStore.get_served_certificate
