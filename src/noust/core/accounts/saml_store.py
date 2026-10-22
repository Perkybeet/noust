# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Persistence for SAML identity providers and the assertions already accepted.

The tables are ``saml_providers`` and ``saml_assertions`` of ``schema_v14.FEDERATION_SQL``. What a
provider may be configured to, and what accepting one of its responses means, is
:class:`~noust.core.accounts.saml.SamlManager`'s; this module reads and writes rows. Nothing in a
provider's row is secret: its certificates are the public half of the keys that sign.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.accounts.oidc_model import GroupMapping, parse_mappings

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The protocol its people are linked under, and the prefix of its audit events.
PROTOCOL_SAML = "saml"

_COLUMNS = (
    "name, label, enabled, idp_entity_id, sso_url, certificates, sp_entity_id, "
    "subject_attribute, email_attribute, name_attribute, username_attribute, groups_attribute, "
    "clock_skew_seconds, provisioning, default_role, default_team, default_team_role, "
    "link_by_email, allowed_domains, require_email_verified, assume_email_verified, "
    "require_amr, group_sync, group_mappings, created_at, created_by, updated_at, updated_by"
)


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
class SamlProvider:
    """
    One configured SAML 2.0 identity provider.

    Attributes:
        name: Its short name, in the sign-in address and the audit log.
        label: What the button says after "Sign in with".
        idp_entity_id: Its entity id: what its assertions' issuer must be.
        sso_url: Where the browser is sent to sign in (HTTP-Redirect binding).
        certificates: Its signing certificates, in PEM: the only keys that verify it.
        sp_entity_id: The entity id Noust presents to it; empty derives it from the console's address.
        enabled: Whether the sign-in page offers it.
        subject_attribute: The attribute that identifies a person; empty uses the NameID.
        email_attribute: Where their address is.
        name_attribute: Where their display name is.
        username_attribute: Where their login name is, a hint for a new account's name.
        groups_attribute: Where the groups they belong to are listed.
        clock_skew_seconds: How far its clock may differ from this server's.
        provisioning: A first sign-in: ``off``, ``invite`` or ``auto``.
        default_role: The role of an account that provisioning creates.
        default_team: The team it joins.
        default_team_role: Its role there.
        link_by_email: Link a first sign-in to the account of its address.
        allowed_domains: Only these e-mail domains; any when empty.
        require_email_verified: Refuse a person without an address.
        assume_email_verified: Take the provider's address as verified.
        require_amr: One of these authentication context classes must be reported.
        group_sync: Whether groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
        created_at: When it was configured.
        created_by: Who configured it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    label: str
    idp_entity_id: str
    sso_url: str
    certificates: tuple[str, ...] = ()
    sp_entity_id: str = ""
    enabled: bool = True
    subject_attribute: str = ""
    email_attribute: str = "email"
    name_attribute: str = "displayName"
    username_attribute: str = ""
    groups_attribute: str = "groups"
    clock_skew_seconds: int = 60
    provisioning: str = "off"
    default_role: str = "viewer"
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool = False
    allowed_domains: tuple[str, ...] = ()
    require_email_verified: bool = False
    assume_email_verified: bool = True
    require_amr: tuple[str, ...] = ()
    group_sync: bool = False
    group_mappings: tuple[GroupMapping, ...] = ()
    created_at: float = 0.0
    created_by: str | None = None
    updated_at: float = 0.0
    updated_by: str | None = None

    @property
    def protocol(self) -> str:
        """The protocol its people are linked under."""
        return PROTOCOL_SAML

    @property
    def issuer(self) -> str:
        """What its identities are filed under: its entity id, which its assertions carry."""
        return self.idp_entity_id

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The provider as the API and ``--json`` show it.
        """
        return {
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled,
            "idp_entity_id": self.idp_entity_id,
            "sso_url": self.sso_url,
            "certificates": list(self.certificates),
            "sp_entity_id": self.sp_entity_id,
            "subject_attribute": self.subject_attribute,
            "email_attribute": self.email_attribute,
            "name_attribute": self.name_attribute,
            "username_attribute": self.username_attribute,
            "groups_attribute": self.groups_attribute,
            "clock_skew_seconds": self.clock_skew_seconds,
            "provisioning": self.provisioning,
            "default_role": self.default_role,
            "default_team": self.default_team,
            "default_team_role": self.default_team_role,
            "link_by_email": self.link_by_email,
            "allowed_domains": list(self.allowed_domains),
            "require_email_verified": self.require_email_verified,
            "assume_email_verified": self.assume_email_verified,
            "require_amr": list(self.require_amr),
            "group_sync": self.group_sync,
            "group_mappings": [mapping.to_dict() for mapping in self.group_mappings],
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


def _provider(row: sqlite3.Row) -> SamlProvider:
    """
    Args:
        row: A row selected with :data:`_COLUMNS`.

    Returns:
        The provider.
    """
    return SamlProvider(
        name=str(row["name"]),
        label=str(row["label"]),
        enabled=bool(row["enabled"]),
        idp_entity_id=str(row["idp_entity_id"]),
        sso_url=str(row["sso_url"]),
        certificates=tuple(str(item) for item in _json_list(row["certificates"])),
        sp_entity_id=str(row["sp_entity_id"]),
        subject_attribute=str(row["subject_attribute"]),
        email_attribute=str(row["email_attribute"]),
        name_attribute=str(row["name_attribute"]),
        username_attribute=str(row["username_attribute"]),
        groups_attribute=str(row["groups_attribute"]),
        clock_skew_seconds=int(row["clock_skew_seconds"]),
        provisioning=str(row["provisioning"]),
        default_role=str(row["default_role"]),
        default_team=row["default_team"],
        default_team_role=row["default_team_role"],
        link_by_email=bool(row["link_by_email"]),
        allowed_domains=tuple(str(item) for item in _json_list(row["allowed_domains"])),
        require_email_verified=bool(row["require_email_verified"]),
        assume_email_verified=bool(row["assume_email_verified"]),
        require_amr=tuple(str(item) for item in _json_list(row["require_amr"])),
        group_sync=bool(row["group_sync"]),
        group_mappings=tuple(parse_mappings(_json_list(row["group_mappings"]))),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"],
    )


class SamlStore:
    """
    Read and write the providers and the replay record.

    Args:
        store: The store; the process-wide one by default.
        clock: The time source, replaceable in tests.
    """

    def __init__(
        self, store: NoustStore | None = None, clock: Callable[[], float] | None = None
    ) -> None:
        self._store = store
        self._clock = clock or time.time

    @property
    def store(self) -> NoustStore:
        """The store the tables live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def now(self) -> float:
        """
        Returns:
            The current time, UNIX seconds.
        """
        return self._clock()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT with fixed clauses.
            params: Its parameters.

        Returns:
            The rows.
        """
        return list(self.store._get_connection().execute(sql, params).fetchall())

    def list_providers(self) -> list[SamlProvider]:
        """
        Returns:
            Every configured provider, by name.
        """
        rows = self._rows(f"SELECT {_COLUMNS} FROM saml_providers ORDER BY name")  # noqa: S608 - fixed columns
        return [_provider(row) for row in rows]

    def get_provider(self, name: str) -> SamlProvider | None:
        """
        Args:
            name: The provider's name.

        Returns:
            The provider, or None.
        """
        rows = self._rows(
            f"SELECT {_COLUMNS} FROM saml_providers WHERE name = ?",  # noqa: S608 - fixed columns
            ((name or "").strip().lower(),),
        )
        return _provider(rows[0]) if rows else None

    def save_provider(self, provider: SamlProvider) -> None:
        """
        Write a provider, replacing the one of the same name.

        Args:
            provider: The validated provider.
        """
        values = (
            provider.name,
            provider.label,
            int(provider.enabled),
            provider.idp_entity_id,
            provider.sso_url,
            json.dumps(list(provider.certificates)),
            provider.sp_entity_id,
            provider.subject_attribute,
            provider.email_attribute,
            provider.name_attribute,
            provider.username_attribute,
            provider.groups_attribute,
            provider.clock_skew_seconds,
            provider.provisioning,
            provider.default_role,
            provider.default_team,
            provider.default_team_role,
            int(provider.link_by_email),
            json.dumps(list(provider.allowed_domains)),
            int(provider.require_email_verified),
            int(provider.assume_email_verified),
            json.dumps(list(provider.require_amr)),
            int(provider.group_sync),
            json.dumps([mapping.to_dict() for mapping in provider.group_mappings]),
            provider.created_at,
            provider.created_by,
            provider.updated_at,
            provider.updated_by,
        )
        updates = ", ".join(
            f"{column.strip()} = excluded.{column.strip()}"
            for column in _COLUMNS.split(",")
            if column.strip() not in ("name", "created_at", "created_by")
        )
        placeholders = ", ".join("?" for _ in values)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO saml_providers ({_COLUMNS}) VALUES ({placeholders}) "  # noqa: S608 - fixed columns
                f"ON CONFLICT(name) DO UPDATE SET {updates}",
                values,
            )

    def delete_provider(self, name: str) -> bool:
        """
        Remove a provider and the sign-ins in flight with it. Identities stay: filed under its
        entity id, a provider configured again for the same one finds them.

        Args:
            name: The provider's name.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM oidc_flows WHERE provider = ?", (name,))
            cursor.execute("DELETE FROM oidc_tickets WHERE provider = ?", (name,))
            cursor.execute("DELETE FROM saml_providers WHERE name = ?", (name,))
            return cursor.rowcount == 1

    def remember_assertion(self, assertion_hash: str, expires_at: float) -> bool:
        """
        Record an accepted assertion, and say whether it was new.

        Args:
            assertion_hash: The digest of the provider and the assertion's ID.
            expires_at: When it could no longer be valid, UNIX seconds; it is forgotten then.

        Returns:
            True when it had not been seen; False for a replay.
        """
        now = self.now()
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM saml_assertions WHERE expires_at < ?", (now,))
            cursor.execute(
                "INSERT OR IGNORE INTO saml_assertions (assertion_hash, expires_at) VALUES (?, ?)",
                (assertion_hash, expires_at),
            )
            return cursor.rowcount == 1
