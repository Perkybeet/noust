# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Persistence for SCIM provisioning: the sources that push users and groups, the roster and the groups.

The tables are declared in ``schema_v14.FEDERATION_SQL``. The rules about who may be created,
disabled or given a role are :class:`~noust.core.accounts.scim.ScimService`'s; this module only
reads and writes rows. A source's bearer token is never stored: only its SHA-256, which is
enough to find it and, the token being 256 random bits, to compare it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.accounts.oidc_model import GroupMapping, parse_mappings

if TYPE_CHECKING:
    from noust.core.store import NoustStore

#: The protocol its people are named under in the audit trail and in a refusal's advice.
PROTOCOL_SCIM = "scim"

_SOURCE_COLUMNS = (
    "name, label, enabled, token_hash, token_hint, token_created_at, last_used_at, "
    "last_used_ip, default_role, default_team, default_team_role, link_by_email, "
    "allowed_domains, group_sync, group_mappings, created_at, created_by, updated_at, updated_by"
)
_USER_COLUMNS = (
    "id, source, account_id, external_id, user_name, disabled_by_scim, mapped_role, "
    "created_at, updated_at"
)
_GROUP_COLUMNS = "id, source, display_name, external_id, created_at, updated_at"


def _json_list(raw: Any) -> list[Any]:
    """
    Args:
        raw: A JSON list as stored.

    Returns:
        The list, or an empty one for anything unreadable.
    """
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return []
    return value if isinstance(value, list) else []


