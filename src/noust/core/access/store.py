# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where custom roles and role bindings are kept.

The one reader and writer of the ``custom_roles`` and ``access_bindings``
tables, through the store's own connection and transaction (the way
:class:`~noust.core.teams.TeamStore` reaches the teams) so ``--dry-run``
rehearses a change as it rehearses any other write. Rules about *what a role
may hold and who may be given it* are not here: this module stores and finds,
:mod:`noust.web.permissions.access` decides.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Any

from noust.core.access.model import (
    INSTANCE_SCOPE_ID,
    SCOPE_INSTANCE,
    SCOPE_PROJECT,
    SCOPE_TEAM,
    AccessConflictError,
    AccessNotFoundError,
    Binding,
    CustomRole,
)

if TYPE_CHECKING:
    from noust.core.store import NoustStore

_BINDING_SELECT = (
    "SELECT b.id, b.account_id, a.username, b.scope_kind, b.scope_id, b.role, b.created_at, "
    "b.created_by FROM access_bindings b JOIN accounts a ON a.id = b.account_id"
)


def _role(row: sqlite3.Row) -> CustomRole:
    """
    Args:
        row: A row of ``custom_roles``.

    Returns:
        The role. A permission list that is not readable is an empty one: a
        hand-edited row grants nothing rather than everything.
    """
    try:
        raw = json.loads(row["permissions"] or "[]")
    except (TypeError, ValueError):
        raw = []
    permissions = sorted({str(item) for item in raw}) if isinstance(raw, list) else []
    return CustomRole(
        name=str(row["name"]),
        label=str(row["label"]),
        description=str(row["description"] or ""),
        permissions=tuple(permissions),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"],
    )


