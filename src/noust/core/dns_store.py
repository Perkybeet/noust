# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The DNS accounts Noust uses, and the records it manages in them.

Three tables of the networking fragment of schema v14. ``dns_providers`` is one
account at a provider: its kind, whether adding a domain creates the record
without asking, and what is not secret about it (an account id). The token is a
secret file (:mod:`noust.core.secrets`), never a column. ``dns_records`` is a
record Noust manages at a provider, and the fact that decides whether it may
ever delete it: ``owned`` is true only for a record Noust created; one that was
already there is adopted as it was and left alone when it goes.
``dns_record_refs`` says which applications use a record, so one shared by two
applications goes when the last lets go.

:class:`DnsStore` is the only reader and writer; the rules of ownership are
:mod:`noust.managers.dns.records`'s.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class DnsAccount:
    """
    An account at a DNS provider.

    Attributes:
        name: What it is called here; also the name of its secret.
        kind: The provider (``cloudflare``, ``digitalocean``...).
        automatic: Create the records of a domain when it is added, without asking.
        settings: What is not secret about it: an account id, the credentials
            template's non-secret fields, the propagation wait.
        verified_at: When its token last worked, ISO 8601 UTC.
        last_error: Why it last did not, in the provider's own words.
        created_at: When it was added.
        created_by: Who added it.
    """

    name: str
    kind: str
    automatic: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    verified_at: str | None = None
    last_error: str | None = None
    created_at: str = ""
    created_by: str | None = None


