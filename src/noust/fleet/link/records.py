# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The links of a central: which servers dial in, and on which loopback port each listens.

One row per server (``node_links``). The row is created by the invitation, holds
the port its link listens on from then on, and becomes ``active`` when the
server's code is accepted. Nothing secret is here: the server's fleet token is in
the secret store like every node's, and the key is the public half.
"""

from __future__ import annotations

import builtins
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NodeError
from noust.core.store import NoustStore, get_store

#: The server was invited and has not answered yet.
INVITED = "invited"

#: The server's code was accepted: its key is installed and it is registered.
ACTIVE = "active"

#: What a link's state can be.
STATES = (INVITED, ACTIVE)

#: The loopback ports links listen on, first and last. Kept apart from the
#: ephemeral range the tunnels use, so a port a link will want is not taken by
#: an outgoing connection.
PORT_FIRST = 24000
PORT_LAST = 24999


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class LinkRecord:
    """
    One server's link.

    Attributes:
        node: The server's name on this central.
        port: The loopback port on this machine its link listens on.
        state: :data:`INVITED` or :data:`ACTIVE`.
        nonce: The invitation's nonce; the server's code must carry it.
        central_host: The address the server was told to dial.
        central_port: The SSH port it was told to dial.
        link_key: The public key line the server dials in with, once accepted.
        created_at: When it was invited.
        created_by: Who invited it.
        activated_at: When its code was accepted.
    """

    node: str
    port: int
    state: str
    nonce: str
    central_host: str
    central_port: int
    link_key: str | None
    created_at: str
    created_by: str | None
    activated_at: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the link for the API and ``--json``; the nonce stays out.

        Returns:
            Plain data.
        """
        return {
            "node": self.node,
            "port": self.port,
            "state": self.state,
            "central_host": self.central_host,
            "central_port": self.central_port,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "activated_at": self.activated_at,
        }


def _record(row: sqlite3.Row) -> LinkRecord:
    """
    Build a record out of its row.

    Args:
        row: A ``node_links`` row.

    Returns:
        The record.
    """
    return LinkRecord(
        node=str(row["node"]),
        port=int(row["port"]),
        state=str(row["state"]),
        nonce=str(row["nonce"]),
        central_host=str(row["central_host"]),
        central_port=int(row["central_port"]),
        link_key=str(row["link_key"]) if row["link_key"] is not None else None,
        created_at=str(row["created_at"]),
        created_by=str(row["created_by"]) if row["created_by"] is not None else None,
        activated_at=str(row["activated_at"]) if row["activated_at"] is not None else None,
    )


class LinkRecords:
    """
    Read and write the links of one central.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get(self, node: str) -> LinkRecord | None:
        """
        Read one server's link.

        Args:
            node: The server.

        Returns:
            The link, or None when the server does not dial in.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM node_links WHERE node = ?", (node,))
            .fetchone()
        )
        return _record(row) if row is not None else None

    def list(self) -> builtins.list[LinkRecord]:
        """
        Returns:
            Every link, by server name.
        """
        rows = self.store._get_connection().execute("SELECT * FROM node_links ORDER BY node")
        return [_record(row) for row in rows.fetchall()]

    def used_ports(self) -> set[int]:
        """
        Returns:
            The ports links hold, active or invited.
        """
        rows = self.store._get_connection().execute("SELECT port FROM node_links").fetchall()
        return {int(row["port"]) for row in rows}

    def invite(
        self,
        node: str,
        *,
        port: int,
        nonce: str,
        central_host: str,
        central_port: int,
        actor: str | None,
        created_at: str | None = None,
    ) -> LinkRecord:
        """
        Record an invitation, replacing one that was never answered.

        Args:
            node: The server's name.
            port: The loopback port its link will listen on.
            nonce: The invitation's nonce.
            central_host: The address the server is told to dial.
            central_port: The SSH port it is told to dial.
            actor: Who invites it.
            created_at: When, ISO 8601 UTC; now when None.

        Returns:
            The record.

        Raises:
            NodeError: When the server already dials in, or the port is another's.
        """
        existing = self.get(node)
        if existing is not None and existing.state == ACTIVE:
            raise NodeError(
                f"{node} already dials in to this central",
                details=f"Remove it first: noust node remove {node}",
                field="name",
            )
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO node_links (node, port, state, nonce, central_host, "
                    "central_port, link_key, created_at, created_by, activated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL) ON CONFLICT(node) DO UPDATE SET "
                    "port = excluded.port, state = excluded.state, nonce = excluded.nonce, "
                    "central_host = excluded.central_host, central_port = excluded.central_port, "
                    "link_key = NULL, created_at = excluded.created_at, "
                    "created_by = excluded.created_by, activated_at = NULL",
                    (
                        node,
                        port,
                        INVITED,
                        nonce,
                        central_host,
                        central_port,
                        created_at or _now(),
                        actor,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise NodeError(
                f"The port {port} is already another server's link",
                details="Choose another with --port, or leave it out to be given one.",
                field="port",
            ) from exc
        record = self.get(node)
        if record is None:
            raise NodeError(f"The invitation of {node} was not recorded", field="name")
        return record

    def activate(self, node: str, link_key: str) -> None:
        """
        Mark a link accepted, with the key the server dials in with.

        Args:
            node: The server.
            link_key: The public key line.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE node_links SET state = ?, link_key = ?, activated_at = ? WHERE node = ?",
                (ACTIVE, link_key, _now(), node),
            )

    def deactivate(self, node: str) -> None:
        """
        Put an accepted link back to invited: its code can be pasted again.

        Args:
            node: The server.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE node_links SET state = ?, link_key = NULL, activated_at = NULL "
                "WHERE node = ?",
                (INVITED, node),
            )

    def delete(self, node: str) -> None:
        """
        Forget a server's link.

        Args:
            node: The server.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM node_links WHERE node = ?", (node,))
