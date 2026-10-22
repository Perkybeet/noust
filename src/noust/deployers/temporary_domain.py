# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A name for a server that has none yet.

A server without a domain can still serve an application over HTTPS: ``sslip.io`` is a public
DNS service that answers a name made of an address with that address, so
``203-0-113-10.sslip.io`` resolves to ``203.0.113.10`` and a certificate can be ordered for it.
It is good for trying an application out and for the days before a real name points here.

This module only works the name out. Giving it to an application is the ordinary
:func:`noust.deployers.domains.add_domain`, so the DNS check, the site and the certificate go
through the one implementation of "an application answers on another name". A name made this way
is *temporary*: the console and ``noust domain list`` say so, because a certificate authority's
limits are per registered domain and sslip.io is everybody's, and because the name belongs to a
service that is not Noust's to promise.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

from noust.core.config import Config
from noust.core.exceptions import DomainError
from noust.deployers.domains import machine_addresses
from noust.managers.dns.provider import is_public_address

#: The service that answers a name made of an address with that address.
SUFFIX = "sslip.io"


@dataclass(frozen=True)
class TemporaryName:
    """
    A name made of this server's public address.

    Attributes:
        address: The public address.
        domain: The name that resolves to it.
        service: The service that answers it.
        declared: Whether the address was declared (``dns.public_addresses``) rather than
            read from a network interface.
    """

    address: str
    domain: str
    service: str = SUFFIX
    declared: bool = False


def name_for(address: str) -> str:
    """
    Make the name that resolves to an address.

    Args:
        address: A public IPv4 or IPv6 address.

    Returns:
        ``203-0-113-10.sslip.io``, or ``2001-db8--1.sslip.io`` for an IPv6 address
        (its colons are dashes, and ``::`` is ``--``).

    Raises:
        DomainError: It is not an address, or is not one that may be published.
    """
    try:
        parsed = ipaddress.ip_address(address.strip())
    except ValueError:
        raise DomainError(
            f"{address!r} is not an address", details="Use an IPv4 or IPv6 address."
        ) from None
    if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
        parsed = parsed.ipv4_mapped  # ::ffff:8.8.8.8 is 8.8.8.8 written the other way
    if not is_public_address(str(parsed)):
        raise DomainError(
            f"{parsed} is not a public address",
            details="A private, loopback or shared address cannot be reached from the internet: "
            "no certificate authority could validate a name made of it.",
        )
    label = str(parsed).replace(".", "-") if parsed.version == 4 else str(parsed).replace(":", "-")
    if label.startswith("-") or label.endswith("-"):
        # sslip.io reads a leading or trailing "--" for an address that starts or ends with "::";
        # a hostname label cannot start or end with a dash, so a zero is written there.
        label = (
            ("0" if label.startswith("-") else "") + label + ("0" if label.endswith("-") else "")
        )
    return f"{label}.{SUFFIX}"


def is_temporary(domain: str) -> bool:
    """
    Args:
        domain: A name.

    Returns:
        Whether it is one of the names made here.
    """
    return domain.lower().endswith(f".{SUFFIX}")


def suggest() -> TemporaryName:
    """
    Work out the temporary name of this server.

    The address is the one declared as public (``dns.public_addresses``), else the first
    public one an interface holds.

    Returns:
        The name and the address it is made of.

    Raises:
        DomainError: This server has no public address (it is behind NAT, and none is declared).
    """
    declared = [str(address) for address in Config().get("dns.public_addresses") or []]
    for address in declared:
        if is_public_address(address):
            return TemporaryName(address=address, domain=name_for(address), declared=True)
    found = sorted(
        (address for address in machine_addresses() if is_public_address(address)),
        key=lambda address: (ipaddress.ip_address(address).version, address),
    )
    if not found:
        raise DomainError(
            "This server has no public address to make a name of",
            details="It is behind NAT, or its address is private. Declare the public one with: "
            "noust config set dns.public_addresses 203.0.113.10",
        )
    return TemporaryName(address=found[0], domain=name_for(found[0]))