@dataclass
class ManagedRecord:
    """
    A DNS record Noust manages.

    Attributes:
        id: Row id; None until it is saved.
        uuid: Identifies it in the ownership comment at the provider.
        provider: The account.
        zone_id: The provider's id of the zone.
        zone_name: The zone, ``example.com``.
        hostname: The record's name.
        record_type: ``A``, ``AAAA``, ``CNAME`` or ``TXT``.
        content: What it holds.
        remote_id: The provider's id of the record.
        owned: Noust created it. False for a record that was already there.
        proxied: The provider proxies it (Cloudflare's orange cloud).
        created_at: When it was recorded.
        created_by: Who asked.
        unreferenced_since: When the last application let go of it, ISO 8601 UTC;
            None while an application uses it.
    """

    uuid: str
    provider: str
    zone_id: str
    zone_name: str
    hostname: str
    record_type: str
    content: str
    remote_id: str | None = None
    owned: bool = True
    proxied: bool = False
    created_at: str = ""
    created_by: str | None = None
    unreferenced_since: str | None = None
    id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record for the API and the CLI.
        """
        return {
            "id": self.id,
            "uuid": self.uuid,
            "provider": self.provider,
            "zone": self.zone_name,
            "hostname": self.hostname,
            "type": self.record_type,
            "content": self.content,
            "owned": self.owned,
            "proxied": self.proxied,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "unreferenced_since": self.unreferenced_since,
        }


class DnsStore:
    """
    Read and write DNS accounts, managed records and who uses them.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    # -- accounts ------------------------------------------------------------

    def list_accounts(self) -> list[DnsAccount]:
        """
        Returns:
            Every account, by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM dns_providers ORDER BY name")
            .fetchall()
        )
        return [_account(row) for row in rows]

    def get_account(self, name: str) -> DnsAccount | None:
        """
        Args:
            name: The account's name.

        Returns:
            The account, or None when there is none of that name.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM dns_providers WHERE name = ?", (name,))
            .fetchone()
        )
        return None if row is None else _account(row)

    def save_account(self, account: DnsAccount) -> None:
        """
        Add an account, or change one.

        Args:
            account: What to keep.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO dns_providers
                    (name, kind, automatic, settings, verified_at, last_error, created_at, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    kind = excluded.kind,
                    automatic = excluded.automatic,
                    settings = excluded.settings,
                    verified_at = excluded.verified_at,
                    last_error = excluded.last_error
                """,
                (
                    account.name,
                    account.kind,
                    int(account.automatic),
                    json.dumps(account.settings, sort_keys=True),
                    account.verified_at,
                    account.last_error,
                    account.created_at or _now(),
                    account.created_by,
                ),
            )

    def mark_verified(self, name: str, error: str | None) -> None:
        """
        Record whether an account's token worked the last time it was tried.

        Args:
            name: The account.
            error: The provider's words when it did not; None when it did.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE dns_providers SET verified_at = ?, last_error = ? WHERE name = ?",
                (_now() if error is None else None, error, name),
            )

    def delete_account(self, name: str) -> bool:
        """
        Remove an account and, with it, the records it managed.

        Args:
            name: The account.

        Returns:
            True when it existed.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM dns_providers WHERE name = ?", (name,))
            return cursor.rowcount > 0

    # -- records -------------------------------------------------------------

    def list_records(self, provider: str | None = None) -> list[ManagedRecord]:
        """
        Args:
            provider: Only this account's; every account's when None.

        Returns:
            The managed records, by hostname.
        """
        query = "SELECT * FROM dns_records"
        params: tuple[Any, ...] = ()
        if provider is not None:
            query += " WHERE provider = ?"
            params = (provider,)
        rows = (
            self.store._get_connection()
            .execute(query + " ORDER BY hostname, record_type", params)
            .fetchall()
        )
        return [_record(row) for row in rows]

    def get_record(self, record_id: int) -> ManagedRecord | None:
        """
        Args:
            record_id: The row id.

        Returns:
            The record, or None.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM dns_records WHERE id = ?", (record_id,))
            .fetchone()
        )
        return None if row is None else _record(row)

    def find_record(self, provider: str, hostname: str, record_type: str) -> ManagedRecord | None:
        """
        Args:
            provider: The account.
            hostname: The record's name.
            record_type: Its type.

        Returns:
            The managed record, or None when Noust manages none like it.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM dns_records WHERE provider = ? AND hostname = ? AND record_type = ?",
                (provider, hostname, record_type),
            )
            .fetchone()
        )
        return None if row is None else _record(row)

    def save_record(self, record: ManagedRecord) -> ManagedRecord:
        """
        Record a managed record, or update the row of one already recorded.

        Args:
            record: What to keep; ``id`` is filled in.

        Returns:
            The record.
        """
        if not record.uuid:
            record.uuid = uuid.uuid4().hex[:12]
        if not record.created_at:
            record.created_at = _now()
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO dns_records
                    (uuid, provider, zone_id, zone_name, hostname, record_type, content,
                     remote_id, owned, proxied, created_at, created_by, unreferenced_since)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, hostname, record_type) DO UPDATE SET
                    zone_id = excluded.zone_id,
                    zone_name = excluded.zone_name,
                    content = excluded.content,
                    remote_id = excluded.remote_id,
                    owned = excluded.owned,
                    proxied = excluded.proxied,
                    unreferenced_since = excluded.unreferenced_since
                """,
                (
                    record.uuid,
                    record.provider,
                    record.zone_id,
                    record.zone_name,
                    record.hostname,
                    record.record_type,
                    record.content,
                    record.remote_id,
                    int(record.owned),
                    int(record.proxied),
                    record.created_at,
                    record.created_by,
                    record.unreferenced_since,
                ),
            )
            cursor.execute(
                "SELECT id, uuid FROM dns_records WHERE provider = ? AND hostname = ? "
                "AND record_type = ?",
                (record.provider, record.hostname, record.record_type),
            )
            row = cursor.fetchone()
        record.id = int(row["id"])
        record.uuid = str(row["uuid"])
        return record

    def delete_record(self, record_id: int) -> None:
        """
        Forget a managed record.

        Args:
            record_id: The row id.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM dns_records WHERE id = ?", (record_id,))

    # -- references ----------------------------------------------------------

    def add_ref(self, record_id: int, app_domain: str) -> None:
        """
        Say an application uses a record.

        Args:
            record_id: The record.
            app_domain: The application's primary domain.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT OR IGNORE INTO dns_record_refs (record_id, app_domain, created_at) "
                "VALUES (?, ?, ?)",
                (record_id, app_domain, _now()),
            )
            cursor.execute(
                "UPDATE dns_records SET unreferenced_since = NULL WHERE id = ?", (record_id,)
            )

    def remove_ref(self, record_id: int, app_domain: str) -> int:
        """
        Say an application no longer uses a record.

        Args:
            record_id: The record.
            app_domain: The application's primary domain.

        Returns:
            How many applications still use it. When none does, the moment is
            noted, which the sweep waits out before deleting.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM dns_record_refs WHERE record_id = ? AND app_domain = ?",
                (record_id, app_domain),
            )
            cursor.execute(
                "SELECT COUNT(*) AS n FROM dns_record_refs WHERE record_id = ?", (record_id,)
            )
            left = int(cursor.fetchone()["n"])
            if left == 0:
                cursor.execute(
                    "UPDATE dns_records SET unreferenced_since = COALESCE(unreferenced_since, ?) "
                    "WHERE id = ?",
                    (_now(), record_id),
                )
        return left

    def refs(self, record_id: int) -> list[str]:
        """
        Args:
            record_id: The record.

        Returns:
            The applications that use it, sorted.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT app_domain FROM dns_record_refs WHERE record_id = ? ORDER BY app_domain",
                (record_id,),
            )
            .fetchall()
        )
        return [str(row["app_domain"]) for row in rows]

    def records_of(self, app_domain: str) -> list[ManagedRecord]:
        """
        Args:
            app_domain: An application's primary domain.

        Returns:
            The managed records it uses, by hostname.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT r.* FROM dns_records r JOIN dns_record_refs f ON f.record_id = r.id "
                "WHERE f.app_domain = ? ORDER BY r.hostname, r.record_type",
                (app_domain,),
            )
            .fetchall()
        )
        return [_record(row) for row in rows]


def _account(row: Any) -> DnsAccount:
    """
    Args:
        row: A ``dns_providers`` row.

    Returns:
        The account.
    """
    try:
        settings = json.loads(row["settings"] or "{}")
    except ValueError:
        settings = {}
    return DnsAccount(
        name=str(row["name"]),
        kind=str(row["kind"]),
        automatic=bool(row["automatic"]),
        settings=settings if isinstance(settings, dict) else {},
        verified_at=row["verified_at"],
        last_error=row["last_error"],
        created_at=str(row["created_at"]),
        created_by=row["created_by"],
    )


def _record(row: Any) -> ManagedRecord:
    """
    Args:
        row: A ``dns_records`` row.

    Returns:
        The record.
    """
    return ManagedRecord(
        id=int(row["id"]),
        uuid=str(row["uuid"]),
        provider=str(row["provider"]),
        zone_id=str(row["zone_id"]),
        zone_name=str(row["zone_name"]),
        hostname=str(row["hostname"]),
        record_type=str(row["record_type"]),
        content=str(row["content"]),
        remote_id=row["remote_id"],
        owned=bool(row["owned"]),
        proxied=bool(row["proxied"]),
        created_at=str(row["created_at"]),
        created_by=row["created_by"],
        unreferenced_since=row["unreferenced_since"],
    )


def _now() -> str:
    """
    Returns:
        The current time, ISO 8601 in UTC.
    """
    return datetime.now(timezone.utc).isoformat()
