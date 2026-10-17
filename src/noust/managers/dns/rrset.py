# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Providers that keep records in sets: a name and a type hold several values.

Route 53, Hetzner and Gandi do not number the records of a name. A set (``www`` of type ``A``)
is read and written whole, and what Noust calls a record is one value in it. This base turns
the three things such a provider can do (read a set, write a set, remove a set) into the
record-at-a-time interface the ownership rules are written against
(:class:`~noust.managers.dns.provider.DnsProvider`), so each provider is its API and nothing else.

A record's id is made of what identifies it in its set (``name|TYPE|value``, the value
percent-encoded): the provider never gave it one, and any record can be found again from it. Two
records of a set that hold the same value are one record, which is what every provider does too.

Providers that cannot keep a note with a record say so (``comments`` false): the ownership marker
of :mod:`noust.managers.dns.records` then rests on Noust's own record of what it made and on the
record still being exactly what it created, which is weaker than a marker the operator can see at
the provider and is documented as such.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from urllib.parse import quote, unquote

from noust.managers.dns.provider import (
    AUTOMATIC_TTL,
    DnsProvider,
    DnsProviderError,
    RemoteRecord,
    Zone,
    same_value,
)

#: A record type that holds one value only.
SINGLE_VALUE_TYPES = frozenset({"CNAME"})


@dataclass(frozen=True)
class Rrset:
    """
    A set of records with one name and type.

    Attributes:
        name: The full name, lower case, without the final dot.
        type: ``A``, ``AAAA``, ``CNAME`` or ``TXT``.
        ttl: Seconds to live; None for the zone's default.
        values: Each value with its note, where the provider keeps notes.
        foreign: Why this set is not one Noust may read or write (an alias, a routing policy);
            empty for a plain set. Writing it back would replace what the operator made.
    """

    name: str
    type: str
    ttl: int | None
    values: tuple[tuple[str, str | None], ...]
    foreign: str = ""


def record_id(name: str, record_type: str, value: str) -> str:
    """
    Args:
        name: The record's full name.
        record_type: Its type.
        value: What it holds.

    Returns:
        The id Noust uses for it.
    """
    return f"{name.lower()}|{record_type}|{quote(value, safe='')}"


def parse_record_id(identifier: str) -> tuple[str, str, str] | None:
    """
    Args:
        identifier: An id made by :func:`record_id`.

    Returns:
        ``(name, type, value)``, or None when it is not one.
    """
    parts = identifier.split("|", 2)
    if len(parts) != 3 or not all(parts):
        return None
    return parts[0], parts[1], unquote(parts[2])


