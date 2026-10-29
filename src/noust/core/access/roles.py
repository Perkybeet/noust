# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the roles are kept: one list, and the roles each account holds.

A server has one list of roles (4.1, item 96). The five of every release and the protected
``owner`` are seeded; the operator edits them, duplicates them and puts them back, and defines
others. A row says what a role holds *now*:

- ``owner`` (kind ``protected``) holds every permission there is, whichever release wrote its row.
- A seeded role nobody has edited (``edited`` is 0) **follows Noust**: what it holds is what the
  running release ships for it, so a permission a new release adds reaches it by itself.
- A role the operator has edited holds exactly what was saved, and receives nothing in silence.
  ``base`` is what the release shipped for it when the operator last looked, so what a newer
  release ships beyond that is an *offer* (:meth:`RoleRecord.offers`), made by the API and
  taken or left by a person.
- A custom role holds what was saved.

The roles an account holds are ``account_roles``, in order. ``accounts.role`` is the first of the
five roles of 3.1 among them (``viewer`` when none is), kept for what still reads one role and
because its CHECK cannot name the others; it is never what decides what an account may do. This
module stores and finds; what a role may hold and who may be given it is
:class:`~noust.core.access.role_manager.RoleManager` and
:class:`~noust.core.accounts.manager.AccountManager`, the chokepoints (rule 4).
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.access.builtin import (
    OWNER_DESCRIPTION,
    OWNER_LABEL,
    ROLE_OWNER,
    SEEDED_ROLES,
    SHIPPED,
    SYSTEM_ROLES,
    everything,
)
from noust.core.access.model import AccessNotFoundError

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: What a row is. ``seeded`` ships with Noust and can be edited and put back; ``protected`` is the
#: owner; ``custom`` is the operator's own.
KIND_SEEDED = "seeded"
KIND_PROTECTED = "protected"
KIND_CUSTOM = "custom"

#: What an account with no role this server knows is worth: reading. A role deleted by hand must
#: never leave an account with nothing to be refused by, nor with more than it had.
FALLBACK_ROLE = "viewer"

#: Accounts that can still sign in, or will once a lockout ends: the owners that count for the
#: guard against locking everybody out.
USABLE_STATUSES = ("active", "locked")


def decode_permissions(raw: Any) -> frozenset[str]:
    """
    Args:
        raw: A permission list as stored.

    Returns:
        Its permissions; empty for anything unreadable, so a hand-edited row grants nothing
        rather than everything.
    """
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return frozenset()
    return frozenset(str(item) for item in value) if isinstance(value, list) else frozenset()


def encode_permissions(permissions: Iterable[str]) -> str:
    """
    Args:
        permissions: Permissions to store.

    Returns:
        Them as the sorted JSON list the table keeps.
    """
    return json.dumps(sorted(set(permissions)))


