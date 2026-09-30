# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The store's record of what Noust did with databases beyond creating them.

Two tables from schema v12 (``core/schema_v12.py``, the databases fragment):

- ``database_links``: which variable of which application's environment
  carries a database's connection string. Unlinking, rotating a password and
  dropping the database read it to know what to rewrite.
- ``database_accounts``: the profile Noust last applied to an account and
  when it last set its password, which is what the console shows as the
  password's age. The engine stays the truth for what an account can do.

They go through the store's own transaction, so ``--dry-run`` rehearses them
like every other write: the rows are written and rolled back.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore

#: What the link to a Redis instance is called by default; everything else
#: gets ``DATABASE_URL``.
DEFAULT_ENV_VARS: dict[str, str] = {"redis": "REDIS_URL"}


def default_env_var(engine: str) -> str:
    """
    Name the variable a link writes when the caller names none.

    Args:
        engine: Canonical engine name.

    Returns:
        ``REDIS_URL`` for Redis, ``DATABASE_URL`` for every other engine.
    """
    return DEFAULT_ENV_VARS.get(engine, "DATABASE_URL")


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 with its UTC offset.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DatabaseLink:
    """
    One application's use of one database.

    Attributes:
        app_id: The application's store id.
        engine: Canonical engine name.
        db_name: The database (a slot number for Redis).
        username: The account the connection string signs in as.
        env_var: The variable holding the connection string.
        extra_vars: Whether ``DB_HOST``, ``DB_PORT``, ``DB_NAME``,
            ``DB_USER`` and ``DB_PASSWORD`` were written too.
        id: Row id.
        created_at: When the link was made.
        updated_at: When it last changed.
    """

    app_id: int
    engine: str
    db_name: str
    username: str | None = None
    env_var: str = "DATABASE_URL"
    extra_vars: bool = False
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> DatabaseLink:
        """
        Build a link from a row.

        Args:
            row: A ``database_links`` row.

        Returns:
            The link.
        """
        data = dict(row)
        data["extra_vars"] = bool(data.get("extra_vars"))
        return cls(**data)

    def to_dict(self) -> dict[str, Any]:
        """
        Render the link as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


@dataclass
class DatabaseAccount:
    """
    What Noust recorded about an account it created or changed.

    Attributes:
        engine: Canonical engine name.
        username: The account.
        host: Its host restriction (MySQL); ``localhost`` elsewhere.
        db_name: The database it was created for, if any.
        profile: The profile Noust last applied, if any.
        password_changed_at: When Noust last set its password.
        id: Row id.
        created_at: When the row was written.
        updated_at: When it last changed.
    """

    engine: str
    username: str
    host: str = "localhost"
    db_name: str | None = None
    profile: str | None = None
    password_changed_at: str | None = None
    id: int | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> DatabaseAccount:
        """
        Build an account from a row.

        Args:
            row: A ``database_accounts`` row.

        Returns:
            The account.
        """
        return cls(**dict(row))

    def to_dict(self) -> dict[str, Any]:
        """
        Render the account as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


