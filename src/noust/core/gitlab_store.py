# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The GitLab accounts this server reads projects with, and the OAuth applications it signs in through.

A connection is one GitLab user's access to one host (gitlab.com or a self-hosted GitLab): a
personal, group or project access token, or the tokens an OAuth sign-in handed back. It is what the
console's project and branch picker lists with, and what clones a private project over https.
The tokens are never columns: each is a secret (:class:`~noust.core.secrets.SecretStore`) named by
the row. An OAuth application is the ``client id`` and secret an operator registered in GitLab
(Admin area, Applications, or the user's own) so that people can connect without pasting a token.

This module is the only reader and writer of both tables (``gitlab_connections`` and
``gitlab_oauth_apps``, ``core/schema_v14.py``).
"""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store

#: How a connection signs in.
KINDS = ("token", "oauth")

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")


@dataclass(frozen=True)
class GitLabConnection:
    """
    One GitLab account's access to one host.

    Attributes:
        id: Row id.
        name: What the operator calls it.
        host: The GitLab host, ``gitlab.com`` or ``git.example.com[:port]``.
        kind: ``token`` or ``oauth``.
        username: The GitLab user the credentials belong to, as GitLab said.
        secret_name: The secret that holds the credentials. Never the credentials themselves.
        created_at: When it was added.
        created_by: Who added it.
        updated_at: When it last changed.
        last_checked_at: When GitLab was last asked who this is.
        last_status: ``ok`` or ``failed`` after a check; None before one.
        last_error: What GitLab said when a check failed.
    """

    id: int
    name: str
    host: str
    kind: str
    username: str | None
    secret_name: str
    created_at: str
    created_by: str | None
    updated_at: str
    last_checked_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the connection for the API and ``--json``, without its credentials.

        Returns:
            Every column but the secret's name.
        """
        return {
            "name": self.name,
            "host": self.host,
            "kind": self.kind,
            "username": self.username,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "last_checked_at": self.last_checked_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class OAuthApplication:
    """
    An OAuth application registered in GitLab, for one host.

    Attributes:
        host: The GitLab host it is registered on.
        client_id: Its application id; public.
        secret_name: The secret that holds its client secret.
        created_at: When it was recorded.
        created_by: Who recorded it.
    """

    host: str
    client_id: str
    secret_name: str
    created_at: str
    created_by: str | None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe it for the API and ``--json``, without the client secret.

        Returns:
            The host, the application id and who recorded it.
        """
        return {
            "host": self.host,
            "client_id": self.client_id,
            "created_at": self.created_at,
            "created_by": self.created_by,
        }


def check_name(name: str) -> str:
    """
    Accept a name for a connection.

    Args:
        name: What the operator typed.

    Returns:
        The name.

    Raises:
        ValidationError: It is not lower-case words joined by ``.``, ``_`` or ``-``.
    """
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValidationError(
            f"{name!r} is not a name for a GitLab connection",
            details="Use lower-case letters, digits, '.', '_' and '-', at most 63, starting with "
            "a letter or a digit: gitlab-acme, work.",
            field="name",
        )
    return name


def connection_secret(name: str) -> str:
    """
    Name the secret that holds a connection's credentials.

    Args:
        name: The connection's name.

    Returns:
        ``gitlab/connections/<name>``.
    """
    return f"gitlab/connections/{name}"


def oauth_secret(host: str) -> str:
    """
    Name the secret that holds an OAuth application's client secret.

    Args:
        host: The GitLab host.

    Returns:
        ``gitlab/oauth/<host>``, the host written so it is one path segment.
    """
    return f"gitlab/oauth/{host.replace(':', '_')}"


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _connection(row: Any) -> GitLabConnection:
    """
    Args:
        row: A ``gitlab_connections`` row.

    Returns:
        The connection.
    """
    return GitLabConnection(
        id=row["id"],
        name=row["name"],
        host=row["host"],
        kind=row["kind"],
        username=row["username"],
        secret_name=row["secret_name"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
        last_checked_at=row["last_checked_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
    )


class GitLabStore:
    """The connections and the OAuth applications, in the store."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store or get_store()

    def list(self) -> list[GitLabConnection]:
        """
        List every connection.

        Returns:
            The connections, by name.
        """
        with self._store._transaction() as cursor:
            cursor.execute("SELECT * FROM gitlab_connections ORDER BY name")
            return [_connection(row) for row in cursor.fetchall()]

    def get(self, name: str) -> GitLabConnection | None:
        """
        Read one connection.

        Args:
            name: Its name.

        Returns:
            It, or None.
        """
        with self._store._transaction() as cursor:
            cursor.execute("SELECT * FROM gitlab_connections WHERE name = ?", (name,))
            row = cursor.fetchone()
        return _connection(row) if row is not None else None

    def for_host(self, host: str) -> GitLabConnection | None:
        """
        Find the connection that signs in to a host.

        Args:
            host: A GitLab host, with its port when it has one.

        Returns:
            The oldest connection of that host; None when there is none.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "SELECT * FROM gitlab_connections WHERE host = ? ORDER BY id LIMIT 1",
                (host.lower(),),
            )
            row = cursor.fetchone()
        return _connection(row) if row is not None else None

    def any(self) -> bool:
        """
        Tell whether any connection exists, cheaply.

        Returns:
            True when there is at least one.
        """
        with self._store._transaction() as cursor:
            cursor.execute("SELECT 1 FROM gitlab_connections LIMIT 1")
            return cursor.fetchone() is not None

    def add(
        self,
        name: str,
        host: str,
        kind: str,
        *,
        username: str | None,
        created_by: str | None,
    ) -> GitLabConnection:
        """
        Record a connection.

        Args:
            name: Its name; a new one.
            host: The GitLab host.
            kind: One of :data:`KINDS`.
            username: The GitLab user, as GitLab said.
            created_by: Who added it.

        Returns:
            The connection.

        Raises:
            ValidationError: The kind is unknown, or the name is taken.
        """
        if kind not in KINDS:
            raise ValidationError(
                f"{kind!r} is not a kind of GitLab connection",
                details="Choose one of: " + ", ".join(KINDS) + ".",
                field="kind",
            )
        now = _now()
        with self._store._transaction() as cursor:
            cursor.execute("SELECT 1 FROM gitlab_connections WHERE name = ?", (name,))
            if cursor.fetchone() is not None:
                raise ValidationError(
                    f"There is a GitLab connection named {name} already",
                    details="Remove it first, or choose another name: noust gitlab list.",
                    field="name",
                )
            cursor.execute(
                "INSERT INTO gitlab_connections (name, host, kind, username, secret_name, "
                "created_at, created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, host.lower(), kind, username, connection_secret(name), now, created_by, now),
            )
        stored = self.get(name)
        if stored is None:  # pragma: no cover - the insert just succeeded
            raise NoustError(f"The GitLab connection {name} was not saved")
        return stored

    def record_check(self, name: str, *, ok: bool, username: str | None, error: str | None) -> None:
        """
        Record the result of asking GitLab who a connection is.

        Args:
            name: The connection.
            ok: Whether GitLab accepted it.
            username: The user GitLab named, when it did.
            error: What GitLab said, when it did not.
        """
        now = _now()
        with self._store._transaction() as cursor:
            cursor.execute(
                "UPDATE gitlab_connections SET last_checked_at = ?, last_status = ?, "
                "last_error = ?, username = COALESCE(?, username), updated_at = ? WHERE name = ?",
                (
                    now,
                    "ok" if ok else "failed",
                    None if ok else (error or "")[:500],
                    username,
                    now,
                    name,
                ),
            )

    def remove(self, name: str) -> bool:
        """
        Delete a connection.

        Args:
            name: Its name.

        Returns:
            Whether there was one.
        """
        with self._store._transaction() as cursor:
            cursor.execute("DELETE FROM gitlab_connections WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def oauth_applications(self) -> builtins.list[OAuthApplication]:
        """
        List the OAuth applications.

        Returns:
            One per host, by host.
        """
        with self._store._transaction() as cursor:
            cursor.execute("SELECT * FROM gitlab_oauth_apps ORDER BY host")
            return [
                OAuthApplication(
                    host=row["host"],
                    client_id=row["client_id"],
                    secret_name=row["secret_name"],
                    created_at=row["created_at"],
                    created_by=row["created_by"],
                )
                for row in cursor.fetchall()
            ]

    def oauth_application(self, host: str) -> OAuthApplication | None:
        """
        Read the OAuth application of a host.

        Args:
            host: The GitLab host.

        Returns:
            It, or None.
        """
        wanted = host.lower()
        return next((app for app in self.oauth_applications() if app.host == wanted), None)

    def save_oauth_application(
        self, host: str, client_id: str, *, created_by: str | None
    ) -> OAuthApplication:
        """
        Record (or replace) the OAuth application of a host.

        Args:
            host: The GitLab host.
            client_id: Its application id.
            created_by: Who recorded it.

        Returns:
            The application.
        """
        with self._store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO gitlab_oauth_apps (host, client_id, secret_name, created_at, "
                "created_by) VALUES (?, ?, ?, ?, ?) ON CONFLICT(host) DO UPDATE SET "
                "client_id = excluded.client_id, created_by = excluded.created_by",
                (host.lower(), client_id, oauth_secret(host.lower()), _now(), created_by),
            )
        stored = self.oauth_application(host)
        if stored is None:  # pragma: no cover - the upsert just succeeded
            raise NoustError(f"The OAuth application of {host} was not saved")
        return stored

    def remove_oauth_application(self, host: str) -> bool:
        """
        Forget the OAuth application of a host.

        Args:
            host: The GitLab host.

        Returns:
            Whether there was one.
        """
        with self._store._transaction() as cursor:
            cursor.execute("DELETE FROM gitlab_oauth_apps WHERE host = ?", (host.lower(),))
            return cursor.rowcount > 0


__all__ = [
    "KINDS",
    "GitLabConnection",
    "GitLabStore",
    "OAuthApplication",
    "check_name",
    "connection_secret",
    "oauth_secret",
]