@dataclass(frozen=True)
class RoleRecord:
    """
    One role of the list, as it holds now.

    Attributes:
        name: Its slug, unique.
        kind: ``seeded``, ``protected`` or ``custom``.
        label: What the console shows.
        description: One sentence on what it is for.
        permissions: What it holds now, sorted (see the module docstring for what "now" means).
        stored: What the row itself keeps; differs from ``permissions`` for a role that follows
            Noust and for the owner.
        edited: Whether the operator has taken over a seeded role.
        base: What the release shipped for a seeded role when it was last reviewed.
        created_at: When it was made, UNIX seconds.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    kind: str
    label: str
    description: str
    permissions: tuple[str, ...]
    stored: tuple[str, ...]
    edited: bool
    base: tuple[str, ...]
    created_at: float
    created_by: str | None
    updated_at: float
    updated_by: str | None

    @property
    def held(self) -> frozenset[str]:
        """What it holds now, as a set."""
        return frozenset(self.permissions)

    @property
    def protected(self) -> bool:
        """Whether nobody edits or deletes it."""
        return self.kind == KIND_PROTECTED

    @property
    def seeded(self) -> bool:
        """Whether Noust ships it (the owner included)."""
        return self.kind in (KIND_SEEDED, KIND_PROTECTED)

    @property
    def follows_noust(self) -> bool:
        """Whether a permission a release adds reaches it by itself."""
        return self.kind == KIND_PROTECTED or (self.kind == KIND_SEEDED and not self.edited)

    @property
    def shipped(self) -> frozenset[str] | None:
        """What the running release ships for it; None for a custom role."""
        if self.kind == KIND_PROTECTED:
            return everything()
        shipped = SHIPPED.get(self.name)
        return shipped.permissions if shipped is not None and self.kind == KIND_SEEDED else None

    @property
    def offers(self) -> tuple[str, ...]:
        """
        What a newer release ships for an edited role that it does not hold yet.

        Returns:
            The permissions the running release ships for it beyond ``base`` that it lacks, sorted;
            empty for a role that follows Noust (it has them already) and for a custom role.
        """
        shipped = self.shipped
        if shipped is None or self.follows_noust:
            return ()
        return tuple(sorted(shipped - frozenset(self.base) - self.held))

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The role as the API and ``--json`` show it.
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "description": self.description,
            "permissions": list(self.permissions),
            "edited": self.edited,
            "follows_noust": self.follows_noust,
            "protected": self.protected,
            "offers": list(self.offers),
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class Holder:
    """
    An account that holds a role.

    Attributes:
        account_id: Its id.
        username: Its name.
        status: Its status as stored.
        primary: Whether the role is its own (``accounts.role``) rather than an added one.
    """

    account_id: int
    username: str
    status: str
    primary: bool


def seed(cursor: sqlite3.Cursor, now: float) -> None:
    """
    Make sure every role every server has is a row, inside the caller's transaction.

    Idempotent: a row that exists is left as it is, whatever the operator did to it.

    Args:
        cursor: A cursor inside the transaction.
        now: The time, UNIX seconds.
    """
    for name in SEEDED_ROLES:
        shipped = SHIPPED[name]
        cursor.execute(
            "INSERT OR IGNORE INTO roles (name, kind, label, description, permissions, edited, "
            "base, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, 'seeded', ?, ?, ?, 0, '[]', ?, 'noust', ?, 'noust')",
            (
                name,
                shipped.label,
                shipped.description,
                encode_permissions(shipped.permissions),
                now,
                now,
            ),
        )
    cursor.execute(
        "INSERT OR IGNORE INTO roles (name, kind, label, description, permissions, edited, "
        "base, created_at, created_by, updated_at, updated_by) "
        "VALUES (?, 'protected', ?, ?, ?, 0, '[]', ?, 'noust', ?, 'noust')",
        (ROLE_OWNER, OWNER_LABEL, OWNER_DESCRIPTION, encode_permissions(everything()), now, now),
    )


def record_of(row: sqlite3.Row) -> RoleRecord:
    """
    Args:
        row: A row of ``roles``.

    Returns:
        The role, with what it holds now worked out (see the module docstring).
    """
    name = str(row["name"])
    kind = str(row["kind"])
    stored = decode_permissions(row["permissions"])
    edited = bool(row["edited"])
    if kind == KIND_PROTECTED:
        held = everything()
    elif kind == KIND_SEEDED and not edited and name in SHIPPED:
        held = SHIPPED[name].permissions
    else:
        held = stored
    return RoleRecord(
        name=name,
        kind=kind,
        label=str(row["label"]),
        description=str(row["description"] or ""),
        permissions=tuple(sorted(held)),
        stored=tuple(sorted(stored)),
        edited=edited,
        base=tuple(sorted(decode_permissions(row["base"]))),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"],
    )


class RoleStore:
    """
    Read and write the list of roles and the roles accounts hold.

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

    def _all(self) -> list[sqlite3.Row]:
        """
        Every row of ``roles``, seeding the list first when a store reaches here without it (a
        store a development build of 4.1 stamped before this existed, or a test's hand-made one).

        The migrations seed it; this only makes the read after them certain.

        Returns:
            The rows.
        """
        rows = self._rows("SELECT * FROM roles")
        if any(row["name"] == ROLE_OWNER for row in rows):
            return rows
        with self.store._transaction() as cursor:
            seed(cursor, time.time())
        return self._rows("SELECT * FROM roles")

    # -------------------------------------------------------------------- roles

    def list_roles(self) -> list[RoleRecord]:
        """
        Returns:
            Every role: the ones Noust ships in their order, then the operator's by name.
        """
        found = [record_of(row) for row in self._all()]
        order = {name: index for index, name in enumerate(SYSTEM_ROLES)}
        return sorted(found, key=lambda role: (order.get(role.name, len(order)), role.name))

    def get(self, name: str) -> RoleRecord | None:
        """
        Args:
            name: A role's name.

        Returns:
            The role, or None.
        """
        wanted = (name or "").strip().lower()
        return next((record_of(row) for row in self._all() if row["name"] == wanted), None)

    def require(self, name: str) -> RoleRecord:
        """
        Args:
            name: A role's name.

        Returns:
            The role.

        Raises:
            AccessNotFoundError: When there is none.
        """
        found = self.get(name)
        if found is None:
            from noust.core.server_text import say

            raise AccessNotFoundError.from_text(
                say("roles.unknown", name=name, command="noust access role list"), field="role"
            )
        return found

    def names(self) -> list[str]:
        """
        Returns:
            The name of every role, in the order of :meth:`list_roles`.
        """
        return [role.name for role in self.list_roles()]

    def permission_map(self) -> dict[str, frozenset[str]]:
        """
        Returns:
            What every role holds now, by name: the one read behind every request.
        """
        return {role.name: role.held for role in self.list_roles()}

    def permissions_for(self, names: Iterable[str]) -> frozenset[str]:
        """
        What an account holds through its roles.

        Args:
            names: The roles of one account.

        Returns:
            The union of what each holds. A role this server does not have contributes nothing,
            and an account none of whose roles exists holds what :data:`FALLBACK_ROLE` does:
            never more than reading.
        """
        table = self.permission_map()
        held: set[str] = set()
        known = False
        for name in names:
            if name in table:
                known = True
                held |= table[name]
        return frozenset(held) if known else table.get(FALLBACK_ROLE, frozenset())

    def count_custom(self) -> int:
        """
        Returns:
            How many roles the operator has defined.
        """
        return sum(1 for row in self._all() if row["kind"] == KIND_CUSTOM)

    def insert_custom(
        self,
        name: str,
        label: str,
        description: str,
        permissions: Iterable[str],
        *,
        now: float,
        actor: str | None,
    ) -> None:
        """
        Write a new custom role.

        Args:
            name: Its validated slug.
            label: What the console shows.
            description: What it is for.
            permissions: Its validated permissions.
            now: The time, UNIX seconds.
            actor: Who makes it.

        Raises:
            sqlite3.IntegrityError: When the name is taken.
        """
        self._all()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO roles (name, kind, label, description, permissions, edited, base, "
                "created_at, created_by, updated_at, updated_by) "
                "VALUES (?, 'custom', ?, ?, ?, 0, '[]', ?, ?, ?, ?)",
                (name, label, description, encode_permissions(permissions), now, actor, now, actor),
            )

    def save(
        self,
        name: str,
        *,
        label: str,
        description: str,
        permissions: Iterable[str],
        base: Iterable[str] | None,
        edited: bool,
        now: float,
        actor: str | None,
    ) -> None:
        """
        Overwrite a role's definition.

        Args:
            name: The role.
            label: Its label.
            description: Its description.
            permissions: What it holds from now on.
            base: What the release ships for it as of this review, for a seeded role; None keeps
                what the row has.
            edited: Whether the operator has taken it over (a seeded role put back is 0).
            now: The time, UNIX seconds.
            actor: Who changes it.
        """
        self._all()
        with self.store._transaction() as cursor:
            if base is None:
                cursor.execute(
                    "UPDATE roles SET label = ?, description = ?, permissions = ?, edited = ?, "
                    "updated_at = ?, updated_by = ? WHERE name = ?",
                    (
                        label,
                        description,
                        encode_permissions(permissions),
                        int(edited),
                        now,
                        actor,
                        name,
                    ),
                )
            else:
                cursor.execute(
                    "UPDATE roles SET label = ?, description = ?, permissions = ?, edited = ?, "
                    "base = ?, updated_at = ?, updated_by = ? WHERE name = ?",
                    (
                        label,
                        description,
                        encode_permissions(permissions),
                        int(edited),
                        encode_permissions(base),
                        now,
                        actor,
                        name,
                    ),
                )

    def delete_custom(self, name: str) -> None:
        """
        Delete a custom role's row.

        Args:
            name: The role. Whether anybody holds it is the manager's question, asked first.
        """
        self._all()
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM roles WHERE name = ? AND kind = 'custom'", (name,))

    # ------------------------------------------------------------------ holders

    def holders(self, name: str) -> list[Holder]:
        """
        Args:
            name: A role.

        Returns:
            The accounts that hold it, first role or added one, by name. An account with no row
            in ``account_roles`` (inserted by hand) holds the role ``accounts.role`` names.
        """
        self._all()
        rows = self._rows(
            "SELECT a.id AS id, a.username AS username, a.status AS status, "
            "(r.position = 0) AS first_role FROM account_roles r "
            "JOIN accounts a ON a.id = r.account_id WHERE r.role = ? "
            "UNION "
            "SELECT a.id, a.username, a.status, 1 FROM accounts a WHERE a.role = ? "
            "AND NOT EXISTS (SELECT 1 FROM account_roles r WHERE r.account_id = a.id) "
            "ORDER BY username",
            (name, name),
        )
        return [
            Holder(int(row["id"]), str(row["username"]), str(row["status"]), bool(row[3]))
            for row in rows
        ]

    def bound_count(self, name: str) -> int:
        """
        Args:
            name: A role.

        Returns:
            How many bindings at a team, a project or an environment name it.
        """
        return int(self._rows("SELECT COUNT(*) FROM access_bindings WHERE role = ?", (name,))[0][0])

    def roles_of(self, account_id: int) -> list[str]:
        """
        Args:
            account_id: An account.

        Returns:
            The roles it holds, in order; empty for an account with no row (its ``accounts.role``
            is then its one role).
        """
        rows = self._rows(
            "SELECT role FROM account_roles WHERE account_id = ? ORDER BY position, role",
            (account_id,),
        )
        return [str(row[0]) for row in rows]

    def usable_owners(self) -> list[Holder]:
        """
        Returns:
            The accounts that hold ``owner`` and can sign in (or will when a lockout ends).
        """
        return [h for h in self.holders(ROLE_OWNER) if h.status in USABLE_STATUSES]


