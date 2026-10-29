# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data step of the move to one list of roles: what an existing server is given.

:mod:`noust.core.schema_v16` only declares the tables. This is the other half, run by the store's
migration to v16 (and by a store stamped v16 before the roles had theirs), inside the migration's
own transaction. Its one promise is that **no account gains or loses a permission**: whatever an
account could do before, it can do now, and nothing more. It is idempotent: once the list has
rows, running it again only adds a role that is missing.

1. The six roles every server has are seeded.
2. On a server where the ``roles_split`` control was in force, the administrator is saved as it
   resulted there (without the five permissions of governing), marked as edited, so it stays
   what it was and does not start to follow Noust's wider one. ``roles_split`` is not a cut made
   behind the roles' backs any more.
3. The custom roles become rows of the list, as they were.
4. Every account gets its roles in the list: its own, then the ones bound to the whole server (a
   custom role bound at the instance scope), and those bindings are removed: there is one place
   for them now.
5. When the list has no owner, the oldest account that can sign in and already holds every
   permission through its roles is given ``owner``, and the roles it held beside its own, which
   owner makes redundant, are taken off it. A server where nobody holds everything (one that
   keeps the duties apart) is left without an owner rather than give anybody more than they had:
   the owner is handed on by an owner, or by root with ``noust user``.
