# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application's site is told to do beyond serving it, kept in the store.

Two tables of the networking fragment of schema v14: ``site_policies`` (one JSON
document per application, written and read as text here and given meaning by
:mod:`noust.managers.site_policy`) and ``site_auth_users`` (the accounts of its
basic authentication, with only the salted hash of each password). They are
tables of their own, not columns of ``apps``, so the full-row write of a
redeploy cannot put back what an operator changed while it ran, and the site is
rendered from them each time, whichever deployer renders it.

:class:`SitePolicyStore` is the only reader and writer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class AuthUser:
    """
    One account of an application's basic authentication.

    Attributes:
        username: The login name.
        password_hash: The salted hash the web server compares against; never the password.
        created_by: Who added it, an actor label.
        created_at: When, ISO 8601 UTC.
    """

    username: str
    password_hash: str
    created_by: str | None
    created_at: str


class SitePolicyStore:
    """
    Read and write the policy documents and the basic-authentication accounts.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def get_document(self, domain: str) -> str | None:
        """
        Read an application's policy document.

        Args:
            domain: The application's primary domain.

        Returns:
            The JSON text, or None when the application has none (every default applies).
        """
        return self.store.get_site_policy_document(domain)

    def set_document(self, domain: str, document: str | None, *, updated_by: str | None) -> None:
        """
        Replace an application's policy document, or remove it.

        Args:
            domain: The application's primary domain.
            document: The validated JSON text, or None to go back to the defaults.
            updated_by: Who changed it, an actor label.
        """
        with self.store._transaction() as cursor:
            if document is None:
                cursor.execute("DELETE FROM site_policies WHERE domain = ?", (domain,))
                return
            cursor.execute(
                """
                INSERT INTO site_policies (domain, document, updated_by, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(domain) DO UPDATE SET
                    document = excluded.document,
                    updated_by = excluded.updated_by,
                    updated_at = excluded.updated_at
                """,
                (domain, document, updated_by, _now()),
            )

    def list_users(self, domain: str) -> list[AuthUser]:
        """
        List an application's basic-authentication accounts.

        Args:
            domain: The application's primary domain.

        Returns:
            The accounts, by name.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT username, password_hash, created_by, created_at "
                "FROM site_auth_users WHERE domain = ? ORDER BY username",
                (domain,),
            )
            .fetchall()
        )
        return [
            AuthUser(
                username=str(row["username"]),
                password_hash=str(row["password_hash"]),
                created_by=row["created_by"],
                created_at=str(row["created_at"]),
            )
            for row in rows
        ]

    def set_user(
        self, domain: str, username: str, password_hash: str, *, created_by: str | None
    ) -> bool:
        """
        Add an account, or replace the password of one.

        Args:
            domain: The application's primary domain.
            username: The login name, already validated.
            password_hash: The salted hash.
            created_by: Who did it, an actor label.

        Returns:
            True when the account is new, False when it already existed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT 1 FROM site_auth_users WHERE domain = ? AND username = ?",
                (domain, username),
            )
            existed = cursor.fetchone() is not None
            cursor.execute(
                """
                INSERT INTO site_auth_users (domain, username, password_hash, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(domain, username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    created_by = excluded.created_by
                """,
                (domain, username, password_hash, created_by, _now()),
            )
        return not existed

    def remove_user(self, domain: str, username: str) -> bool:
        """
        Remove an account.

        Args:
            domain: The application's primary domain.
            username: The login name.

        Returns:
            True when the account existed.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM site_auth_users WHERE domain = ? AND username = ?",
                (domain, username),
            )
            return cursor.rowcount > 0

    def forget(self, domain: str) -> None:
        """
        Drop everything kept for an application whose site is gone.

        Args:
            domain: The application's primary domain.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM site_policies WHERE domain = ?", (domain,))
            cursor.execute("DELETE FROM site_auth_users WHERE domain = ?", (domain,))

    def domains_with_policy(self) -> list[str]:
        """
        List the applications that have a policy document.

        Returns:
            Their primary domains, sorted.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT domain FROM site_policies ORDER BY domain")
            .fetchall()
        )
        return [str(row["domain"]) for row in rows]


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat()