def replace_roles(
    cursor: sqlite3.Cursor, account_id: int, roles: Iterable[str], *, now: float, actor: str | None
) -> None:
    """
    Set every role an account holds, inside the caller's transaction.

    Args:
        cursor: A cursor inside the transaction.
        account_id: The account.
        roles: Its roles in order, already validated; the first is the one it is known by.
        now: The time, UNIX seconds.
        actor: Who gives them.
    """
    keep = list(dict.fromkeys(roles))
    held = {
        str(row[0]): (float(row[1]), row[2])
        for row in cursor.execute(
            "SELECT role, created_at, created_by FROM account_roles WHERE account_id = ?",
            (account_id,),
        ).fetchall()
    }
    cursor.execute("DELETE FROM account_roles WHERE account_id = ?", (account_id,))
    for position, role in enumerate(keep):
        # A role already held keeps the date and the person that gave it.
        given_at, given_by = held.get(role, (now, actor))
        cursor.execute(
            "INSERT INTO account_roles (account_id, role, position, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (account_id, role, position, given_at, given_by),
        )


def mirror_of(roles: Iterable[str]) -> str:
    """
    Args:
        roles: An account's roles, in order.

    Returns:
        What ``accounts.role`` says for them: the first of the five roles of 3.1 among them, and
        ``viewer`` when there is none (the owner alone, or custom roles), the least a reader of
        that column can mistake the account for.
    """
    return next((role for role in roles if role in SEEDED_ROLES), FALLBACK_ROLE)