"""

from __future__ import annotations

import logging
import sqlite3
import time

from noust.core.access.builtin import (
    ADMIN,
    ROLE_ADMIN,
    ROLE_OWNER,
    SHIPPED,
    everything,
)
from noust.core.access.roles import (
    FALLBACK_ROLE,
    KIND_CUSTOM,
    KIND_PROTECTED,
    KIND_SEEDED,
    USABLE_STATUSES,
    decode_permissions,
    encode_permissions,
    replace_roles,
    seed,
)

logger = logging.getLogger(__name__)


def roles_split_in_force() -> bool:
    """
    Returns:
        Whether the ``roles_split`` control is in force on this server right now, which is what
        decided what its administrators could do until this migration.
    """
    from noust.core.compliance import is_on

    return is_on("roles_split")


def run_at_store(cursor: sqlite3.Cursor, *, fresh: bool = False) -> None:
    """
    The step as the store runs it, so the store holds none of its logic.

    Args:
        cursor: A cursor inside the migration's transaction.
        fresh: A store born at this version: there is nobody whose powers could change, so the
            roles are only seeded.
    """
    run(cursor, roles_split=False if fresh else roles_split_in_force(), now=time.time())


def run(cursor: sqlite3.Cursor, *, roles_split: bool, now: float) -> None:
    """
    Give an existing server its list of roles without changing what anybody can do.

    Args:
        cursor: A cursor inside the migration's transaction.
        roles_split: Whether ``roles_split`` was in force, which is what the administrator
            held until now.
        now: The time, UNIX seconds.
    """
    cursor.execute("SELECT COUNT(*) FROM roles")
    first_run = int(cursor.fetchone()[0]) == 0
    seed(cursor, now)
    if not first_run:
        return
    if roles_split:
        _keep_the_split_administrator(cursor, now)
    _move_custom_roles(cursor)
    _move_account_roles(cursor, now)
    _give_the_owner(cursor, now)


def _keep_the_split_administrator(cursor: sqlite3.Cursor, now: float) -> None:
    """
    Save the administrator as ``roles_split`` made it, and mark it as edited.

    Args:
        cursor: A cursor inside the migration's transaction.
        now: The time, UNIX seconds.
    """
    cursor.execute(
        "UPDATE roles SET permissions = ?, edited = 1, base = ?, updated_at = ?, "
        "updated_by = 'noust' WHERE name = ?",
        (
            encode_permissions(ADMIN),
            encode_permissions(SHIPPED[ROLE_ADMIN].permissions),
            now,
            ROLE_ADMIN,
        ),
    )


def _move_custom_roles(cursor: sqlite3.Cursor) -> None:
    """
    Make the custom roles rows of the one list.

    Args:
        cursor: A cursor inside the migration's transaction.
    """
    cursor.execute(
        "SELECT name, label, description, permissions, created_at, created_by, updated_at, "
        "updated_by FROM custom_roles"
    )
    for row in cursor.fetchall():
        cursor.execute(
            "INSERT OR IGNORE INTO roles (name, kind, label, description, permissions, edited, "
            "base, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, 0, '[]', ?, ?, ?, ?)",
            (
                row["name"],
                KIND_CUSTOM,
                row["label"],
                row["description"],
                row["permissions"],
                row["created_at"],
                row["created_by"],
                row["updated_at"],
                row["updated_by"],
            ),
        )


def _move_account_roles(cursor: sqlite3.Cursor, now: float) -> None:
    """
    Give every account its roles in the list: its own, then the ones bound to the whole server.

    A binding to a role that does not exist granted nothing, so it is not carried over: carrying
    it would not change what anybody holds, only leave a name nothing resolves.

    Args:
        cursor: A cursor inside the migration's transaction.
        now: The time, UNIX seconds.
    """
    cursor.execute("SELECT name FROM roles")
    known = {str(row[0]) for row in cursor.fetchall()}
    cursor.execute("SELECT id, role, created_at FROM accounts ORDER BY id")
    for account in cursor.fetchall():
        roles = [str(account["role"])]
        cursor.execute(
            "SELECT role FROM access_bindings WHERE account_id = ? AND scope_kind = 'instance' "
            "ORDER BY created_at, id",
            (int(account["id"]),),
        )
        roles += [str(row[0]) for row in cursor.fetchall() if row[0] in known]
        replace_roles(
            cursor, int(account["id"]), roles, now=account["created_at"] or now, actor="noust"
        )
    cursor.execute("DELETE FROM access_bindings WHERE scope_kind = 'instance'")


def _give_the_owner(cursor: sqlite3.Cursor, now: float) -> None:
    """
    Hand ``owner`` to the oldest account that already holds everything, when nobody has it.

    Args:
        cursor: A cursor inside the migration's transaction.
        now: The time, UNIX seconds.
    """
    cursor.execute("SELECT 1 FROM account_roles WHERE role = ?", (ROLE_OWNER,))
    if cursor.fetchone() is not None:
        return
    held: dict[str, frozenset[str]] = {}
    cursor.execute("SELECT name, kind, edited, permissions FROM roles")
    for row in cursor.fetchall():
        name = str(row["name"])
        if row["kind"] == KIND_PROTECTED:
            held[name] = everything()
        elif row["kind"] == KIND_SEEDED and not row["edited"] and name in SHIPPED:
            held[name] = SHIPPED[name].permissions
        else:
            held[name] = decode_permissions(row["permissions"])
    marks = ", ".join("?" for _ in USABLE_STATUSES)
    cursor.execute(
        f"SELECT id, role FROM accounts WHERE status IN ({marks}) ORDER BY created_at, id",  # noqa: S608 - fixed marks
        USABLE_STATUSES,
    )
    for account in cursor.fetchall():
        cursor.execute(
            "SELECT role FROM account_roles WHERE account_id = ? ORDER BY position",
            (int(account["id"]),),
        )
        names = [str(row[0]) for row in cursor.fetchall()] or [str(account["role"])]
        union: set[str] = set()
        known = False
        for name in names:
            if name in held:
                known = True
                union |= held[name]
        if not known:
            union = set(held.get(FALLBACK_ROLE, frozenset()))
        if union >= everything():
            # Owner holds everything the roles it replaces held, so nothing is lost: the account
            # keeps its own role and drops the ones beside it, which owner makes redundant.
            replace_roles(
                cursor, int(account["id"]), [names[0], ROLE_OWNER], now=now, actor="noust"
            )
            logger.info(
                "The owner role was given to account %s: it held every permission", account["id"]
            )
            return
