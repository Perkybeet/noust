# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Noust DNS in the store: this server's part in it, and the zones a primary serves.

Four tables of schema v16. ``authdns_server`` is one row: the role (``primary`` keeps the zones
here, ``secondary`` copies them from a primary), the addresses the server answers on, the name
it is known by as a name server, and the firewall rules Noust made for it (all it will close).
``authdns_zones`` and ``authdns_records`` are the primary's zones: the editor writes them and the
zone files are rendered from them, so the engine holds no state Noust must reconcile. The SOA
serial is the engine's (it signs and increments), so it is not kept here. ``authdns_secondaries``
are the servers a primary notifies and lets transfer its zones.

No secret is a column: the transfer key is a file in the secret store and the signing keys are
the engine's.

:class:`AuthDnsStore` is the only reader and writer. Every value is judged by
:mod:`noust.validators.dns_zone` before it is written, whoever asks; what a zone may contain
together (a CNAME and another record at one name) is :mod:`noust.managers.authdns.zones`'s.
"""

from __future__ import annotations

import ipaddress
import json
import sqlite3
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, TypeVar

from noust.core.exceptions import ValidationError
from noust.core.timeutil import utc_now_iso
from noust.validators.dns_zone import (
    check_contact,
    check_hostname,
    check_nameservers,
    check_record,
    check_record_name,
    check_record_type,
    check_ttl,
    check_zone_name,
)

if TYPE_CHECKING:
    from noust.core.store import NoustStore

__all__ = [
    "MAX_RECORDS_PER_ZONE",
    "MAX_ZONES",
    "PRIMARY",
    "SECONDARY",
    "AuthDnsRecord",
    "AuthDnsSecondary",
    "AuthDnsServer",
    "AuthDnsStore",
    "AuthDnsZone",
    "ZoneSnapshot",
    "check_address",
]

_T = TypeVar("_T")

#: The server keeps the zones and their records here.
PRIMARY = "primary"

#: The server copies the zones from a primary.
SECONDARY = "secondary"

#: What one server is asked to hold: a zone file is read whole, and a person edits these.
MAX_ZONES = 500
MAX_RECORDS_PER_ZONE = 5000

#: The most addresses a server answers on.
MAX_ADDRESSES = 16

_MAX_ACTOR = 128


def check_address(value: object, *, field_name: str = "address") -> str:
    """
    Judge an address a server answers on or is reached at.

    Args:
        value: An IPv4 or IPv6 address.
        field_name: The request field, for the error.

    Returns:
        The address in its canonical form.

    Raises:
        ValidationError: It is not an address, or it cannot be one a server listens on or is
            reached at (unspecified, multicast, loopback).
    """
    if not isinstance(value, str):
        raise ValidationError(f"The {field_name} must be text", field=field_name)
    try:
        address = ipaddress.ip_address(value.strip(" "))
    except ValueError:
        raise ValidationError(
            f"{str(value)[:60]!r} is not an IP address",
            details="Write an IPv4 or IPv6 address, such as 203.0.113.7.",
            field=field_name,
        ) from None
    if address.is_unspecified or address.is_multicast or address.is_loopback:
        raise ValidationError(
            f"{address} cannot be an address of a name server",
            details="Use an address other servers and resolvers can reach.",
            field=field_name,
        )
    return str(address)


def _kept(row: _T | None) -> _T:
    """
    Args:
        row: What was just read back after a write.

    Returns:
        It.

    Raises:
        ValidationError: It is not there, which only a store another process emptied between the
            write and the read can cause.
    """
    if row is None:
        raise ValidationError("That was written and could not be read back", field="store")
    return row


def _actor(value: str | None) -> str | None:
    """
    Args:
        value: Who is acting.

    Returns:
        It, when it can be kept.

    Raises:
        ValidationError: It is too long.
    """
    if value is not None and len(value) > _MAX_ACTOR:
        raise ValidationError("The name of whoever acts is too long to keep", field="actor")
    return value


@dataclass(frozen=True)
class AuthDnsServer:
    """
    This server's part in Noust DNS.

    Attributes:
        engine: The name server program (``knot``).
        role: ``primary`` or ``secondary``.
        hostname: The name this server is known by as a name server (``ns1.example.net``).
        listen: The addresses it answers on.
        primary_address: For a secondary, where it transfers the zones from.
        firewall: What Noust opened in the firewall: ``{"backend", "active", "made": [...],
            "note"}``.
        created_at: When it was set up.
        created_by: Who set it up.
        updated_at: When it last changed.
    """

    engine: str
    role: str
    hostname: str | None = None
    listen: tuple[str, ...] = ()
    primary_address: str | None = None
    firewall: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    created_by: str | None = None
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The server for the API and the CLI.
        """
        return {
            "engine": self.engine,
            "role": self.role,
            "hostname": self.hostname,
            "listen": list(self.listen),
            "primary_address": self.primary_address,
            "firewall": self.firewall,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class AuthDnsZone:
    """
    A zone a primary serves.

    Attributes:
        name: The zone, ``example.com``.
        nameservers: The name servers it names (its apex NS records), in order.
        contact: The address the SOA names, ``hostmaster@example.com``.
        default_ttl: The TTL of a record that has none.
        dnssec: The zone is signed.
        created_at: When it was made.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    nameservers: tuple[str, ...]
    contact: str
    default_ttl: int = 3600
    dnssec: bool = False
    created_at: str = ""
    created_by: str | None = None
    updated_at: str = ""
    updated_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The zone for the API and the CLI.
        """
        return {
            "name": self.name,
            "nameservers": list(self.nameservers),
            "contact": self.contact,
            "default_ttl": self.default_ttl,
            "dnssec": self.dnssec,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class AuthDnsRecord:
    """
    A record of a zone.

    Attributes:
        id: Its number; None until it is saved.
        zone: The zone.
        name: Its name relative to the zone; ``@`` is the zone itself.
        type: ``A``, ``AAAA``, ``CNAME``, ``MX``, ``NS``, ``TXT``, ``SRV``, ``CAA``, ``PTR`` or ``DS``.
        content: What it holds, in the canonical form of :func:`~noust.validators.dns_zone.check_record`.
        ttl: Seconds to live; None for the zone's default.
        created_at: When it was made.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    zone: str
    name: str
    type: str
    content: str
    ttl: int | None = None
    id: int | None = None
    created_at: str = ""
    created_by: str | None = None
    updated_at: str = ""
    updated_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The record for the API and the CLI.
        """
        return {
            "id": self.id,
            "zone": self.zone,
            "name": self.name,
            "type": self.type,
            "content": self.content,
            "ttl": self.ttl,
            "created_at": self.created_at,
            "created_by": self.created_by,
            "updated_at": self.updated_at,
            "updated_by": self.updated_by,
        }


@dataclass(frozen=True)
class AuthDnsSecondary:
    """
    A server a primary notifies and lets transfer its zones.

    Attributes:
        address: Where it is reached.
        hostname: The name it is known by as a name server.
        added_at: When it was added.
        added_by: Who added it.
    """

    address: str
    hostname: str | None = None
    added_at: str = ""
    added_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The secondary for the API and the CLI.
        """
        return {
            "address": self.address,
            "hostname": self.hostname,
            "added_at": self.added_at,
            "added_by": self.added_by,
        }


@dataclass(frozen=True)
class ZoneSnapshot:
    """
    A zone as it was, to put it back when the engine does not take a change.

    Attributes:
        zone: The zone row.
        records: Its records.
    """

    zone: AuthDnsZone
    records: tuple[AuthDnsRecord, ...]


def _server(row: sqlite3.Row) -> AuthDnsServer:
    return AuthDnsServer(
        engine=row["engine"],
        role=row["role"],
        hostname=row["hostname"],
        listen=tuple(json.loads(row["listen"] or "[]")),
        primary_address=row["primary_address"],
        firewall=json.loads(row["firewall"] or "{}"),
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
    )


def _zone(row: sqlite3.Row) -> AuthDnsZone:
    return AuthDnsZone(
        name=row["name"],
        nameservers=tuple(json.loads(row["nameservers"] or "[]")),
        contact=row["contact"],
        default_ttl=int(row["default_ttl"]),
        dnssec=bool(row["dnssec"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


def _record(row: sqlite3.Row) -> AuthDnsRecord:
    return AuthDnsRecord(
        id=int(row["id"]),
        zone=row["zone"],
        name=row["name"],
        type=row["type"],
        content=row["content"],
        ttl=None if row["ttl"] is None else int(row["ttl"]),
        created_at=row["created_at"],
        created_by=row["created_by"],
        updated_at=row["updated_at"],
        updated_by=row["updated_by"],
    )


class AuthDnsStore:
    """
    Read and write Noust DNS.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        if self._store is None:
            from noust.core.store import get_store

            self._store = get_store()
        return self._store

    # -- this server ---------------------------------------------------------------------

    def server(self) -> AuthDnsServer | None:
        """
        Returns:
            This server's part in Noust DNS, or None when it has none.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM authdns_server WHERE id = 1")
            .fetchone()
        )
        return None if row is None else _server(row)

    def save_server(self, server: AuthDnsServer, *, actor: str | None = None) -> AuthDnsServer:
        """
        Record this server's part, replacing what was there.

        Args:
            server: What to keep.
            actor: Who sets it.

        Returns:
            What was kept.

        Raises:
            ValidationError: The role, the hostname or an address cannot be kept.
        """
        if server.role not in (PRIMARY, SECONDARY):
            raise ValidationError("A server is a primary or a secondary", field="role")
        if not server.engine or len(server.engine) > 32:
            raise ValidationError("The name server program cannot be kept", field="engine")
        hostname = (
            None
            if server.hostname in (None, "")
            else check_hostname(server.hostname, field="hostname")
        )
        if hostname == ".":
            raise ValidationError("A name server has a name", field="hostname")
        if len(server.listen) > MAX_ADDRESSES:
            raise ValidationError(
                f"A server answers on at most {MAX_ADDRESSES} addresses", field="listen"
            )
        listen = tuple(check_address(a, field_name="listen") for a in server.listen)
        primary = (
            None
            if server.primary_address is None
            else check_address(server.primary_address, field_name="primary_address")
        )
        now = utc_now_iso("seconds")
        previous = self.server()
        created = previous.created_at if previous is not None else now
        created_by = previous.created_by if previous is not None else _actor(actor)
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                INSERT INTO authdns_server
                    (id, engine, role, hostname, listen, primary_address, firewall,
                     created_at, created_by, updated_at)
                VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    engine = excluded.engine, role = excluded.role, hostname = excluded.hostname,
                    listen = excluded.listen, primary_address = excluded.primary_address,
                    firewall = excluded.firewall, updated_at = excluded.updated_at
                """,
                (
                    server.engine,
                    server.role,
                    hostname,
                    json.dumps(list(listen)),
                    primary,
                    json.dumps(server.firewall, sort_keys=True),
                    created,
                    created_by,
                    now,
                ),
            )
        return _kept(self.server())

    def delete_server(self) -> bool:
        """
        Forget this server's part, with its zones, records and secondaries.

        Returns:
            True when it had one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM authdns_records")
            cursor.execute("DELETE FROM authdns_zones")
            cursor.execute("DELETE FROM authdns_secondaries")
            cursor.execute("DELETE FROM authdns_server")
            return cursor.rowcount > 0

    # -- zones ---------------------------------------------------------------------------

    def zones(self) -> list[AuthDnsZone]:
        """
        Returns:
            Every zone, by name.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM authdns_zones ORDER BY name")
            .fetchall()
        )
        return [_zone(row) for row in rows]

    def zone(self, name: str) -> AuthDnsZone | None:
        """
        Args:
            name: The zone.

        Returns:
            It, or None when there is none of that name.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM authdns_zones WHERE name = ?", (name,))
            .fetchone()
        )
        return None if row is None else _zone(row)

    def create_zone(self, zone: AuthDnsZone, *, actor: str | None = None) -> AuthDnsZone:
        """
        Add a zone.

        Args:
            zone: What to keep.
            actor: Who adds it.

        Returns:
            The zone as kept.

        Raises:
            ValidationError: A value cannot be kept, the zone exists, or there are too many.
        """
        name = check_zone_name(zone.name)
        nameservers = check_nameservers(list(zone.nameservers))
        contact = check_contact(zone.contact)
        ttl = check_ttl(zone.default_ttl, field="default_ttl")
        if len(self.zones()) >= MAX_ZONES:
            raise ValidationError(
                f"This server already serves {MAX_ZONES} zones",
                details="Serve the rest from another server.",
                field="zone",
            )
        now = utc_now_iso("seconds")
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    """
                    INSERT INTO authdns_zones
                        (name, nameservers, contact, default_ttl, dnssec, created_at, created_by,
                         updated_at, updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        name,
                        json.dumps(nameservers),
                        contact,
                        ttl,
                        int(zone.dnssec),
                        now,
                        _actor(actor),
                        now,
                        _actor(actor),
                    ),
                )
        except sqlite3.IntegrityError:
            raise ValidationError(
                f"{name} is already a zone of this server",
                details=f"Change it: noust dns zone show {name}",
                field="zone",
            ) from None
        return _kept(self.zone(name))

    def update_zone(
        self,
        name: str,
        *,
        nameservers: list[str] | None = None,
        contact: str | None = None,
        default_ttl: int | None = None,
        dnssec: bool | None = None,
        actor: str | None = None,
    ) -> AuthDnsZone:
        """
        Change a zone; what is not given stays.

        Args:
            name: The zone.
            nameservers: Its name servers.
            contact: The SOA's contact.
            default_ttl: The TTL of a record that has none.
            dnssec: Whether it is signed.
            actor: Who changes it.

        Returns:
            The zone as it is now.

        Raises:
            ValidationError: There is no such zone, or a value cannot be kept.
        """
        current = self.zone(name)
        if current is None:
            raise ValidationError(f"{name} is not a zone of this server", field="zone")
        servers = (
            current.nameservers if nameservers is None else tuple(check_nameservers(nameservers))
        )
        with self.store._transaction() as cursor:
            cursor.execute(
                """
                UPDATE authdns_zones SET nameservers = ?, contact = ?, default_ttl = ?, dnssec = ?,
                    updated_at = ?, updated_by = ? WHERE name = ?
                """,
                (
                    json.dumps(list(servers)),
                    current.contact if contact is None else check_contact(contact),
                    current.default_ttl
                    if default_ttl is None
                    else check_ttl(default_ttl, field="default_ttl"),
                    int(current.dnssec if dnssec is None else dnssec),
                    utc_now_iso("seconds"),
                    _actor(actor),
                    name,
                ),
            )
        return _kept(self.zone(name))

    def delete_zone(self, name: str) -> bool:
        """
        Remove a zone with its records.

        Args:
            name: The zone.

        Returns:
            True when there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM authdns_zones WHERE name = ?", (name,))
            return cursor.rowcount > 0

    # -- records -------------------------------------------------------------------------

    def records(self, zone: str) -> list[AuthDnsRecord]:
        """
        Args:
            zone: The zone.

        Returns:
            Its records, apex first, then by name, type and content.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM authdns_records WHERE zone = ? "
                "ORDER BY name != '@', name, type, content",
                (zone,),
            )
            .fetchall()
        )
        return [_record(row) for row in rows]

    def record(self, record_id: int) -> AuthDnsRecord | None:
        """
        Args:
            record_id: The record's number.

        Returns:
            It, or None.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM authdns_records WHERE id = ?", (record_id,))
            .fetchone()
        )
        return None if row is None else _record(row)

    def add_record(
        self,
        zone: str,
        name: str,
        record_type: str,
        content: str,
        *,
        ttl: int | None = None,
        actor: str | None = None,
    ) -> AuthDnsRecord:
        """
        Add a record.

        Args:
            zone: The zone.
            name: Its name relative to the zone.
            record_type: Its type.
            content: What it holds.
            ttl: Seconds to live; None for the zone's default.
            actor: Who adds it.

        Returns:
            The record as kept.

        Raises:
            ValidationError: A value cannot be kept, the zone does not exist, the record is
                already there, or the zone is full.
        """
        if self.zone(zone) is None:
            raise ValidationError(f"{zone} is not a zone of this server", field="zone")
        kind = check_record_type(record_type)
        relative = check_record_name(name, zone)
        canonical = check_record(kind, content)
        seconds = None if ttl is None else check_ttl(ttl)
        count = (
            self.store._get_connection()
            .execute("SELECT COUNT(*) FROM authdns_records WHERE zone = ?", (zone,))
            .fetchone()[0]
        )
        if count >= MAX_RECORDS_PER_ZONE:
            raise ValidationError(
                f"{zone} already has {MAX_RECORDS_PER_ZONE} records",
                details="Split the zone, or remove the ones you no longer use.",
                field="zone",
            )
        now = utc_now_iso("seconds")
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    """
                    INSERT INTO authdns_records
                        (zone, name, type, ttl, content, created_at, created_by, updated_at,
                         updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        zone,
                        relative,
                        kind,
                        seconds,
                        canonical,
                        now,
                        _actor(actor),
                        now,
                        _actor(actor),
                    ),
                )
                new_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            raise ValidationError(
                f"{relative} already has that {kind} record in {zone}", field="content"
            ) from None
        return _kept(self.record(int(new_id or 0)))

    def update_record(
        self,
        record_id: int,
        *,
        content: str | None = None,
        ttl: int | None = None,
        clear_ttl: bool = False,
        actor: str | None = None,
    ) -> AuthDnsRecord:
        """
        Change what a record holds or its TTL; its name and type are what identify it.

        Args:
            record_id: The record's number.
            content: The new content.
            ttl: The new TTL.
            clear_ttl: Go back to the zone's TTL.
            actor: Who changes it.

        Returns:
            The record as it is now.

        Raises:
            ValidationError: There is no such record, a value cannot be kept, or the zone
                already has a record with that content.
        """
        current = self.record(record_id)
        if current is None:
            raise ValidationError(f"There is no record {record_id}", field="id")
        canonical = current.content if content is None else check_record(current.type, content)
        seconds = None if clear_ttl else (current.ttl if ttl is None else check_ttl(ttl))
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "UPDATE authdns_records SET content = ?, ttl = ?, updated_at = ?, "
                    "updated_by = ? WHERE id = ?",
                    (canonical, seconds, utc_now_iso("seconds"), _actor(actor), record_id),
                )
        except sqlite3.IntegrityError:
            raise ValidationError(
                f"{current.name} already has that {current.type} record in {current.zone}",
                field="content",
            ) from None
        return _kept(self.record(record_id))

    def delete_record(self, record_id: int) -> bool:
        """
        Args:
            record_id: The record's number.

        Returns:
            True when there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM authdns_records WHERE id = ?", (record_id,))
            return cursor.rowcount > 0

    # -- putting a zone back -------------------------------------------------------------

    def snapshot(self, name: str) -> ZoneSnapshot | None:
        """
        Args:
            name: The zone.

        Returns:
            The zone as it is, to be put back with :meth:`restore`; None when there is none.
        """
        zone = self.zone(name)
        if zone is None:
            return None
        return ZoneSnapshot(zone, tuple(self.records(name)))

    def restore(self, snapshot: ZoneSnapshot) -> None:
        """
        Put a zone back as it was, whatever has happened to it since.

        Args:
            snapshot: What :meth:`snapshot` returned.
        """
        zone = snapshot.zone
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM authdns_zones WHERE name = ?", (zone.name,))
            cursor.execute(
                """
                INSERT INTO authdns_zones
                    (name, nameservers, contact, default_ttl, dnssec, created_at, created_by,
                     updated_at, updated_by)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    zone.name,
                    json.dumps(list(zone.nameservers)),
                    zone.contact,
                    zone.default_ttl,
                    int(zone.dnssec),
                    zone.created_at,
                    zone.created_by,
                    zone.updated_at,
                    zone.updated_by,
                ),
            )
            for record in snapshot.records:
                cursor.execute(
                    """
                    INSERT INTO authdns_records
                        (id, zone, name, type, ttl, content, created_at, created_by, updated_at,
                         updated_by)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.zone,
                        record.name,
                        record.type,
                        record.ttl,
                        record.content,
                        record.created_at,
                        record.created_by,
                        record.updated_at,
                        record.updated_by,
                    ),
                )

    # -- secondaries ---------------------------------------------------------------------

    def secondaries(self) -> list[AuthDnsSecondary]:
        """
        Returns:
            The servers this primary notifies and lets transfer its zones, by address.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM authdns_secondaries ORDER BY added_at, address")
            .fetchall()
        )
        return [
            AuthDnsSecondary(
                address=row["address"],
                hostname=row["hostname"],
                added_at=row["added_at"],
                added_by=row["added_by"],
            )
            for row in rows
        ]

    def add_secondary(
        self, address: str, hostname: str | None = None, *, actor: str | None = None
    ) -> AuthDnsSecondary:
        """
        Let a server transfer this primary's zones, and notify it when they change.

        Args:
            address: Where it is reached.
            hostname: The name it is known by as a name server.
            actor: Who adds it.

        Returns:
            The secondary as kept (adding one again changes its name).

        Raises:
            ValidationError: A value cannot be kept.
        """
        where = check_address(address)
        name = None if not hostname else check_hostname(hostname, field="hostname")
        if name == ".":
            raise ValidationError("A name server has a name", field="hostname")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO authdns_secondaries (address, hostname, added_at, added_by) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(address) DO UPDATE SET hostname = excluded.hostname",
                (where, name, utc_now_iso("seconds"), _actor(actor)),
            )
        return next(s for s in self.secondaries() if s.address == where)

    def remove_secondary(self, address: str) -> bool:
        """
        Args:
            address: The secondary's address.

        Returns:
            True when there was one.
        """
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM authdns_secondaries WHERE address = ?", (check_address(address),)
            )
            return cursor.rowcount > 0
