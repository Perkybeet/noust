# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a check of the monitor must remember between two scans.

"This cron job's last run was already announced", "this missing backup was
announced yesterday", "these updates were announced for the same packages":
each is a small fact that has to survive a restart of the monitor (the daemon
restarts on every upgrade), or the same alert is sent again, or never sent.

The monitor's older checks keep theirs in JSON files next to its database. The
4.0 checks use the store instead (``notification_state``, v14): one table, one
reader and writer, a transaction per write and a rollback under ``--dry-run``
like every other write to the store.

A value is a short string; :meth:`NotificationState.get_json` and
:meth:`~NotificationState.put_json` carry anything structured.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

from noust.core.store import NoustStore, get_store


class NotificationState:
    """
    Key-value memory for the monitor's checks, in the store.

    Every method can raise :class:`sqlite3.Error` (and :class:`OSError` when
    the store cannot be opened); the caller is the monitor loop, which logs it
    and carries on: a check that cannot remember is a check that repeats
    itself, not one that stops.
    """

    def __init__(
        self, store: NoustStore | None = None, *, clock: Callable[[], float] = time.time
    ) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
            clock: Epoch seconds. Tests move it.
        """
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store the facts live in."""
        return self._store if self._store is not None else get_store()

    def get(self, scope: str, item: str) -> str | None:
        """
        Args:
            scope: Which check (``cron``, ``backups``).
            item: What within it (a job, a domain).

        Returns:
            The value, or None when nothing was remembered.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT value FROM notification_state WHERE scope = ? AND item = ?", (scope, item)
            )
            row = cursor.fetchone()
        return str(row[0]) if row is not None else None

    def put(self, scope: str, item: str, value: str) -> None:
        """
        Remember a value, replacing the last.

        Args:
            scope: Which check.
            item: What within it.
            value: The value.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO notification_state (scope, item, value, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(scope, item) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (scope, item, value, self._clock()),
            )

    def delete(self, scope: str, item: str) -> None:
        """
        Forget a value.

        Args:
            scope: Which check.
            item: What within it.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM notification_state WHERE scope = ? AND item = ?", (scope, item)
            )

    def items(self, scope: str) -> dict[str, str]:
        """
        Args:
            scope: Which check.

        Returns:
            Everything it remembers, by item.
        """
        with self.store._transaction() as cursor:
            cursor.execute("SELECT item, value FROM notification_state WHERE scope = ?", (scope,))
            return {str(item): str(value) for item, value in cursor.fetchall()}

    def keep_only(self, scope: str, items: set[str]) -> None:
        """
        Forget what a check no longer watches (a deleted job, a removed application).

        Args:
            scope: Which check.
            items: The items still watched.
        """
        for item in set(self.items(scope)) - items:
            self.delete(scope, item)

    def get_json(self, scope: str, item: str) -> Any:
        """
        Args:
            scope: Which check.
            item: What within it.

        Returns:
            The decoded value, or None when nothing (or nothing readable) was remembered.
        """
        raw = self.get(scope, item)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    def put_json(self, scope: str, item: str, value: Any) -> None:
        """
        Remember a structured value.

        Args:
            scope: Which check.
            item: What within it.
            value: Anything JSON can carry.
        """
        self.put(scope, item, json.dumps(value, separators=(",", ":")))