class RrsetProvider(DnsProvider):
    """A provider whose records live in sets, moving them one value at a time."""

    #: Whether the provider keeps a note with each record.
    comments: bool = False

    @abstractmethod
    def read_rrset(self, zone: Zone, name: str, record_type: str) -> Rrset | None:
        """
        Args:
            zone: The zone.
            name: The set's full name.
            record_type: Its type.

        Returns:
            The set, or None when there is none.

        Raises:
            DnsProviderError: The provider failed.
        """

    @abstractmethod
    def write_rrset(self, zone: Zone, rrset: Rrset, *, existed: bool) -> None:
        """
        Create a set, or replace the values of one that exists.

        Args:
            zone: The zone.
            rrset: What it must hold afterwards.
            existed: Whether the set is already at the provider.

        Raises:
            DnsProviderError: The provider refused it.
        """

    @abstractmethod
    def remove_rrset(self, zone: Zone, name: str, record_type: str) -> bool:
        """
        Args:
            zone: The zone.
            name: The set's full name.
            record_type: Its type.

        Returns:
            True when it was removed, False when it was not there.

        Raises:
            DnsProviderError: The provider refused it.
        """

    # -- the interface the ownership rules use ---------------------------------

    def find_records(self, zone: Zone, name: str, record_type: str) -> list[RemoteRecord]:
        """
        Args:
            zone: The zone.
            name: The record's full name.
            record_type: Its type.

        Returns:
            Every value of that name and type, as records.

        Raises:
            DnsProviderError: The provider failed.
        """
        rrset = self.read_rrset(zone, name.lower(), record_type)
        if rrset is None:
            return []
        if rrset.foreign:
            raise self._refused(rrset)
        return self._records(rrset)

    def get_record(self, zone: Zone, record_id: str) -> RemoteRecord | None:
        """
        Args:
            zone: The zone.
            record_id: An id made by :func:`record_id`.

        Returns:
            The record, or None when it no longer exists.

        Raises:
            DnsProviderError: The provider failed.
        """
        parsed = parse_record_id(record_id)
        if parsed is None:
            return None
        name, record_type, value = parsed
        rrset = self.read_rrset(zone, name, record_type)
        if rrset is None or rrset.foreign:
            return None
        return next(
            (record for record in self._records(rrset) if same_value(record.content, value)), None
        )

    def create_record(
        self,
        zone: Zone,
        name: str,
        record_type: str,
        content: str,
        *,
        ttl: int = AUTOMATIC_TTL,
        proxied: bool = False,
        comment: str | None = None,
    ) -> RemoteRecord:
        """
        Add a value to the set of a name and type, creating the set if it is not there.

        Args:
            zone: The zone.
            name: The record's full name.
            record_type: Its type.
            content: What it holds.
            ttl: Seconds to live; used only when the set is new.
            proxied: Ignored: no provider here proxies.
            comment: A note to keep with it, where the provider keeps them.

        Returns:
            The record as created.

        Raises:
            DnsProviderError: The provider refused it, or the type holds one value and the
                name has another.
        """
        name = name.lower()
        existing = self.read_rrset(zone, name, record_type)
        if existing is not None and existing.foreign:
            raise self._refused(existing)
        values = list(existing.values) if existing is not None else []
        if existing is not None:
            for record in self._records(existing):
                if same_value(record.content, content):
                    return record
        if record_type in SINGLE_VALUE_TYPES and values:
            raise DnsProviderError(
                f"{name} already has a {record_type} record, and a name has only one",
                details="Replace that record or choose another name.",
            )
        values.append((content, comment if self.comments else None))
        rrset = Rrset(
            name,
            record_type,
            existing.ttl if existing is not None else (None if ttl <= AUTOMATIC_TTL else ttl),
            tuple(values),
        )
        self.write_rrset(zone, rrset, existed=existing is not None)
        return RemoteRecord(
            id=record_id(name, record_type, content),
            type=record_type,
            name=name,
            content=content,
            ttl=rrset.ttl,
            comment=comment if self.comments else None,
        )

    def update_record(self, zone: Zone, record_id_: str, content: str) -> RemoteRecord:
        """
        Change what one value of a set holds; its note stays.

        Args:
            zone: The zone.
            record_id_: An id made by :func:`record_id`.
            content: The new content.

        Returns:
            The record as it is now.

        Raises:
            DnsProviderError: The record is gone, or the provider refused it.
        """
        parsed = parse_record_id(record_id_)
        rrset = self.read_rrset(zone, parsed[0], parsed[1]) if parsed else None
        if parsed is None or rrset is None or rrset.foreign:
            raise DnsProviderError("The record to change is not there any more", status=404)
        name, record_type, value = parsed
        if not any(same_value(current, value) for current, _ in rrset.values):
            raise DnsProviderError("The record to change is not there any more", status=404)
        values = [
            (content, note) if same_value(current, value) else (current, note)
            for current, note in rrset.values
        ]
        note = next((note for current, note in values if same_value(current, content)), None)
        self.write_rrset(zone, Rrset(name, record_type, rrset.ttl, tuple(values)), existed=True)
        return RemoteRecord(
            id=record_id(name, record_type, content),
            type=record_type,
            name=name,
            content=content,
            ttl=rrset.ttl,
            comment=note,
        )

    def delete_record(self, zone: Zone, record_id_: str) -> bool:
        """
        Remove one value of a set, and the set with its last.

        Args:
            zone: The zone.
            record_id_: An id made by :func:`record_id`.

        Returns:
            True when it was removed, False when it was already gone.

        Raises:
            DnsProviderError: The provider refused it.
        """
        parsed = parse_record_id(record_id_)
        if parsed is None:
            return False
        name, record_type, value = parsed
        rrset = self.read_rrset(zone, name, record_type)
        if (
            rrset is None
            or rrset.foreign
            or not any(same_value(current, value) for current, _ in rrset.values)
        ):
            return False
        remaining = tuple(
            (current, note) for current, note in rrset.values if not same_value(current, value)
        )
        if not remaining:
            return self.remove_rrset(zone, name, record_type)
        self.write_rrset(zone, Rrset(name, record_type, rrset.ttl, remaining), existed=True)
        return True

    @staticmethod
    def _refused(rrset: Rrset) -> DnsProviderError:
        """
        Args:
            rrset: A set Noust may not touch.

        Returns:
            The error that says so, naming the set and why.
        """
        return DnsProviderError(
            f"{rrset.name} already has a {rrset.type} record that is {rrset.foreign}",
            details="Noust does not replace it. Choose another name, or remove it at the provider.",
        )

    @staticmethod
    def _records(rrset: Rrset) -> list[RemoteRecord]:
        """
        Args:
            rrset: A set.

        Returns:
            Its values as records.
        """
        return [
            RemoteRecord(
                id=record_id(rrset.name, rrset.type, value),
                type=rrset.type,
                name=rrset.name,
                content=value,
                ttl=rrset.ttl,
                comment=note,
            )
            for value, note in rrset.values
        ]