@dataclass(frozen=True)
class ScimSource:
    """
    One provider that pushes users and groups here, without its token.

    It follows the same admission rules as every identity source
    (:class:`~noust.core.accounts.federation.Admission`): what a new account starts as,
    the team it joins, the domains it may come from, and what each group means.

    Attributes:
        name: Its short name, in the audit trail and the base address.
        label: What a person reads about it.
        enabled: Whether its token is accepted at all.
        token_hash: SHA-256 of the bearer token it authenticates with.
        token_hint: The last characters of the token, to tell tokens apart.
        token_created_at: When the token was issued.
        last_used_at: When it last authenticated a request.
        last_used_ip: From where.
        default_role: The role of an account it creates; at most ``admin``.
        default_team: The team a new account joins, or None for the default one.
        default_team_role: Their role there.
        link_by_email: Whether a user whose e-mail is an existing account's is attached to it.
        allowed_domains: The e-mail domains it may provision; any when empty.
        group_sync: Whether the groups it pushes decide roles, memberships and bindings.
        group_mappings: What each group means.
        created_at: When it was configured.
        created_by: Who configured it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    label: str
    token_hash: str
    token_hint: str
    token_created_at: float
    enabled: bool = True
    last_used_at: float | None = None
    last_used_ip: str | None = None
    default_role: str = "viewer"
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool = False
    allowed_domains: tuple[str, ...] = ()
    group_sync: bool = False
    group_mappings: tuple[GroupMapping, ...] = ()
    created_at: float = 0.0
    created_by: str | None = None
    updated_at: float = 0.0
    updated_by: str | None = None

    @property
    def protocol(self) -> str:
        """The protocol its people are named under."""
        return PROTOCOL_SCIM

    @property
    def provisioning(self) -> str:
        """It always creates the accounts it is told about."""
        return "auto"

    @property
    def require_email_verified(self) -> bool:
        """The provider owns the directory: the address is its to vouch for."""
        return False

    @property
    def assume_email_verified(self) -> bool:
        """The address a provider pushes is taken as verified."""
        return True

    @property
    def require_amr(self) -> tuple[str, ...]:
        """There is no sign-in here, so no method to require."""
        return ()

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The source as the API and ``--json`` show it; the token is never here.
        """
        return {
            "name": self.name,
            "label": self.label,
            "enabled": self.enabled,
            "token_hint": self.token_hint,
            "token_created_at": self.token_created_at,
            "last_used_at": self.last_used_at,
            "last_used_ip": self.last_used_ip,
            "default_role": self.default_role,
            "default_team": self.default_team,
            "default_team_role": self.default_team_role,
            "link_by_email": self.link_by_email,
            "allowed_domains": list(self.allowed_domains),
            "group_sync": self.group_sync,
            "group_mappings": [mapping.to_dict() for mapping in self.group_mappings],
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class ScimUser:
    """
    A user a source provisioned: the id it knows them by and the account that is.

    Attributes:
        id: The id the provider uses, opaque.
        source: The source's name.
        account_id: The account.
        external_id: The provider's own id for them, when it sent one.
        user_name: The ``userName`` it sent, unique within the source.
        disabled_by_scim: Whether the provider turned the account off.
        mapped_role: The instance role a group mapping last gave.
        created_at: When it was provisioned.
        updated_at: When it last changed.
    """

    id: str
    source: str
    account_id: int
    external_id: str | None
    user_name: str
    disabled_by_scim: bool
    mapped_role: str | None
    created_at: float
    updated_at: float


@dataclass(frozen=True)
class ScimGroup:
    """
    A group a source pushed.

    Attributes:
        id: The id the provider uses, opaque.
        source: The source's name.
        display_name: Its name, which a group mapping is written against.
        external_id: The provider's own id for it, when it sent one.
        created_at: When it was pushed.
        updated_at: When it last changed.
    """

    id: str
    source: str
    display_name: str
    external_id: str | None
    created_at: float
    updated_at: float


def _source(row: sqlite3.Row) -> ScimSource:
    """
    Args:
        row: A row selected with :data:`_SOURCE_COLUMNS`.

    Returns:
        The source.
    """
    return ScimSource(
        name=str(row["name"]),
        label=str(row["label"]),
        enabled=bool(row["enabled"]),
        token_hash=str(row["token_hash"]),
        token_hint=str(row["token_hint"]),
        token_created_at=float(row["token_created_at"]),
        last_used_at=row["last_used_at"],
        last_used_ip=row["last_used_ip"],
        default_role=str(row["default_role"]),
        default_team=row["default_team"],
        default_team_role=row["default_team_role"],
        link_by_email=bool(row["link_by_email"]),
        allowed_domains=tuple(str(item) for item in _json_list(row["allowed_domains"])),
        group_sync=bool(row["group_sync"]),
        group_mappings=tuple(parse_mappings(_json_list(row["group_mappings"]))),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
        updated_at=float(row["updated_at"]),
        updated_by=row["updated_by"],
    )


def _user(row: sqlite3.Row) -> ScimUser:
    """
    Args:
        row: A row selected with :data:`_USER_COLUMNS`.

    Returns:
        The user.
    """
    return ScimUser(
        id=str(row["id"]),
        source=str(row["source"]),
        account_id=int(row["account_id"]),
        external_id=row["external_id"],
        user_name=str(row["user_name"]),
        disabled_by_scim=bool(row["disabled_by_scim"]),
        mapped_role=row["mapped_role"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


def _group(row: sqlite3.Row) -> ScimGroup:
    """
    Args:
        row: A row selected with :data:`_GROUP_COLUMNS`.

    Returns:
        The group.
    """
    return ScimGroup(
        id=str(row["id"]),
        source=str(row["source"]),
        display_name=str(row["display_name"]),
        external_id=row["external_id"],
        created_at=float(row["created_at"]),
        updated_at=float(row["updated_at"]),
    )


class ScimStore:
    """
    Read and write the sources, the roster and the groups.

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

    # -------------------------------------------------------------- sources

    def list_sources(self) -> list[ScimSource]:
        """
        Returns:
            Every configured source, by name.
        """
        rows = self._rows(f"SELECT {_SOURCE_COLUMNS} FROM scim_sources ORDER BY name")  # noqa: S608 - fixed columns
        return [_source(row) for row in rows]

    def get_source(self, name: str) -> ScimSource | None:
        """
        Args:
            name: The source's name.

        Returns:
            The source, or None.
        """
        rows = self._rows(
            f"SELECT {_SOURCE_COLUMNS} FROM scim_sources WHERE name = ?",  # noqa: S608 - fixed columns
            ((name or "").strip().lower(),),
        )
        return _source(rows[0]) if rows else None

    def source_by_token(self, token_hash: str) -> ScimSource | None:
        """
        Args:
            token_hash: SHA-256 of a presented bearer token.

        Returns:
            The source that token belongs to, or None.
        """
        rows = self._rows(
            f"SELECT {_SOURCE_COLUMNS} FROM scim_sources WHERE token_hash = ?",  # noqa: S608 - fixed columns
            (token_hash,),
        )
        return _source(rows[0]) if rows else None

    def save_source(self, source: ScimSource) -> None:
        """
        Write a source, replacing the one of the same name.

        Args:
            source: The validated source.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO scim_sources ({_SOURCE_COLUMNS}) "  # noqa: S608 - fixed columns
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(name) DO UPDATE SET label = excluded.label, "
                "enabled = excluded.enabled, token_hash = excluded.token_hash, "
                "token_hint = excluded.token_hint, token_created_at = excluded.token_created_at, "
                "default_role = excluded.default_role, default_team = excluded.default_team, "
                "default_team_role = excluded.default_team_role, "
                "link_by_email = excluded.link_by_email, "
                "allowed_domains = excluded.allowed_domains, group_sync = excluded.group_sync, "
                "group_mappings = excluded.group_mappings, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (
                    source.name,
                    source.label,
                    int(source.enabled),
                    source.token_hash,
                    source.token_hint,
                    source.token_created_at,
                    source.last_used_at,
                    source.last_used_ip,
                    source.default_role,
                    source.default_team,
                    source.default_team_role,
                    int(source.link_by_email),
                    json.dumps(list(source.allowed_domains)),
                    int(source.group_sync),
                    json.dumps([mapping.to_dict() for mapping in source.group_mappings]),
                    source.created_at,
                    source.created_by,
                    source.updated_at,
                    source.updated_by,
                ),
            )

    def touch_source(self, name: str, client_ip: str, *, min_interval: float = 0.0) -> None:
        """
        Record that a source's token authenticated a request.

        Args:
            name: The source.
            client_ip: From where.
            min_interval: Do nothing if it was recorded more recently than this many seconds
                ago: a provider's synchronisation is a burst of requests.
        """
        now = self.now()
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE scim_sources SET last_used_at = ?, last_used_ip = ? WHERE name = ? "
                "AND (last_used_at IS NULL OR last_used_at <= ?)",
                (now, client_ip, name, now - min_interval),
            )

    def delete_source(self, name: str) -> bool:
        """
        Remove a source with its roster and groups. The accounts stay: they are people's.

        Args:
            name: The source's name.

        Returns:
            Whether there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM scim_sources WHERE name = ?", (name,))
            return cursor.rowcount == 1

    # ---------------------------------------------------------------- users

    def add_user(self, user: ScimUser) -> None:
        """
        Args:
            user: The user to record.

        Raises:
            sqlite3.IntegrityError: When the name or the account is already in the roster.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO scim_users ({_USER_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",  # noqa: S608 - fixed columns
                (
                    user.id,
                    user.source,
                    user.account_id,
                    user.external_id,
                    user.user_name,
                    int(user.disabled_by_scim),
                    user.mapped_role,
                    user.created_at,
                    user.updated_at,
                ),
            )

    def get_user(self, source: str, user_id: str) -> ScimUser | None:
        """
        Args:
            source: The source.
            user_id: The id the provider uses.

        Returns:
            The user, or None.
        """
        rows = self._rows(
            f"SELECT {_USER_COLUMNS} FROM scim_users WHERE source = ? AND id = ?",  # noqa: S608 - fixed columns
            (source, user_id),
        )
        return _user(rows[0]) if rows else None

    def find_user(
        self,
        source: str,
        *,
        user_name: str | None = None,
        external_id: str | None = None,
        account_id: int | None = None,
    ) -> ScimUser | None:
        """
        Args:
            source: The source.
            user_name: A ``userName``, compared without regard to case.
            external_id: The provider's own id.
            account_id: An account.

        Returns:
            The first user that matches, or None.
        """
        clauses = ["source = ?"]
        params: list[Any] = [source]
        if user_name is not None:
            clauses.append("user_name = ? COLLATE NOCASE")
            params.append(user_name)
        if external_id is not None:
            clauses.append("external_id = ?")
            params.append(external_id)
        if account_id is not None:
            clauses.append("account_id = ?")
            params.append(account_id)
        rows = self._rows(
            f"SELECT {_USER_COLUMNS} FROM scim_users WHERE {' AND '.join(clauses)} LIMIT 1",  # noqa: S608 - fixed clauses
            tuple(params),
        )
        return _user(rows[0]) if rows else None

    def list_users(
        self,
        source: str,
        *,
        user_name: str | None = None,
        external_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[int, list[ScimUser]]:
        """
        Args:
            source: The source.
            user_name: Only the user with this ``userName`` (case-insensitive).
            external_id: Only the user with this provider id.
            offset: How many to skip.
            limit: How many to return at most.

        Returns:
            How many match, and the page asked for, oldest first.
        """
        clauses = ["source = ?"]
        params: list[Any] = [source]
        if user_name is not None:
            clauses.append("user_name = ? COLLATE NOCASE")
            params.append(user_name)
        if external_id is not None:
            clauses.append("external_id = ?")
            params.append(external_id)
        where = " AND ".join(clauses)
        count_sql = f"SELECT COUNT(*) AS n FROM scim_users WHERE {where}"  # noqa: S608 - fixed clauses
        total = int(self._rows(count_sql, tuple(params))[0]["n"])
        rows = self._rows(
            f"SELECT {_USER_COLUMNS} FROM scim_users WHERE {where} "  # noqa: S608 - fixed clauses
            "ORDER BY created_at, rowid LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return total, [_user(row) for row in rows]

    def count_users(self, source: str) -> int:
        """
        Args:
            source: The source.

        Returns:
            How many users it has provisioned.
        """
        return int(
            self._rows("SELECT COUNT(*) AS n FROM scim_users WHERE source = ?", (source,))[0]["n"]
        )

    def update_user(self, user_id: str, **changes: Any) -> None:
        """
        Change a user, and stamp the change. With nothing to change it only stamps.

        Args:
            user_id: The user's id.
            **changes: ``user_name``, ``external_id``, ``disabled_by_scim`` or ``mapped_role``.

        Raises:
            ValueError: For a column that is not one of those.
        """
        allowed = {"user_name", "external_id", "disabled_by_scim", "mapped_role"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Not a column of a SCIM user: {', '.join(sorted(unknown))}")
        values = [int(v) if isinstance(v, bool) else v for v in changes.values()]
        sets = "".join(f"{column} = ?, " for column in changes)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"UPDATE scim_users SET {sets}updated_at = ? WHERE id = ?",  # noqa: S608 - names checked above
                (*values, self.now(), user_id),
            )

    def delete_user(self, user_id: str) -> None:
        """
        Args:
            user_id: The user's id; its group memberships go with it.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM scim_users WHERE id = ?", (user_id,))

    # --------------------------------------------------------------- groups

    def add_group(self, group: ScimGroup) -> None:
        """
        Args:
            group: The group to record.

        Raises:
            sqlite3.IntegrityError: When the source already has a group of that name.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                f"INSERT INTO scim_groups ({_GROUP_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?)",  # noqa: S608 - fixed columns
                (
                    group.id,
                    group.source,
                    group.display_name,
                    group.external_id,
                    group.created_at,
                    group.updated_at,
                ),
            )

    def get_group(self, source: str, group_id: str) -> ScimGroup | None:
        """
        Args:
            source: The source.
            group_id: The id the provider uses.

        Returns:
            The group, or None.
        """
        rows = self._rows(
            f"SELECT {_GROUP_COLUMNS} FROM scim_groups WHERE source = ? AND id = ?",  # noqa: S608 - fixed columns
            (source, group_id),
        )
        return _group(rows[0]) if rows else None

    def list_groups(
        self,
        source: str,
        *,
        display_name: str | None = None,
        external_id: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> tuple[int, list[ScimGroup]]:
        """
        Args:
            source: The source.
            display_name: Only the group with this name (case-insensitive).
            external_id: Only the group with this provider id.
            offset: How many to skip.
            limit: How many to return at most.

        Returns:
            How many match, and the page asked for, oldest first.
        """
        clauses = ["source = ?"]
        params: list[Any] = [source]
        if display_name is not None:
            clauses.append("display_name = ? COLLATE NOCASE")
            params.append(display_name)
        if external_id is not None:
            clauses.append("external_id = ?")
            params.append(external_id)
        where = " AND ".join(clauses)
        count_sql = f"SELECT COUNT(*) AS n FROM scim_groups WHERE {where}"  # noqa: S608 - fixed clauses
        total = int(self._rows(count_sql, tuple(params))[0]["n"])
        rows = self._rows(
            f"SELECT {_GROUP_COLUMNS} FROM scim_groups WHERE {where} "  # noqa: S608 - fixed clauses
            "ORDER BY created_at, rowid LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
        return total, [_group(row) for row in rows]

    def update_group(self, group_id: str, **changes: Any) -> None:
        """
        Args:
            group_id: The group's id.
            **changes: ``display_name`` or ``external_id``.

        Raises:
            ValueError: For a column that is not one of those.
        """
        unknown = set(changes) - {"display_name", "external_id"}
        if unknown:
            raise ValueError(f"Not a column of a SCIM group: {', '.join(sorted(unknown))}")
        if not changes:
            return
        sets = ", ".join(f"{column} = ?" for column in changes)
        with self.store._transaction() as cursor:
            cursor.execute(
                f"UPDATE scim_groups SET {sets}, updated_at = ? WHERE id = ?",  # noqa: S608 - names checked above
                (*changes.values(), self.now(), group_id),
            )

    def delete_group(self, group_id: str) -> None:
        """
        Args:
            group_id: The group's id; its memberships go with it.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM scim_groups WHERE id = ?", (group_id,))

    def members(self, group_id: str) -> list[str]:
        """
        Args:
            group_id: The group.

        Returns:
            The ids of the users in it, oldest member first.
        """
        rows = self._rows(
            "SELECT m.user_id FROM scim_group_members m JOIN scim_users u ON u.id = m.user_id "
            "WHERE m.group_id = ? ORDER BY u.created_at, u.rowid",
            (group_id,),
        )
        return [str(row["user_id"]) for row in rows]

    def set_members(self, group_id: str, user_ids: Iterable[str]) -> None:
        """
        Replace who is in a group.

        Args:
            group_id: The group.
            user_ids: The users that are in it now.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM scim_group_members WHERE group_id = ?", (group_id,))
            cursor.executemany(
                "INSERT OR IGNORE INTO scim_group_members (group_id, user_id) VALUES (?, ?)",
                [(group_id, user_id) for user_id in dict.fromkeys(user_ids)],
            )

    def groups_of(self, user_id: str) -> list[ScimGroup]:
        """
        Args:
            user_id: A user.

        Returns:
            The groups they are in, by name.
        """
        rows = self._rows(
            f"SELECT {', '.join('g.' + c.strip() for c in _GROUP_COLUMNS.split(','))} "  # noqa: S608 - fixed columns
            "FROM scim_groups g JOIN scim_group_members m ON m.group_id = g.id "
            "WHERE m.user_id = ? ORDER BY g.display_name",
            (user_id,),
        )
        return [_group(row) for row in rows]