class DatabaseRecords:
    """
    Read and write the two tables, through a store's transactions.

    The store's ``_transaction`` is used on purpose, not a connection of this
    module's own: it is where ``--dry-run`` turns a write into a rehearsal.
    """

    def __init__(self, store: NoustStore) -> None:
        """
        Args:
            store: The store holding the tables.
        """
        self.store = store

    # ------------------------------------------------------------------ links

    def links(
        self,
        *,
        app_id: int | None = None,
        engine: str | None = None,
        db_name: str | None = None,
        username: str | None = None,
    ) -> list[DatabaseLink]:
        """
        List links, optionally narrowed.

        Args:
            app_id: Only this application's.
            engine: Only this engine's.
            db_name: Only this database's (with ``engine``).
            username: Only those signing in as this account.

        Returns:
            The links, oldest first.
        """
        query = "SELECT * FROM database_links WHERE 1=1"
        params: list[object] = []
        for column, value in (
            ("app_id", app_id),
            ("engine", engine),
            ("db_name", db_name),
            ("username", username),
        ):
            if value is not None:
                query += f" AND {column} = ?"
                params.append(value)
        query += " ORDER BY id"
        with self.store._transaction() as cursor:
            cursor.execute(query, params)
            return [DatabaseLink.from_row(row) for row in cursor.fetchall()]

    def link(self, app_id: int, engine: str, db_name: str) -> DatabaseLink | None:
        """
        Read one application's link to one database.

        Args:
            app_id: The application.
            engine: The engine.
            db_name: The database.

        Returns:
            The link, or None.
        """
        found = self.links(app_id=app_id, engine=engine, db_name=db_name)
        return found[0] if found else None

    def save_link(self, link: DatabaseLink) -> DatabaseLink:
        """
        Record a link, replacing the application's previous one to the database.

        Args:
            link: The link.

        Returns:
            The link as stored.
        """
        now = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO database_links "
                "(app_id, engine, db_name, username, env_var, extra_vars, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(app_id, engine, db_name) DO UPDATE SET "
                "username = excluded.username, env_var = excluded.env_var, "
                "extra_vars = excluded.extra_vars, updated_at = excluded.updated_at",
                (
                    link.app_id,
                    link.engine,
                    link.db_name,
                    link.username,
                    link.env_var,
                    int(link.extra_vars),
                    now,
                    now,
                ),
            )
        stored = self.link(link.app_id, link.engine, link.db_name)
        return stored if stored is not None else link

    def delete_link(self, app_id: int, engine: str, db_name: str) -> bool:
        """
        Forget one application's link to one database.

        Args:
            app_id: The application.
            engine: The engine.
            db_name: The database.

        Returns:
            Whether a link was removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_links WHERE app_id = ? AND engine = ? AND db_name = ?",
                (app_id, engine, db_name),
            )
            return cursor.rowcount > 0

    def delete_links_to(self, engine: str, db_name: str) -> int:
        """
        Forget every link to a database that is gone.

        Args:
            engine: The engine.
            db_name: The database.

        Returns:
            How many links were removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_links WHERE engine = ? AND db_name = ?", (engine, db_name)
            )
            return cursor.rowcount

    # --------------------------------------------------------------- accounts

    def account(
        self, engine: str, username: str, host: str = "localhost"
    ) -> DatabaseAccount | None:
        """
        Read what Noust recorded about an account.

        Args:
            engine: The engine.
            username: The account.
            host: Its host restriction.

        Returns:
            The record, or None.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM database_accounts WHERE engine = ? AND username = ? AND host = ?",
                (engine, username, host),
            )
            row = cursor.fetchone()
            return DatabaseAccount.from_row(row) if row else None

    def accounts(self, engine: str | None = None) -> list[DatabaseAccount]:
        """
        List the accounts Noust recorded.

        Args:
            engine: Only this engine's.

        Returns:
            The records.
        """
        query = "SELECT * FROM database_accounts"
        params: list[object] = []
        if engine is not None:
            query += " WHERE engine = ?"
            params.append(engine)
        with self.store._transaction() as cursor:
            cursor.execute(query + " ORDER BY username", params)
            return [DatabaseAccount.from_row(row) for row in cursor.fetchall()]

    def save_account(
        self,
        engine: str,
        username: str,
        host: str = "localhost",
        *,
        db_name: str | None = None,
        profile: str | None = None,
        password_changed: bool = False,
    ) -> DatabaseAccount:
        """
        Record, or update, what Noust did to an account.

        Only what is given changes: a profile change keeps the password's
        age, a rotation keeps the profile.

        Args:
            engine: The engine.
            username: The account.
            host: Its host restriction.
            db_name: The database it is for, when known.
            profile: The profile just applied.
            password_changed: Whether Noust just set its password.

        Returns:
            The record as stored.
        """
        now = _now()
        current = self.account(engine, username, host)
        with self.store._transaction() as cursor:
            if current is None:
                cursor.execute(
                    "INSERT INTO database_accounts (engine, username, host, db_name, profile, "
                    "password_changed_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        engine,
                        username,
                        host,
                        db_name,
                        profile,
                        now if password_changed else None,
                        now,
                        now,
                    ),
                )
            else:
                cursor.execute(
                    "UPDATE database_accounts SET db_name = ?, profile = ?, "
                    "password_changed_at = ?, updated_at = ? WHERE id = ?",
                    (
                        db_name if db_name is not None else current.db_name,
                        profile if profile is not None else current.profile,
                        now if password_changed else current.password_changed_at,
                        now,
                        current.id,
                    ),
                )
        stored = self.account(engine, username, host)
        return stored if stored is not None else DatabaseAccount(engine, username, host)

    def delete_account(self, engine: str, username: str, host: str = "localhost") -> bool:
        """
        Forget an account that was dropped.

        Args:
            engine: The engine.
            username: The account.
            host: Its host restriction.

        Returns:
            Whether a record was removed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM database_accounts WHERE engine = ? AND username = ? AND host = ?",
                (engine, username, host),
            )
            return cursor.rowcount > 0
