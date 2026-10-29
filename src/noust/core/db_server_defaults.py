# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which database server a new database of an engine is made on, when the operator chose one.

A server can run several database servers of one engine: the system's MySQL and a MySQL 8.0 in a
container beside it. A new database goes to one of them, and which is a decision a hosting panel
makes once per engine (Plesk's "default MySQL server"). This is where Noust keeps that decision
(``db_server_defaults``, schema v16): the flavour (``mysql`` and ``mariadb`` each have theirs)
and the key of the server, the host's engine (``mysql``) or an instance Noust runs
(``mysql@legacy``).

Only a choice is stored. When nobody chose, the default is worked out from what exists
(:mod:`noust.managers.database.servers`) and follows it, so removing the instance a choice named
leaves the engine with the automatic default and not with a key that points at nothing. Every
setter judges its values before it writes (:mod:`noust.validators.db_instance`), like
:class:`~noust.core.db_instance_store.DbInstanceStore`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from noust.core.db_instance_store import check_flavour
from noust.core.exceptions import ValidationError
from noust.core.timeutil import utc_now_iso
from noust.validators.db_instance import check_server_key

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = ["DbServerDefault", "DbServerDefaultStore"]

#: The longest name of the person who made the choice that the row keeps.
_MAX_ACTOR = 128


@dataclass(frozen=True)
class DbServerDefault:
    """
    The server an operator chose for an engine.

    Attributes:
        flavour: The engine (``mysql``, ``mariadb``, ``postgresql``...).
        server_key: The server: the host's engine (``mysql``) or an instance (``mysql@legacy``).
        set_by: Who chose it, as the audit trail names them; None when nobody is bound.
        set_at: When, ISO 8601 in UTC.
    """

    flavour: str
    server_key: str
    set_by: str | None
    set_at: str


class DbServerDefaultStore:
    """The operator's choices of default database server, in the store's own connection."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store, resolved on first use."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    def all(self) -> dict[str, DbServerDefault]:
        """
        List every choice.

        Returns:
            The choices, by flavour.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT flavour, server_key, set_by, set_at FROM db_server_defaults ORDER BY flavour"
            )
            return {row[0]: DbServerDefault(*row) for row in cursor.fetchall()}

    def get(self, flavour: str) -> DbServerDefault | None:
        """
        Find the choice made for an engine.

        Args:
            flavour: The engine.

        Returns:
            The choice, or None when nobody made one.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT flavour, server_key, set_by, set_at FROM db_server_defaults "
                "WHERE flavour = ?",
                (flavour,),
            )
            row = cursor.fetchone()
        return DbServerDefault(*row) if row is not None else None

    def set(self, flavour: str, server_key: str, *, set_by: str | None) -> DbServerDefault:
        """
        Choose the server a new database of an engine is made on, replacing the choice before.

        Args:
            flavour: The engine.
            server_key: The server: the host's engine or an instance Noust runs.
            set_by: Who chooses.

        Returns:
            The choice as it is now.

        Raises:
            ValidationError: The engine or the key is not one, or the name is too long
                (``field`` names it); nothing is written.
        """
        check_flavour(flavour)
        check_server_key(server_key)
        if set_by is not None and len(set_by) > _MAX_ACTOR:
            raise ValidationError(
                "The name of whoever chooses is too long to keep",
                details=f"At most {_MAX_ACTOR} characters.",
                field="set_by",
            )
        stamp = utc_now_iso("seconds")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO db_server_defaults (flavour, server_key, set_by, set_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(flavour) DO UPDATE SET "
                "server_key = excluded.server_key, set_by = excluded.set_by, "
                "set_at = excluded.set_at",
                (flavour, server_key, set_by, stamp),
            )
        return DbServerDefault(flavour, server_key, set_by, stamp)

    def clear(self, flavour: str) -> bool:
        """
        Take the choice back: the engine's default is the one Noust works out.

        Args:
            flavour: The engine.

        Returns:
            True when there was a choice to take back.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM db_server_defaults WHERE flavour = ?", (flavour,))
            return cursor.rowcount > 0

    def follow(self, flavour: str, old_key: str, new_key: str, *, set_by: str | None) -> bool:
        """
        Move the choice to the server that took over from the one chosen.

        A move of an engine's databases to a newer version leaves the old server stopped: the
        operator who chose it chose where their databases are, and they are now on the new one.

        Args:
            flavour: The engine.
            old_key: The server the databases left.
            new_key: The server they went to.
            set_by: Who moved them.

        Returns:
            True when ``old_key`` was the chosen default and ``new_key`` is now; False when
            nobody chose it (the automatic default follows what exists by itself).

        Raises:
            ValidationError: ``new_key`` is not a key a default can be chosen by.
        """
        current = self.get(flavour)
        if current is None or current.server_key != old_key:
            return False
        self.set(flavour, new_key, set_by=set_by)
        return True

    def forget(self, server_key: str) -> int:
        """
        Forget that a server was chosen, because it is gone.

        Args:
            server_key: The server that no longer exists.

        Returns:
            How many engines had it as their default.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM db_server_defaults WHERE server_key = ?", (server_key,))
            return cursor.rowcount
