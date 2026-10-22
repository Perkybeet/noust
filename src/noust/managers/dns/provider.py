# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a DNS provider is to Noust: zones and the records in them.

One interface, so the ownership rules of :mod:`noust.managers.dns.records` and the
DNS-01 challenge of :mod:`noust.managers.dns.challenge` are written once whichever
provider answers. A provider only moves records: it never decides whether one may
be created, replaced or deleted. That is the manager's, which is why this module
also holds the two small rules the manager and every provider need to agree on -
when an address is one a public record may carry, and when two record values name
the same thing.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from dataclasses import dataclass

from noust.core.exceptions import IntegrationError

#: The record types Noust manages.
RECORD_TYPES: tuple[str, ...] = ("A", "AAAA", "CNAME", "TXT")

#: Seconds to live of a record Noust creates; 1 is "automatic" at Cloudflare.
AUTOMATIC_TTL = 1


class DnsProviderError(IntegrationError):
    """
    A DNS provider refused a request or could not be reached.

    Attributes:
        status: The HTTP status it answered, or 0 when it never did.
    """

    def __init__(
        self, message: str, *, status: int = 0, details: str = "", output: str | None = None
    ) -> None:
        """
        Args:
            message: What failed.
            status: The provider's HTTP status, or 0.
            details: How to fix it.
            output: The provider's own message, verbatim.
        """
        super().__init__(message, details, output=output)
        self.status = status


@dataclass(frozen=True)
class Zone:
    """
    A DNS zone at a provider.

    Attributes:
        id: The provider's id.
        name: The zone, lower case, ``example.com``.
        account_id: The provider's account that owns it, when it says.
    """

    id: str
    name: str
    account_id: str | None = None


@dataclass(frozen=True)
class RemoteRecord:
    """
    A record as the provider has it.

    Attributes:
        id: The provider's id of the record.
        type: ``A``, ``AAAA``, ``CNAME`` or ``TXT``.
        name: Its full name, lower case.
        content: What it holds, TXT quotes removed.
        ttl: Seconds to live.
        proxied: The provider proxies it, when it can.
        comment: The note kept with it, where the provider has them.
    """

    id: str
    type: str
    name: str
    content: str
    ttl: int | None = None
    proxied: bool | None = None
    comment: str | None = None


def is_public_address(address: str) -> bool:
    """
    Tell whether an address may be published in a public DNS record.

    Private, loopback, link-local, unique-local, shared (CGNAT: Tailscale uses
    it), reserved and multicast addresses are not: a record carrying one points
    visitors, and the certificate authority, at nothing.

    Args:
        address: An IPv4 or IPv6 address.

    Returns:
        True when it is a routable unicast address.
    """
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        return False
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped
    if isinstance(parsed, ipaddress.IPv4Address) and parsed in ipaddress.ip_network(
        "100.64.0.0/10"
    ):
        return False
    return parsed.is_global and not parsed.is_multicast


def same_value(first: str | None, second: str | None) -> bool:
    """
    Tell whether two record values name the same thing.

    Addresses are compared as addresses, so ``2001:db8::1`` and
    ``2001:0DB8:0:0::1`` match; names ignore case and a trailing dot; anything else
    (a TXT) must be identical once its quotes are off.

    Args:
        first: One value.
        second: The other.

    Returns:
        True when they are the same.
    """
    if first is None or second is None:
        return False
    a, b = unquote(first).strip(), unquote(second).strip()
    try:
        return ipaddress.ip_address(a) == ipaddress.ip_address(b)
    except ValueError:
        pass
    return a == b or a.lower().rstrip(".") == b.lower().rstrip(".")


def unquote(content: str) -> str:
    """
    Args:
        content: A record's content as a provider returns it.

    Returns:
        The content without the quotes a provider wraps a TXT value in.
    """
    text = content.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        return text[1:-1]
    return text


def record_type_for(address: str) -> str:
    """
    Args:
        address: An IP address.

    Returns:
        ``AAAA`` for IPv6, else ``A``.
    """
    return "AAAA" if ":" in address else "A"


def zone_for(hostname: str, zones: list[Zone]) -> Zone | None:
    """
    Find the zone a name belongs to: the longest zone it is in.

    Args:
        hostname: The name.
        zones: The provider's zones.

    Returns:
        The zone, or None when the name is in none of them.
    """
    name = hostname.lower().rstrip(".")
    matches = [zone for zone in zones if name == zone.name or name.endswith(f".{zone.name}")]
    return max(matches, key=lambda zone: len(zone.name), default=None)


class DnsProvider(ABC):
    """A DNS account at one provider, moving records and nothing else."""

    #: The provider's kind, as the catalog names it.
    kind: str

    #: Whether the provider keeps a note with each record. Where it does, the ownership marker is
    #: written there and read back; where it does not, Noust's own record of what it made stands.
    comments: bool = True

    @abstractmethod
    def zones(self) -> list[Zone]:
        """
        Returns:
            Every zone the account can manage.

        Raises:
            DnsProviderError: The token was refused or the provider failed.
        """

    @abstractmethod
    def find_records(self, zone: Zone, name: str, record_type: str) -> list[RemoteRecord]:
        """
        Args:
            zone: The zone.
            name: The record's full name.
            record_type: Its type.

        Returns:
            Every record of that name and type.

        Raises:
            DnsProviderError: The provider failed.
        """

    @abstractmethod
    def get_record(self, zone: Zone, record_id: str) -> RemoteRecord | None:
        """
        Args:
            zone: The zone.
            record_id: The provider's id.

        Returns:
            The record, or None when it no longer exists.

        Raises:
            DnsProviderError: The provider failed.
        """

    @abstractmethod
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
        Args:
            zone: The zone.
            name: The record's full name.
            record_type: Its type.
            content: What it holds.
            ttl: Seconds to live.
            proxied: Let the provider proxy it.
            comment: A note to keep with it.

        Returns:
            The record as created.

        Raises:
            DnsProviderError: The provider refused it.
        """

    @abstractmethod
    def update_record(self, zone: Zone, record_id: str, content: str) -> RemoteRecord:
        """
        Change only what a record holds: its proxying, TTL and comment stay.

        Args:
            zone: The zone.
            record_id: The provider's id.
            content: The new content.

        Returns:
            The record as it is now.

        Raises:
            DnsProviderError: The provider refused it.
        """

    @abstractmethod
    def delete_record(self, zone: Zone, record_id: str) -> bool:
        """
        Args:
            zone: The zone.
            record_id: The provider's id.

        Returns:
            True when it was deleted, False when it was already gone.

        Raises:
            DnsProviderError: The provider refused it.
        """
