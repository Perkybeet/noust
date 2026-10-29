# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a central leased to which server: the ``source_lease_grants`` table.

The token is not here, only the fact: which lease (host, installation and part), for which
repositories, until when on the central's clock, or why it could not be made. It is what lets the
central renew a lease before it ends and say, for each server, whether it holds one and why not.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from noust.core.store import NoustStore, get_store

__all__ = ["Grant", "LeaseGrants"]


@dataclass(frozen=True)
class Grant:
    """
    One lease a central made to a server.

    Attributes:
        node: The server.
        key: The lease's name on the server.
        host: The GitHub host.
        installation_id: The installation its token was issued from.
        repositories: ``owner/repo`` it covers, sorted.
        state: ``leased`` while it is held, ``failed`` when the last try did not work.
        pushed_at: When the server took it (epoch seconds, the central's clock).
        expires_at: When it ends (the central's clock); None for one that never was made.
        error: The sentence saying why the last try failed, as ``{code, params}``.
        updated_at: When this row was last written.
    """

    node: str
    key: str
    host: str
    installation_id: int
    repositories: tuple[str, ...]
    state: str
    pushed_at: float | None
    expires_at: float | None
    error: dict[str, Any] | None
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The grant as the API shows it.
        """
        return {
            "key": self.key,
            "host": self.host,
            "installation_id": self.installation_id,
            "repositories": list(self.repositories),
            "state": self.state,
            "pushed_at": self.pushed_at,
            "expires_at": self.expires_at,
            "error": self.error,
        }


def _grant(row: Any) -> Grant:
    """
    Args:
        row: A ``source_lease_grants`` row.

    Returns:
        The grant.
    """
    return Grant(
        node=str(row["node"]),
        key=str(row["lease_key"]),
        host=str(row["host"]),
        installation_id=int(row["installation_id"]),
        repositories=tuple(json.loads(row["repos"])),
        state=str(row["state"]),
        pushed_at=row["pushed_at"],
        expires_at=row["expires_at"],
        error=json.loads(row["error"]) if row["error"] else None,
        updated_at=float(row["updated_at"]),
    )


class LeaseGrants:
    """
    The leases a central made, read and written in one place.

    Args:
        store: The central's store; the process-wide one by default.
        clock: Epoch seconds (tests move it).
    """

    def __init__(self, store: NoustStore | None = None, clock: Callable[[], float] = time.time):
        self._store = store
        self._clock = clock

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def list(self, node: str | None = None) -> list[Grant]:
        """
        Args:
            node: Only this server's.

        Returns:
            The grants, by server and key.
        """
        sql = "SELECT * FROM source_lease_grants"
        params: tuple[str, ...] = ()
        if node is not None:
            sql += " WHERE node = ?"
            params = (node,)
        rows = self.store._get_connection().execute(sql + " ORDER BY node, lease_key", params)
        return [_grant(row) for row in rows.fetchall()]

    def get(self, node: str, key: str) -> Grant | None:
        """
        Args:
            node: The server.
            key: The lease's key.

        Returns:
            The grant, or None.
        """
        return next((g for g in self.list(node) if g.key == key), None)

    def leased(
        self,
        node: str,
        key: str,
        *,
        host: str,
        installation_id: int,
        repositories: Sequence[str],
        expires_in: int,
    ) -> Grant:
        """
        Record that a server took a lease.

        Args:
            node: The server.
            key: The lease's key.
            host: The GitHub host.
            installation_id: The installation.
            repositories: What it covers.
            expires_in: Seconds it lives from now.

        Returns:
            The grant.
        """
        now = self._clock()
        return self._write(
            node, key, host, installation_id, repositories, "leased", now, now + expires_in, None
        )

    def failed(
        self,
        node: str,
        key: str,
        *,
        host: str,
        installation_id: int,
        repositories: Sequence[str],
        error: Mapping[str, Any],
    ) -> Grant:
        """
        Record that a lease could not be made or handed over, keeping what the server holds.

        Args:
            node: The server.
            key: The lease's key.
            host: The GitHub host.
            installation_id: The installation.
            repositories: What it should cover.
            error: Why.

        Returns:
            The grant. A server that holds an earlier lease keeps its end.
        """
        earlier = self.get(node, key)
        return self._write(
            node,
            key,
            host,
            installation_id,
            earlier.repositories if earlier else repositories,
            "failed",
            earlier.pushed_at if earlier else None,
            earlier.expires_at if earlier else None,
            dict(error),
        )

    def _write(
        self,
        node: str,
        key: str,
        host: str,
        installation_id: int,
        repositories: Sequence[str],
        state: str,
        pushed_at: float | None,
        expires_at: float | None,
        error: dict[str, Any] | None,
    ) -> Grant:
        """
        Write one grant, replacing the one of the same server and key.

        Args:
            node: The server.
            key: The lease's key.
            host: The GitHub host.
            installation_id: The installation.
            repositories: What it covers.
            state: ``leased`` or ``failed``.
            pushed_at: When the server took it.
            expires_at: When it ends.
            error: Why it failed, if it did.

        Returns:
            The grant as written.
        """
        updated = self._clock()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO source_lease_grants (node, lease_key, host, installation_id, repos, "
                "state, pushed_at, expires_at, error, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(node, lease_key) DO UPDATE SET host = excluded.host, "
                "installation_id = excluded.installation_id, repos = excluded.repos, "
                "state = excluded.state, pushed_at = excluded.pushed_at, "
                "expires_at = excluded.expires_at, error = excluded.error, "
                "updated_at = excluded.updated_at",
                (
                    node,
                    key,
                    host,
                    installation_id,
                    json.dumps(sorted(repositories)),
                    state,
                    pushed_at,
                    expires_at,
                    json.dumps(error, sort_keys=True) if error else None,
                    updated,
                ),
            )
        return Grant(
            node=node,
            key=key,
            host=host,
            installation_id=installation_id,
            repositories=tuple(sorted(repositories)),
            state=state,
            pushed_at=pushed_at,
            expires_at=expires_at,
            error=dict(error) if error else None,
            updated_at=updated,
        )

    def forget(self, node: str, keys: Sequence[str] | None = None) -> int:
        """
        Forget what was leased to a server.

        Args:
            node: The server.
            keys: Only these leases; all of the server's when None.

        Returns:
            How many were forgotten.
        """
        with self.store._transaction() as cursor:
            if keys is None:
                cursor.execute("DELETE FROM source_lease_grants WHERE node = ?", (node,))
                return int(cursor.rowcount)
            count = 0
            for key in keys:
                cursor.execute(
                    "DELETE FROM source_lease_grants WHERE node = ? AND lease_key = ?", (node, key)
                )
                count += int(cursor.rowcount)
            return count
