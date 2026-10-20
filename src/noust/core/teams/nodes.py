# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which team a server of the fleet is assigned to.

A central's servers used to be the instance's alone: no team could see one or
choose it as a place to run. A server can now be *assigned* to a team. The team
then sees it, and works on it through the central within the permissions its
roles hold (:mod:`noust.web.api.scope` holds every call to that); a server nobody
took stays the instance's.

:class:`NodeAssignments` is the one reader and writer of ``node_teams``. The
assignment is a table of its own and not a column of ``nodes``: the registry
rewrites its columns every time a node answers, and an owner must survive that.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from noust.core.teams.model import TeamNotFoundError
from noust.core.teams.repository import TeamStore

if TYPE_CHECKING:
    from noust.core.store import NoustStore


@dataclass(frozen=True)
class NodeAssignment:
    """
    A server and the team it belongs to.

    Attributes:
        node: The node's name on this central.
        team_id: The team's id.
        team: The team's slug.
        assigned_at: When it was assigned, ISO 8601 UTC.
        assigned_by: Who assigned it.
    """

    node: str
    team_id: int
    team: str
    assigned_at: str
    assigned_by: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        """
        Returns:
            The assignment as plain data.
        """
        return {
            "node": self.node,
            "team": self.team,
            "assigned_at": self.assigned_at,
            "assigned_by": self.assigned_by,
        }


_SELECT = (
    "SELECT n.node, n.team_id, t.slug AS team_slug, n.assigned_at, n.assigned_by "
    "FROM node_teams n JOIN teams t ON t.id = n.team_id"
)


class NodeAssignments:
    """
    Read and write the team of each server.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store the assignments live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def _read(self, sql: str, params: tuple[object, ...] = ()) -> list[NodeAssignment]:
        rows = self.store._get_connection().execute(sql, params).fetchall()
        return [
            NodeAssignment(
                node=str(row["node"]),
                team_id=int(row["team_id"]),
                team=str(row["team_slug"]),
                assigned_at=str(row["assigned_at"]),
                assigned_by=row["assigned_by"],
            )
            for row in rows
        ]

    def list(self, *, team: str | None = None) -> list[NodeAssignment]:
        """
        Args:
            team: Only this team's servers.

        Returns:
            The assignments, by node name.
        """
        if team is not None:
            return self._read(
                f"{_SELECT} WHERE t.slug = ? ORDER BY n.node",
                (team.strip().lower(),),
            )
        return self._read(f"{_SELECT} ORDER BY n.node")

    def get(self, node: str) -> NodeAssignment | None:
        """
        Args:
            node: A node's name.

        Returns:
            Its assignment, or None when nobody took it.
        """
        found = self._read(f"{_SELECT} WHERE n.node = ?", (node,))
        return found[0] if found else None

    def team_of(self, node: str) -> str | None:
        """
        Args:
            node: A node's name.

        Returns:
            The slug of the team it belongs to, or None.
        """
        found = self.get(node)
        return found.team if found is not None else None

    def assign(self, node: str, team: str, *, actor: str | None = None) -> NodeAssignment:
        """
        Give a server to a team, taking it from the one that had it.

        Args:
            node: The node's name.
            team: The team's slug.
            actor: Who is assigning it.

        Returns:
            The assignment.

        Raises:
            TeamNotFoundError: When the node is not registered on this central
                or there is no such team.
        """
        owner = TeamStore(self.store).require_team(team)
        if self.store.get_node(node) is None:
            raise TeamNotFoundError(
                f"No server named {node!r} is registered on this central",
                details="List them with 'noust node list'.",
                field="node",
            )
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO node_teams (node, team_id, assigned_at, assigned_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(node) DO UPDATE SET team_id = excluded.team_id, "
                "assigned_at = excluded.assigned_at, assigned_by = excluded.assigned_by",
                (node, owner.id, stamp, actor),
            )
        assigned = self.get(node)
        if assigned is None:  # pragma: no cover - written in the transaction above
            raise TeamNotFoundError(f"The server {node!r} could not be assigned")
        return assigned

    def unassign(self, node: str) -> bool:
        """
        Give a server back to the instance.

        Args:
            node: The node's name.

        Returns:
            Whether it had a team.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM node_teams WHERE node = ?", (node,))
            return cursor.rowcount > 0

    def count_for_team(self, team_id: int) -> int:
        """
        Args:
            team_id: A team's id.

        Returns:
            How many servers it has.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT COUNT(*) FROM node_teams WHERE team_id = ?", (team_id,))
            .fetchone()
        )
        return int(row[0])
