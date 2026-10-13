# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The container registry accounts Noust knows, and which applications use them.

``registry_logins`` (``schema_v14.SOURCES_SQL``) holds one row per account: a
name the operator chooses, the kind of registry (it decides how a login is made),
the host, the user, and the name of the secret that holds the password. The
password is never in the row: it is a file of the secret store
(:class:`~noust.core.secrets.SecretStore`), 0600 and sealed with the rest on a
central. ``app_registry_logins`` says which applications pull and build with an
account, and goes with either side.

This is the only module that reads or writes the two tables (rule 3). Every
value is checked here, whoever calls: a host, a user or a name that could split
``docker login``'s arguments never reaches a row.
"""

from __future__ import annotations

import builtins
import re
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from noust.core.exceptions import NoustError, ValidationError
from noust.core.store import NoustStore, get_store
from noust.validators.container import check_registry_server, check_registry_username

#: The kinds of registry, and the host each one means when none is given.
KINDS: dict[str, str | None] = {
    "dockerhub": "docker.io",
    "ghcr": "ghcr.io",
    "gitlab": "registry.gitlab.com",
    "ecr": None,
    "acr": None,
    "gar": None,
    "custom": None,
}

_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")

#: The region an ECR host names: ``123456789012.dkr.ecr.eu-west-1.amazonaws.com``.
_ECR_HOST = re.compile(
    r"^\d{12}\.dkr\.ecr(?:-fips)?\.(?P<region>[a-z0-9-]+)\.amazonaws\.com(?:\.cn)?$"
)


@dataclass(frozen=True)
class RegistryLogin:
    """
    One registry account.

    Attributes:
        id: Row id.
        name: What the operator calls it.
        kind: One of :data:`KINDS`.
        server: The registry host, ``ghcr.io``.
        username: The account (for ECR, the AWS access key id).
        secret_name: The secret that holds the password (for ECR, the AWS
            secret access key). Never the password itself.
        created_at: When it was added.
        created_by: Who added it.
        updated_at: When it last changed.
        last_tested_at: When it was last tried against its registry.
        last_status: ``ok`` or ``failed`` after a test; None before one.
        last_error: What the registry said when a test failed.
        apps: The applications that use it, by domain.
    """

    id: int
    name: str
    kind: str
    server: str
    username: str
    secret_name: str
    created_at: str
    created_by: str | None
    updated_at: str
    last_tested_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    apps: tuple[str, ...] = ()

    @property
    def region(self) -> str | None:
        """The AWS region of an ECR host, else None."""
        match = _ECR_HOST.match(self.server)
        return match.group("region") if match else None

    def to_dict(self) -> dict[str, Any]:
        """
        Describe the account for the API and ``--json``, without its password.

        Returns:
            Every column but the secret's name, and the applications.
        """
        return {
            "name": self.name,
            "kind": self.kind,
            "server": self.server,
            "username": self.username,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "last_tested_at": self.last_tested_at,
            "last_status": self.last_status,
            "last_error": self.last_error,
            "apps": list(self.apps),
        }


def secret_name_for(name: str) -> str:
    """
    Name the secret that holds an account's password.

    Args:
        name: The account's name.

    Returns:
        ``registry/<name>``.
    """
    return f"registry/{name}"


def check_name(name: str) -> str:
    """
    Accept a name for an account.

    Args:
        name: What the operator typed.

    Returns:
        The name.

    Raises:
        ValidationError: It is not lower-case words joined by ``.``, ``_`` or ``-``.
    """
    if not isinstance(name, str) or _NAME.fullmatch(name) is None:
        raise ValidationError(
            f"{name!r} is not a name for a registry login",
            details="Use lower-case letters, digits, '.', '_' and '-', at most 63, starting with "
            "a letter or a digit: ghcr-acme, hub.",
            field="name",
        )
    return name


def check_login(kind: str, server: str | None, username: str) -> tuple[str, str]:
    """
    Check the three things that decide how a login is made.

    Args:
        kind: One of :data:`KINDS`.
        server: The host; the kind's own when None and it has one.
        username: The account.

    Returns:
        The host and the user.

    Raises:
        ValidationError: The kind is unknown, no host can be told, or a value
            is not one ``docker login`` takes.
    """
    if kind not in KINDS:
        raise ValidationError(
            f"{kind!r} is not a kind of registry",
            details="Choose one of: " + ", ".join(KINDS) + ".",
            field="kind",
        )
    host = server or KINDS[kind]
    if not host:
        raise ValidationError(
            f"A {kind} registry needs its host",
            details={
                "ecr": "Give it as 123456789012.dkr.ecr.eu-west-1.amazonaws.com.",
                "acr": "Give it as myregistry.azurecr.io.",
                "gar": "Give it as europe-west1-docker.pkg.dev.",
            }.get(kind, "Give the registry's host: registry.example.com."),
            field="server",
        )
    check_registry_server(host)
    if kind == "ecr" and _ECR_HOST.match(host) is None:
        raise ValidationError(
            f"{host!r} is not an Amazon ECR host",
            details="An ECR host looks like 123456789012.dkr.ecr.eu-west-1.amazonaws.com: it "
            "names the region the token is asked in.",
            field="server",
        )
    return host, check_registry_username(username)


def _row(row: Any, apps: tuple[str, ...]) -> RegistryLogin:
    """
    Args:
        row: A ``registry_logins`` row.
        apps: The domains that use it.

    Returns:
        The account.
    """
    return RegistryLogin(
        id=row["id"],
        name=row["name"],
        kind=row["kind"],
        server=row["server"],
        username=row["username"],
        secret_name=row["secret_name"],
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
        last_tested_at=row["last_tested_at"],
        last_status=row["last_status"],
        last_error=row["last_error"],
        apps=apps,
    )


class RegistryStore:
    """The accounts and their assignments, in the store."""

    def __init__(self, store: NoustStore | None = None) -> None:
        """
        Args:
            store: The store; the process-wide one when None.
        """
        self._store = store or get_store()

    def _users(self, cursor: Any) -> dict[int, tuple[str, ...]]:
        """
        Args:
            cursor: A cursor inside a transaction.

        Returns:
            The domains each account is assigned to, by account id.
        """
        cursor.execute(
            "SELECT l.login_id, a.domain FROM app_registry_logins l "
            "JOIN apps a ON a.id = l.app_id ORDER BY a.domain"
        )
        found: dict[int, list[str]] = {}
        for login_id, domain in cursor.fetchall():
            found.setdefault(login_id, []).append(domain)
        return {key: tuple(value) for key, value in found.items()}

    def list(self) -> list[RegistryLogin]:
        """
        List every account.

        Returns:
            The accounts, by name.
        """
        with self._store._transaction() as cursor:
            users = self._users(cursor)
            cursor.execute("SELECT * FROM registry_logins ORDER BY name")
            return [_row(row, users.get(row["id"], ())) for row in cursor.fetchall()]

    def get(self, name: str) -> RegistryLogin | None:
        """
        Read one account.

        Args:
            name: Its name.

        Returns:
            The account, or None.
        """
        with self._store._transaction() as cursor:
            users = self._users(cursor)
            cursor.execute("SELECT * FROM registry_logins WHERE name = ?", (name,))
            row = cursor.fetchone()
            return _row(row, users.get(row["id"], ())) if row is not None else None

    def add(
        self, name: str, kind: str, server: str, username: str, *, created_by: str | None
    ) -> RegistryLogin:
        """
        Record a new account (its password is the caller's to store).

        Args:
            name: A validated name.
            kind: A validated kind.
            server: A validated host.
            username: A validated user.
            created_by: Who added it.

        Returns:
            The account.

        Raises:
            ValidationError: An account of that name exists.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._store._transaction() as cursor:
            cursor.execute("SELECT 1 FROM registry_logins WHERE name = ?", (name,))
            if cursor.fetchone() is not None:
                raise ValidationError(
                    f"A registry login named {name} already exists",
                    details="Change it with 'noust registry update', or choose another name.",
                    field="name",
                )
            cursor.execute(
                "INSERT INTO registry_logins (name, kind, server, username, secret_name, "
                "created_at, created_by, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (name, kind, server, username, secret_name_for(name), now, created_by, now),
            )
        added = self.get(name)
        if added is None:
            raise NoustError("The registry login was not recorded")
        return added

    def update(
        self,
        name: str,
        *,
        kind: str | None = None,
        server: str | None = None,
        username: str | None = None,
    ) -> RegistryLogin:
        """
        Change an account's kind, host or user.

        Args:
            name: Its name.
            kind: The new kind.
            server: The new host.
            username: The new user.

        Returns:
            The account as it is now. A change forgets the last test.

        Raises:
            NoustError: There is no such account.
            ValidationError: A value is not valid.
        """
        current = self.get(name)
        if current is None:
            raise NoustError(
                f"No registry login named {name}", details="List them with: noust registry list"
            )
        new_kind = kind or current.kind
        new_server, new_user = check_login(
            new_kind,
            server or (current.server if new_kind == current.kind else None),
            username or current.username,
        )
        changed = replace(current, kind=new_kind, server=new_server, username=new_user)
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._store._transaction() as cursor:
            cursor.execute(
                "UPDATE registry_logins SET kind = ?, server = ?, username = ?, updated_at = ?, "
                "last_tested_at = NULL, last_status = NULL, last_error = NULL WHERE name = ?",
                (changed.kind, changed.server, changed.username, now, name),
            )
        updated = self.get(name)
        if updated is None:
            raise NoustError("The registry login disappeared while it was changed")
        return updated

    def record_test(self, name: str, ok: bool, error: str | None) -> None:
        """
        Remember how the last test went.

        Args:
            name: The account.
            ok: Whether the registry accepted it.
            error: What the registry said when it did not.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._store._transaction() as cursor:
            cursor.execute(
                "UPDATE registry_logins SET last_tested_at = ?, last_status = ?, last_error = ? "
                "WHERE name = ?",
                (now, "ok" if ok else "failed", None if ok else (error or "")[:500], name),
            )

    def remove(self, name: str) -> bool:
        """
        Delete an account and its assignments.

        Args:
            name: Its name.

        Returns:
            Whether there was one.
        """
        with self._store._transaction() as cursor:
            cursor.execute("DELETE FROM registry_logins WHERE name = ?", (name,))
            return cursor.rowcount > 0

    def assigned_to(self, domain: str) -> builtins.list[RegistryLogin]:
        """
        List the accounts an application uses.

        Args:
            domain: The application.

        Returns:
            The accounts, by name.
        """
        return [login for login in self.list() if domain in login.apps]

    def assign(self, domain: str, names: builtins.list[str], *, assigned_by: str | None) -> None:
        """
        Set the accounts an application uses.

        Args:
            domain: The application.
            names: The accounts' names; empty for none.
            assigned_by: Who set them.

        Raises:
            NoustError: The application or an account is unknown.
        """
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._store._transaction() as cursor:
            cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
            app = cursor.fetchone()
            if app is None:
                raise NoustError(
                    f"Application not found: {domain}",
                    details="Run 'noust list' to see what is deployed.",
                )
            ids: builtins.list[int] = []
            for name in dict.fromkeys(names):
                cursor.execute("SELECT id FROM registry_logins WHERE name = ?", (name,))
                row = cursor.fetchone()
                if row is None:
                    raise NoustError(
                        f"No registry login named {name}",
                        details="List them with: noust registry list",
                    )
                ids.append(row[0])
            cursor.execute("DELETE FROM app_registry_logins WHERE app_id = ?", (app[0],))
            for login_id in ids:
                cursor.execute(
                    "INSERT INTO app_registry_logins (app_id, login_id, assigned_at, assigned_by) "
                    "VALUES (?, ?, ?, ?)",
                    (app[0], login_id, now, assigned_by),
                )