class AccessStore:
    """
    Read and write custom roles and bindings.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store the roles live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT.
            params: Its parameters.

        Returns:
            The rows.
        """
        return list(self.store._get_connection().execute(sql, params).fetchall())

    # -------------------------------------------------------------------- roles

    def list_roles(self) -> list[CustomRole]:
        """
        Returns:
            Every custom role, by name.
        """
        return [_role(row) for row in self._rows("SELECT * FROM custom_roles ORDER BY name")]

    def get_role(self, name: str) -> CustomRole | None:
        """
        Args:
            name: A custom role's name.

        Returns:
            The role, or None.
        """
        rows = self._rows("SELECT * FROM custom_roles WHERE name = ?", ((name or "").lower(),))
        return _role(rows[0]) if rows else None

    def require_role(self, name: str) -> CustomRole:
        """
        Args:
            name: A custom role's name.

        Returns:
            The role.

        Raises:
            AccessNotFoundError: When there is none.
        """
        found = self.get_role(name)
        if found is None:
            raise AccessNotFoundError(
                f"No custom role named {name!r}",
                details="List the roles with 'noust access role list'.",
                field="role",
            )
        return found

    def count_roles(self) -> int:
        """
        Returns:
            How many custom roles there are.
        """
        return int(self._rows("SELECT COUNT(*) FROM custom_roles")[0][0])

    def insert_role(
        self,
        name: str,
        label: str,
        description: str,
        permissions: list[str],
        *,
        now: float,
        actor: str | None,
    ) -> CustomRole:
        """
        Write a new custom role.

        Args:
            name: Its validated slug.
            label: What the console shows.
            description: What it is for.
            permissions: Its validated, sorted permissions.
            now: The time, UNIX seconds.
            actor: Who makes it.

        Returns:
            The role.

        Raises:
            AccessConflictError: When the name is taken.
        """
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO custom_roles (name, label, description, permissions, "
                    "created_at, created_by, updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, label, description, json.dumps(permissions), now, actor, now, actor),
                )
        except sqlite3.IntegrityError as exc:
            raise AccessConflictError(
                f"A role named {name!r} already exists",
                details="Change it with 'noust access role set', or pick another name.",
                field="name",
            ) from exc
        return self.require_role(name)

    def update_role(
        self,
        name: str,
        *,
        label: str | None,
        description: str | None,
        permissions: list[str] | None,
        now: float,
        actor: str | None,
    ) -> CustomRole:
        """
        Change a custom role; what is None stays.

        Args:
            name: The role.
            label: The new label.
            description: The new description.
            permissions: The new validated, sorted permissions.
            now: The time, UNIX seconds.
            actor: Who changes it.

        Returns:
            The role as it is now.

        Raises:
            AccessNotFoundError: When there is no such role.
        """
        current = self.require_role(name)
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE custom_roles SET label = ?, description = ?, permissions = ?, "
                "updated_at = ?, updated_by = ? WHERE name = ?",
                (
                    current.label if label is None else label,
                    current.description if description is None else description,
                    json.dumps(list(current.permissions) if permissions is None else permissions),
                    now,
                    actor,
                    current.name,
                ),
            )
        return self.require_role(name)

    def delete_role(self, name: str) -> CustomRole:
        """
        Delete a custom role that nobody holds.

        Args:
            name: The role.

        Returns:
            The role as it was.

        Raises:
            AccessNotFoundError: When there is no such role.
            AccessConflictError: When an account still holds it.
        """
        current = self.require_role(name)
        holders = self._rows(
            "SELECT COUNT(*) FROM access_bindings WHERE role = ?", (current.name,)
        )[0][0]
        if holders:
            raise AccessConflictError(
                f"The role {current.name!r} is held by {holders} binding(s)",
                details="Take it from the accounts that hold it first: 'noust access list'.",
            )
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM custom_roles WHERE name = ?", (current.name,))
        return current

    # ----------------------------------------------------------------- bindings

    def scope_label(self, kind: str, scope_id: int) -> str:
        """
        Args:
            kind: A scope kind.
            scope_id: The row it is.

        Returns:
            The scope as people write it; empty for the server and for a scope
            that no longer exists.
        """
        if kind == SCOPE_INSTANCE:
            return ""
        if kind == SCOPE_TEAM:
            rows = self._rows("SELECT slug FROM teams WHERE id = ?", (scope_id,))
            return str(rows[0][0]) if rows else ""
        if kind == SCOPE_PROJECT:
            rows = self._rows(
                "SELECT t.slug, p.slug FROM projects p JOIN teams t ON t.id = p.team_id "
                "WHERE p.id = ?",
                (scope_id,),
            )
            return f"{rows[0][0]}/{rows[0][1]}" if rows else ""
        rows = self._rows(
            "SELECT t.slug, p.slug, e.slug FROM environments e JOIN projects p ON p.id = "
            "e.project_id JOIN teams t ON t.id = p.team_id WHERE e.id = ?",
            (scope_id,),
        )
        return f"{rows[0][0]}/{rows[0][1]}/{rows[0][2]}" if rows else ""

    def _binding(self, row: sqlite3.Row) -> Binding:
        """
        Args:
            row: A row selected with :data:`_BINDING_SELECT`.

        Returns:
            The binding, with its scope spelled out.
        """
        kind = str(row["scope_kind"])
        scope_id = int(row["scope_id"])
        return Binding(
            id=int(row["id"]),
            account_id=int(row["account_id"]),
            username=str(row["username"]),
            scope_kind=kind,
            scope_id=scope_id,
            scope=self.scope_label(kind, scope_id),
            role=str(row["role"]),
            created_at=float(row["created_at"]),
            created_by=row["created_by"],
        )

    def list_bindings(
        self,
        *,
        account_id: int | None = None,
        scope_kind: str | None = None,
        scope_id: int | None = None,
        role: str | None = None,
    ) -> list[Binding]:
        """
        Args:
            account_id: Only this account's.
            scope_kind: Only this kind of scope.
            scope_id: Only this scope.
            role: Only this role's.

        Returns:
            The bindings, by account and then by scope.
        """
        where = ["1 = 1"]
        params: list[Any] = []
        for column, value in (
            ("b.account_id", account_id),
            ("b.scope_kind", scope_kind),
            ("b.scope_id", scope_id),
            ("b.role", role),
        ):
            if value is not None:
                where.append(f"{column} = ?")
                params.append(value)
        sql = f"{_BINDING_SELECT} WHERE {' AND '.join(where)} ORDER BY a.username, b.scope_kind, b.scope_id"
        return [self._binding(row) for row in self._rows(sql, tuple(params))]

    def grants_of(self, account_id: int) -> list[tuple[str, int, str]]:
        """
        What an account holds, without spelling the scopes out.

        The lean read of every request: a payload learns its grants here, so no
        label is looked up.

        Args:
            account_id: The account.

        Returns:
            ``(scope_kind, scope_id, role)`` for each of its bindings.
        """
        rows = self._rows(
            "SELECT scope_kind, scope_id, role FROM access_bindings WHERE account_id = ?",
            (account_id,),
        )
        return [(str(row[0]), int(row[1]), str(row[2])) for row in rows]

    def get_binding(self, binding_id: int) -> Binding | None:
        """
        Args:
            binding_id: A binding's id.

        Returns:
            The binding, or None.
        """
        rows = self._rows(f"{_BINDING_SELECT} WHERE b.id = ?", (binding_id,))
        return self._binding(rows[0]) if rows else None

    def require_binding(self, binding_id: int) -> Binding:
        """
        Args:
            binding_id: A binding's id.

        Returns:
            The binding.

        Raises:
            AccessNotFoundError: When there is none.
        """
        found = self.get_binding(binding_id)
        if found is None:
            raise AccessNotFoundError(
                f"No access binding with id {binding_id}",
                details="List them with 'noust access list'.",
                field="binding",
            )
        return found

    def put_binding(
        self,
        account_id: int,
        scope_kind: str,
        scope_id: int,
        role: str,
        *,
        now: float,
        actor: str | None,
    ) -> Binding:
        """
        Give an account a role at a scope, replacing the one it had there.

        Args:
            account_id: The account.
            scope_kind: One of the scope kinds.
            scope_id: The scope's row (0 for the server).
            role: A validated role.
            now: The time, UNIX seconds.
            actor: Who gives it.

        Returns:
            The binding.
        """
        if scope_kind == SCOPE_INSTANCE:
            scope_id = INSTANCE_SCOPE_ID
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO access_bindings (account_id, scope_kind, scope_id, role, "
                "created_at, created_by) VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(account_id, scope_kind, scope_id) DO UPDATE SET "
                "role = excluded.role, created_at = excluded.created_at, "
                "created_by = excluded.created_by",
                (account_id, scope_kind, scope_id, role, now, actor),
            )
            cursor.execute(
                "SELECT id FROM access_bindings WHERE account_id = ? AND scope_kind = ? "
                "AND scope_id = ?",
                (account_id, scope_kind, scope_id),
            )
            binding_id = int(cursor.fetchone()[0])
        return self.require_binding(binding_id)

    def delete_binding(self, binding_id: int) -> Binding:
        """
        Take a role away.

        Args:
            binding_id: The binding's id.

        Returns:
            The binding as it was.

        Raises:
            AccessNotFoundError: When there is none.
        """
        found = self.require_binding(binding_id)
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM access_bindings WHERE id = ?", (binding_id,))
        return found

    def projects_of_team(self, team_id: int) -> list[int]:
        """
        Args:
            team_id: A team's id.

        Returns:
            Its projects' ids.
        """
        return [
            int(r[0]) for r in self._rows("SELECT id FROM projects WHERE team_id = ?", (team_id,))
        ]

    def environments_of_team(self, team_id: int) -> list[int]:
        """
        Args:
            team_id: A team's id.

        Returns:
            The ids of the environments of its projects.
        """
        rows = self._rows(
            "SELECT e.id FROM environments e JOIN projects p ON p.id = e.project_id "
            "WHERE p.team_id = ?",
            (team_id,),
        )
        return [int(r[0]) for r in rows]

    def environments_of_project(self, project_id: int) -> list[int]:
        """
        Args:
            project_id: A project's id.

        Returns:
            Its environments' ids.
        """
        rows = self._rows("SELECT id FROM environments WHERE project_id = ?", (project_id,))
        return [int(r[0]) for r in rows]

    def project_of_environment(self, environment_id: int) -> int | None:
        """
        Args:
            environment_id: An environment's id.

        Returns:
            The project it belongs to, or None.
        """
        rows = self._rows("SELECT project_id FROM environments WHERE id = ?", (environment_id,))
        return int(rows[0][0]) if rows else None

    def team_of_project(self, project_id: int) -> int | None:
        """
        Args:
            project_id: A project's id.

        Returns:
            The team it belongs to, or None.
        """
        rows = self._rows("SELECT team_id FROM projects WHERE id = ?", (project_id,))
        return int(rows[0][0]) if rows else None
