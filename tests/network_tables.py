# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The networking tables, for the hand-written stand-ins of the store.

Writing a site reads the policy and the certificate an application is served with, and
listing certificates reads the ones uploaded. A test that replaces the store with a
small fake of its own would have to learn each of those reads; mixing this in gives it
the tables, empty, and the reads and writes the web server manager makes, exactly as the real
store answers them.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

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

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Cursor]:
        """
        Yields:
            A cursor on that database, committed when the block ends: a site's history
            is written there every time its file is.
        """
        connection = self._get_connection()
        cursor = connection.cursor()
        try:
            yield cursor
            connection.commit()
        except sqlite3.Error:
            connection.rollback()
            raise

    get_site_policy_document = NoustStore.get_site_policy_document
    get_served_certificate = NoustStore.get_served_certificate
