# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The machines this central created at a cloud provider, and how far each got.

A row is written the moment the provider has answered the create call, before
anything else is attempted, so a machine that costs money is on record whatever
happens next: the job dying, the host key never matching, the enrollment failing.
It stays after the server leaves the fleet (state ``deleted``), as the history of
what was created and when.

Nothing secret is in a row: not the token, not the one-time key, not the host
key. :class:`CloudServers` is the only reader and writer of ``cloud_servers``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NodeError, ValidationError
from noust.core.store import NoustStore, get_store

#: The machine was asked for and the provider has answered.
CREATING = "creating"
#: It runs, and its host key is being waited for.
BOOTING = "booting"
#: The central is installing Noust on it and joining it.
ENROLLING = "enrolling"
#: It is in the fleet.
READY = "ready"
#: The creation stopped before it joined; the machine may still exist.
FAILED = "failed"
#: The machine was deleted at the provider.
DELETED = "deleted"

STATES = (CREATING, BOOTING, ENROLLING, READY, FAILED, DELETED)

#: Longest error kept in a row.
MAX_ERROR = 2000


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class CloudServer:
    """
    A machine created at a provider.

    Attributes:
        node: The name the server has (or was to have) on this central.
        account: The cloud account it was created with.
        provider: The provider's key.
        provider_id: The provider's id for the machine.
        address: The address the central reaches it on.
        location: Where it is.
        plan: Its size.
        image: What it started from.
        state: One of :data:`STATES`.
        error: Why it stopped, when it failed.
        job_id: The job that created it.
        created_at: When the provider answered, ISO 8601 UTC.
        created_by: Who asked.
        updated_at: When the row last changed.
    """

    node: str
    account: str
    provider: str
    provider_id: str
    address: str | None
    location: str | None
    plan: str | None
    image: str | None
    state: str
    error: str | None
    job_id: str | None
    created_at: str
    created_by: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "node": self.node,
            "account": self.account,
            "provider": self.provider,
            "provider_id": self.provider_id,
            "address": self.address,
            "location": self.location,
            "plan": self.plan,
            "image": self.image,
            "state": self.state,
            "error": self.error,
            "job_id": self.job_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
        }


class CloudServers:
    """
    Read and write the record of the machines this central created.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def list(self) -> list[CloudServer]:
        """
        Returns:
            Every machine, the newest first.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM cloud_servers ORDER BY created_at DESC, node")
            .fetchall()
        )
        return [_server(row) for row in rows]

    def find(self, node: str) -> CloudServer | None:
        """
        Read one machine's record.

        Args:
            node: The server's name on this central.

        Returns:
            The record, or None.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM cloud_servers WHERE node = ?", (node,))
            .fetchone()
        )
        return _server(row) if row is not None else None

    def get(self, node: str) -> CloudServer:
        """
        Read one machine's record.

        Args:
            node: The server's name on this central.

        Returns:
            The record.

        Raises:
            NodeError: When this central did not create a machine under that name.
        """
        found = self.find(node)
        if found is None:
            raise NodeError(
                f"This central created no machine named {node}",
                details="List them with 'noust fleet cloud server list'.",
                field="node",
            )
        return found

    def holder_of(self, account: str, provider_id: str) -> CloudServer | None:
        """
        Find the server a machine is already on record for.

        Args:
            account: The cloud account.
            provider_id: The provider's id for the machine.

        Returns:
            The record that holds it, unless that machine was deleted; None otherwise.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM cloud_servers WHERE account = ? AND provider_id = ? AND state != ?",
                (account, provider_id, DELETED),
            )
            .fetchone()
        )
        return _server(row) if row is not None else None

    def forget(self, node: str) -> bool:
        """
        Forget the record of a machine. Nothing is touched at the provider.

        Args:
            node: The server's name on this central.

        Returns:
            True when there was a record.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM cloud_servers WHERE node = ?", (node,))
            return cursor.rowcount > 0

    def record(
        self,
        *,
        node: str,
        account: str,
        provider: str,
        provider_id: str,
        location: str | None,
        plan: str | None,
        image: str | None,
        address: str | None = None,
        job_id: str | None = None,
        created_by: str | None = None,
    ) -> CloudServer:
        """
        Write down a machine the provider has just created.

        A name used by an earlier machine that was deleted is taken over: the
        history of that one is replaced by this.

        Args:
            node: The name the server will have.
            account: The cloud account.
            provider: The provider's key.
            provider_id: The provider's id.
            location: Where it is.
            plan: Its size.
            image: What it started from.
            address: Its address, when already known.
            job_id: The job that creates it.
            created_by: Who asked.

        Returns:
            The record, in state :data:`CREATING`.

        Raises:
            NodeError: When a machine of that name is still on record.
        """
        existing = self.find(node)
        if existing is not None and existing.state != DELETED:
            raise NodeError(
                f"A machine named {node} is already on record ({existing.state})",
                details="Delete it first, or choose another name.",
                field="name",
            )
        now = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO cloud_servers (node, account, provider, provider_id, "
                "address, location, plan, image, state, error, job_id, created_at, created_by, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)",
                (
                    node,
                    account,
                    provider,
                    provider_id,
                    address,
                    location,
                    plan,
                    image,
                    CREATING,
                    job_id,
                    now,
                    created_by,
                    now,
                ),
            )
        return self.get(node)

    def update(
        self,
        node: str,
        *,
        state: str | None = None,
        address: str | None = None,
        error: str | None = None,
    ) -> CloudServer:
        """
        Change how far a machine got.

        Args:
            node: The server's name.
            state: The new state, one of :data:`STATES`.
            address: Its address, once known.
            error: Why it failed; set with :data:`FAILED` and cleared by any other state.

        Returns:
            The record.

        Raises:
            ValidationError: When the state is unknown.
            NodeError: When there is no such record.
        """
        current = self.get(node)
        if state is not None and state not in STATES:
            raise ValidationError(
                f"Unknown state: {state!r}", details=f"It is one of {', '.join(STATES)}."
            )
        new_state = state or current.state
        new_error = (error[:MAX_ERROR] if error else None) if new_state == FAILED else None
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE cloud_servers SET state = ?, address = ?, error = ?, updated_at = ? "
                "WHERE node = ?",
                (new_state, address or current.address, new_error, _now(), node),
            )
        return self.get(node)


def _server(row: Any) -> CloudServer:
    """
    Build a record out of its row.

    Args:
        row: A ``cloud_servers`` row.

    Returns:
        The record.
    """
    return CloudServer(
        node=str(row["node"]),
        account=str(row["account"]),
        provider=str(row["provider"]),
        provider_id=str(row["provider_id"]),
        address=row["address"],
        location=row["location"],
        plan=row["plan"],
        image=row["image"],
        state=str(row["state"]),
        error=row["error"],
        job_id=row["job_id"],
        created_at=str(row["created_at"]),
        created_by=row["created_by"],
        updated_at=str(row["updated_at"]),
    )
