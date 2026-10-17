# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Persistence for directories people sign in against (LDAP, Active Directory).

The table is ``ldap_directories`` of ``schema_v14.FEDERATION_SQL``. What is allowed in a
directory's settings, and what signing in against one does, is
:class:`~noust.core.accounts.directory.DirectoryManager`'s; this module reads and writes rows,
and keeps the service account's password where every secret is kept.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.accounts.oidc_model import GroupMapping, parse_mappings
from noust.core.secrets import SecretStore

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The protocol its people are linked under, and the prefix of its audit events.
PROTOCOL_LDAP = "ldap"

_COLUMNS = (
    "name, label, enabled, host, port, tls, ca_file, bind_dn, user_base, user_filter, "
    "subject_attribute, email_attribute, name_attribute, username_attribute, groups_attribute, "
    "group_base, group_filter, timeout_seconds, provisioning, default_role, default_team, "
    "default_team_role, link_by_email, allowed_domains, require_email_verified, "
    "assume_email_verified, group_sync, group_mappings, created_at, created_by, updated_at, "
    "updated_by"
)


def secret_name(directory: str) -> str:
    """
    Args:
        directory: A directory's name.

    Returns:
        The secret store's name for its service account's password.
    """
    return f"ldap/{directory}"


def _json_list(raw: Any) -> list[Any]:
    """
    Args:
        raw: A JSON list as stored.

    Returns:
        The list; empty for anything that is not one.
    """
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class LdapDirectory:
    """
    One configured directory, without its service account's password.

    Attributes:
        name: Its short name, in the audit log and the sign-in page.
        label: What a person reads on the sign-in page.
        enabled: Whether the sign-in page offers it.
        host: Its name or address.
        port: Its port.
        tls: ``ldaps``, ``starttls`` or ``none`` (a directory on this machine only).
        ca_file: A certificate authority bundle to trust instead of the system's.
        bind_dn: The service account that searches; empty searches anonymously.
        user_base: Where people are searched from.
        user_filter: The search, with ``{username}`` where what a person typed goes.
        subject_attribute: The directory's own stable id for a person (``entryUUID``,
            ``objectGUID``): what identifies them, never their name or e-mail.
        email_attribute: Where their address is.
        name_attribute: Where their display name is.
        username_attribute: Where their login name is, a hint for a new account's name.
        groups_attribute: Where the groups they belong to are listed on their entry.
        group_base: Where groups are searched, when the entry does not list them.
        group_filter: That search, with ``{dn}`` and ``{username}``.
        timeout_seconds: How long one sign-in may take.
        provisioning: A first sign-in: ``off``, ``invite`` or ``auto``.
        default_role: The role of an account that provisioning creates.
        default_team: The team it joins.
        default_team_role: Its role there.
        link_by_email: Link a first sign-in to the account of its address.
        allowed_domains: Only these e-mail domains; any when empty.
        require_email_verified: Refuse a person without an address.
        assume_email_verified: Take the directory's address as verified (it is the
            organisation's own).
        group_sync: Whether groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
        created_at: When it was configured.
        created_by: Who configured it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    label: str
    host: str
    user_base: str
    user_filter: str
    enabled: bool = True
    port: int = 636
    tls: str = "ldaps"
    ca_file: str | None = None
    bind_dn: str = ""
    subject_attribute: str = "entryUUID"
    email_attribute: str = "mail"
    name_attribute: str = "displayName"
    username_attribute: str = "uid"
    groups_attribute: str = "memberOf"
    group_base: str = ""
    group_filter: str = ""
    timeout_seconds: int = 10
    provisioning: str = "off"
    default_role: str = "viewer"
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool = False
    allowed_domains: tuple[str, ...] = ()
    require_email_verified: bool = False
    assume_email_verified: bool = True
    group_sync: bool = False
    group_mappings: tuple[GroupMapping, ...] = ()
    created_at: float = 0.0
    created_by: str | None = None
    updated_at: float = 0.0
    updated_by: str | None = None

    @property
    def protocol(self) -> str:
        """The protocol its people are linked under."""
        return PROTOCOL_LDAP

    @property
    def issuer(self) -> str:
        """
        What its identities are filed under. Not its address: moving the directory to another
        server keeps everybody linked.
        """
        return f"{PROTOCOL_LDAP}:{self.name}"

    @property
    def require_amr(self) -> tuple[str, ...]:
        """A password bind says nothing a server could require."""
        return ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The directory as the API and ``--json`` show it; the password is never here.
        """
        return {
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled,
            "host": self.host,
            "port": self.port,
            "tls": self.tls,
            "ca_file": self.ca_file,
            "bind_dn": self.bind_dn,
            "user_base": self.user_base,
            "user_filter": self.user_filter,
            "subject_attribute": self.subject_attribute,
            "email_attribute": self.email_attribute,
            "name_attribute": self.name_attribute,
            "username_attribute": self.username_attribute,
            "groups_attribute": self.groups_attribute,
            "group_base": self.group_base,
            "group_filter": self.group_filter,
            "timeout_seconds": self.timeout_seconds,
            "provisioning": self.provisioning,
            "default_role": self.default_role,
            "default_team": self.default_team,
            "default_team_role": self.default_team_role,
            "link_by_email": self.link_by_email,
            "allowed_domains": list(self.allowed_domains),
            "require_email_verified": self.require_email_verified,
            "assume_email_verified": self.assume_email_verified,
            "group_sync": self.group_sync,
            "group_mappings": [mapping.to_dict() for mapping in self.group_mappings],
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _directory(row: sqlite3.Row) -> LdapDirectory:
    """
    Args:
        row: A row selected with :data:`_COLUMNS`.

    Returns:
        The directory.
    """
    return LdapDirectory(
        name=str(row["name"]),
        label=str(row["label"]),
        enabled=bool(row["enabled"]),
        host=str(row["host"]),
        port=int(row["port"]),
        tls=str(row["tls"]),
        ca_file=row["ca_file"],
        bind_dn=str(row["bind_dn"]),
        user_base=str(row["user_base"]),
        user_filter=str(row["user_filter"]),
        subject_attribute=str(row["subject_attribute"]),
        email_attribute=str(row["email_attribute"]),
        name_attribute=str(row["name_attribute"]),
        username_attribute=str(row["username_attribute"]),
        groups_attribute=str(row["groups_attribute"]),
        group_base=str(row["group_base"]),
        group_filter=str(row["group_filter"]),
        timeout_seconds=int(row["timeout_seconds"]),
        provisioning=str(row["provisioning"]),
        default_role=str(row["default_role"]),
        default_team=row["default_team"],
        default_team_role=row["default_team_role"],
        link_by_email=bool(row["link_by_email"]),
        allowed_domains=tuple(str(item) for item in _json_list(row["allowed_domains"])),
        require_email_verified=bool(row["require_email_verified"]),
        assume_email_verified=bool(row["assume_email_verified"]),
        group_sync=bool(row["group_sync"]),
        group_mappings=tuple(parse_mappings(_json_list(row["group_mappings"]))),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"],
    )


class DirectoryStore:
    """
    Read and write the directories and their service account passwords.

    Args:
        store: The store; the process-wide one by default.
        secrets: The secret store; the process-wide one by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self,
        store: NoustStore | None = None,
        secrets: SecretStore | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._store = store
        self._secrets = secrets
        self._clock = clock or time.time

    @property
    def store(self) -> NoustStore:
        """The store the table lives in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    @property
    def secrets(self) -> SecretStore:
        """Where service account passwords are kept."""
        return self._secrets or SecretStore()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT with fixed clauses.
            params: Its parameters.

        Returns:
            The rows.
        """
        return list(self.store._get_connection().execute(sql, params).fetchall())

    def list_directories(self) -> list[LdapDirectory]:
        """
        Returns:
            Every configured directory, by name.
        """
        rows = self._rows(f"SELECT {_COLUMNS} FROM ldap_directories ORDER BY name")  # noqa: S608 - fixed columns
        return [_directory(row) for row in rows]

    def get_directory(self, name: str) -> LdapDirectory | None:
        """
        Args:
            name: The directory's name.

        Returns:
            The directory, or None.
        """
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM ldap_directories WHERE name = ?",  # noqa: S608 - fixed columns
            ((name or "").strip().lower(),),
        )
        return _directory(rows[0]) if rows else None

    def save_directory(self, directory: LdapDirectory) -> None:
        """
        Write a directory, replacing the one of the same name.

        Args:
            directory: The validated directory.
        """
        values = (
            directory.name,
            directory.label,
            int(directory.enabled),
            directory.host,
            directory.port,
            directory.tls,
            directory.ca_file,
            directory.bind_dn,
            directory.user_base,
            directory.user_filter,
            directory.subject_attribute,
            directory.email_attribute,
            directory.name_attribute,
            directory.username_attribute,
            directory.groups_attribute,
            directory.group_base,
            directory.group_filter,
            directory.timeout_seconds,
            directory.provisioning,
            directory.default_role,
            directory.default_team,
            directory.default_team_role,
            int(directory.link_by_email),
            json.dumps(list(directory.allowed_domains)),
            int(directory.require_email_verified),
            int(directory.assume_email_verified),
            int(directory.group_sync),
            json.dumps([mapping.to_dict() for mapping in directory.group_mappings]),
            directory.created_at,
            directory.created_by,
            directory.updated_at,
            directory.updated_by,
        )
        updates = ", ".join(
            f"{column.strip()} = excluded.{column.strip()}"
            for column in _COLUMNS.split(",")
            if column.strip() not in ("name", "created_at", "created_by")
        )
        placeholders = ", ".join("?" for _ in values)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO ldap_directories ({_COLUMNS}) VALUES ({placeholders}) "  # noqa: S608 - fixed columns
                f"ON CONFLICT(name) DO UPDATE SET {updates}",
                values,
            )

    def delete_directory(self, name: str) -> bool:
        """
        Remove a directory and its password. Identities stay, as OpenID Connect's do: filed under
        the directory's name, a directory configured again under it finds them.

        Args:
            name: The directory's name.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM ldap_directories WHERE name = ?", (name,))
            removed = cursor.rowcount == 1
        self.secrets.delete(secret_name(name))
        return removed

    def has_secret(self, name: str) -> bool:
        """
        Args:
            name: The directory's name.

        Returns:
            Whether a password is stored, without reading it (a sealed, locked central can
            still say).
        """
        return self.secrets.path(secret_name(name)).is_file()

    def read_secret(self, name: str) -> str | None:
        """
        Args:
            name: The directory's name.

        Returns:
            The service account's password, or None when it searches anonymously.

        Raises:
            SecretsLockedError: The store is sealed and this process is locked.
        """
        return self.secrets.read(secret_name(name))

    def write_secret(self, name: str, value: str) -> None:
        """
        Args:
            name: The directory's name.
            value: The service account's password.
        """
        self.secrets.write(secret_name(name), value)

    def delete_secret(self, name: str) -> None:
        """
        Args:
            name: The directory's name.
        """
        self.secrets.delete(secret_name(name))
