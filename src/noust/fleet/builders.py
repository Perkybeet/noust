# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server that builds an application for the others (checklist A-49).

A small VPS is a poor place to compile an application: a Next.js build wants more memory than the
application ever will. A central that manages several servers can have one of them build, and the
rest receive the release it built. This is the central's record of which: one builder per
application, a server of this central. The application and its build settings stay on the servers;
the record holds nothing but the choice, like a placement.

Choosing it changes nothing on any server. The release moves when an operator ships
(:mod:`noust.web.fleet_ship_actions`), and the builder must have the application registered, with the
same type as the servers that run it: shipping says so before it builds anything.

:class:`Builders` is the only reader and writer of ``fleet_builders`` (schema v14).
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NodeError
from noust.core.store import NoustStore, get_store
from noust.fleet.models import validate_node_name
from noust.fleet.placements import checked_domain

__all__ = ["Builder", "Builders"]


@dataclass(frozen=True)
class Builder:
    """
    The server that builds one application.

    Attributes:
        domain: The application.
        node: The server that builds it.
        created_at: When it was recorded, ISO 8601 UTC.
        created_by: Who recorded it.
        updated_at: When it last changed.
    """

    domain: str
    node: str
    created_at: str
    created_by: str | None
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            Every field, JSON-ready.
        """
        return {
            "domain": self.domain,
            "node": self.node,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
        }


class Builders:
    """
    Read and write which server builds which application.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def list(self) -> builtins.list[Builder]:
        """
        Returns:
            Every builder, by domain.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM fleet_builders ORDER BY domain")
            .fetchall()
        )
        return [self._read(row) for row in rows]

    def get(self, domain: str) -> Builder | None:
        """
        Read the builder of an application.

        Args:
            domain: The application's domain.

        Returns:
            The builder, or None when the application is built where it runs.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM fleet_builders WHERE domain = ?", (domain.strip().lower(),))
            .fetchone()
        )
        return self._read(row) if row is not None else None

    def set(self, domain: str, node: str, *, actor: str | None = None) -> Builder:
        """
        Choose the server that builds an application.

        Args:
            domain: The application.
            node: The server that builds it.
            actor: Who records it.

        Returns:
            The builder as it is now.

        Raises:
            ValidationError: When the domain or the name is not one.
            NodeError: When the server is not registered on this central.
        """
        key = checked_domain(domain)
        name = validate_node_name(node)
        if self.store.get_node(name) is None:
            raise NodeError(
                f"No node named {name} is registered on this central",
                details="List them with 'noust node list'.",
                field="node",
            )
        now = _now()
        current = self.get(key)
        builder = Builder(
            domain=key,
            node=name,
            created_at=current.created_at if current else now,
            created_by=current.created_by if current else actor,
            updated_at=now,
        )
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO fleet_builders (domain, node, created_at, created_by, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET node = excluded.node, "
                "updated_at = excluded.updated_at",
                (
                    builder.domain,
                    builder.node,
                    builder.created_at,
                    builder.created_by,
                    builder.updated_at,
                ),
            )
        return builder

    def clear(self, domain: str) -> bool:
        """
        Go back to building an application where it runs. Nothing is touched on any server.

        Args:
            domain: The application.

        Returns:
            True when it had a builder.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM fleet_builders WHERE domain = ?", (domain.strip().lower(),))
            return cursor.rowcount > 0

    @staticmethod
    def _read(row: Any) -> Builder:
        """
        Args:
            row: A ``fleet_builders`` row.

        Returns:
            The builder.
        """
        return Builder(
            domain=row["domain"],
            node=row["node"],
            created_at=row["created_at"],
            created_by=row["created_by"],
            updated_at=row["updated_at"],
        )


def _now() -> str:
    """Returns: The current time, ISO 8601 UTC."""
    return datetime.now(timezone.utc).isoformat()
