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
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from noust.core.config import Config
from noust.core.exceptions import DomainError
from noust.core.store import NoustStore, get_store
from noust.core.utils import domain_to_app_name, sanitize_name
from noust.deployers.domains import machine_addresses
from noust.managers.dns.provider import is_public_address

#: The service that answers a name made of an address with that address.
SUFFIX = "sslip.io"

#: The longest prefix kept. A host label holds 63 characters; the rest is room for the number
#: that tells two applications of one name apart.
MAX_PREFIX = 40

#: How many applications of one name get a number before the search gives up.
_MAX_NUMBER = 99


class NoPublicAddressError(DomainError):
    """
    This server has no public address to make a name of.

    Its own class because it is not a failure of a request but an answer about the server: the
    console asks on every new application, and a server behind NAT says "not here" without an
    error status for the browser to log.
    """


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


def label_of(prefix: str) -> str:
    """
    Make one host label of a name an application is called.

    Args:
        prefix: The name of a repository, a recipe or an application, as typed.

    Returns:
        Lower case letters, digits and single dashes, at most :data:`MAX_PREFIX` long; ``app``
        when nothing is left. A label made only of digits and dashes gets ``app-`` before it:
        sslip.io reads such a label as part of the address it is in front of.
    """
    label = sanitize_name(prefix)[:MAX_PREFIX].strip("-") or "app"
    if re.fullmatch(r"[0-9-]+", label):
        label = f"app-{label}"
    return label


#: What the last part of a source may end in without being part of the name.
_SOURCE_SUFFIXES = (".git", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".zip")


def prefix_of_source(source: str | None) -> str | None:
    """
    Say what an application deployed from a source is called.

    Args:
        source: A repository URL (``https``, ``ssh`` or the ``user@host:team/name`` form), an
            archive URL or a directory.

    Returns:
        The last part of it without ``.git`` or an archive extension, or None when there is
        none (``.``, an address with no path).
    """
    text = (source or "").strip()
    if "://" in text:
        text = urlsplit(text).path
    elif "@" in text:
        text = text.split("#", 1)[0]
    last = text.rstrip("/").rsplit("/", 1)[-1]
    if "@" in last and ":" in last:
        last = last.rsplit(":", 1)[-1]  # user@host:name.git, with no team in front of it
    for suffix in _SOURCE_SUFFIXES:
        if last.lower().endswith(suffix):
            last = last[: -len(suffix)]
            break
    return None if last in ("", ".", "..") else last


def prefix_of_image(image: str | None) -> str | None:
    """
    Say what an application deployed from a registry image is called.

    Args:
        image: ``ghcr.io/team/name:tag``, ``name@sha256:...`` or ``name``.

    Returns:
        The name without its registry, team, tag or digest, or None when there is none.
    """
    last = (image or "").strip().split("@", 1)[0].rstrip("/").rsplit("/", 1)[-1]
    return last.split(":", 1)[0] or None


def application_name(
    *, prefix: str | None = None, source: str | None = None, image: str | None = None
) -> str | None:
    """
    Say what an application is called, from whatever was said about it.

    The one place the order is decided, for the API the console asks and for ``noust create``:
    a name given outright, else the image, else the source.

    Args:
        prefix: A name given outright (a recipe's, or the demo's).
        source: The repository, archive or directory it is deployed from.
        image: The registry image it is deployed from.

    Returns:
        The name, ``app`` when a source or an image was given and has no name in it, or None when
        nothing was said (the name of the server alone is wanted then).
    """
    if prefix:
        return prefix
    if image:
        return prefix_of_image(image) or "app"
    if source:
        return prefix_of_source(source) or "app"
    return None


def name_for(address: str, prefix: str | None = None) -> str:
    """
    Make the name that resolves to an address.

    Args:
        address: A public IPv4 or IPv6 address.
        prefix: What the application is called. sslip.io answers any label in front of the
            address with that address, so one server can name each application apart.

    Returns:
        ``203-0-113-10.sslip.io``, or ``2001-db8--1.sslip.io`` for an IPv6 address
        (its colons are dashes, and ``::`` is ``--``); ``shop.203-0-113-10.sslip.io`` with
        the prefix ``shop``.

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
    host = f"{label}.{SUFFIX}"
    return host if prefix is None else f"{label_of(prefix)}.{host}"


def is_temporary(domain: str) -> bool:
    """
    Args:
        domain: A name.

    Returns:
        Whether it is one of the names made here.
    """
    return domain.lower().endswith(f".{SUFFIX}")


def suggest(prefix: str | None = None, *, store: NoustStore | None = None) -> TemporaryName:
    """
    Work out the temporary name of this server, or of an application on it.

    The address is the one declared as public (``dns.public_addresses``), else the first
    public one an interface holds.

    Args:
        prefix: What the application is called. With one, the name is
            ``<prefix>.<address>.sslip.io`` and is free: when an application or a domain already
            has it the next number is used (``shop-2``), because two applications of one name
            would be one unit, one directory and one user.
        store: The store the names are looked up in (the server's by default).

    Returns:
        The name and the address it is made of.

    Raises:
        DomainError: This server has no public address (it is behind NAT, and none is declared),
            or ninety-nine applications already have the prefix.
    """
    address, declared = _public_address()
    if prefix is None:
        return TemporaryName(address=address, domain=name_for(address), declared=declared)
    taken = _TakenNames(store or get_store())
    label = label_of(prefix)
    host = name_for(address)
    for number in range(1, _MAX_NUMBER + 1):
        # The number is added after the label was cut, so it is never the part that is lost.
        domain = f"{label if number == 1 else f'{label}-{number}'}.{host}"
        if not taken(domain):
            return TemporaryName(address=address, domain=domain, declared=declared)
    raise DomainError(
        f"{_MAX_NUMBER} applications are already called {label!r}",
        details="Give the application another name, or a domain of its own.",
    )


class _TakenNames:
    """
    The names an application cannot be given: a domain anything answers on, or one that makes
    an application name another application already has.
    """

    def __init__(self, store: NoustStore) -> None:
        self._store = store
        self._app_names = {domain_to_app_name(app.domain) for app in store.list_apps()}

    def __call__(self, domain: str) -> bool:
        return (
            self._store.domain_owner(domain) is not None
            or domain_to_app_name(domain) in self._app_names
        )


def _public_address() -> tuple[str, bool]:
    """
    Pick the address a name is made of.

    Returns:
        The address, and whether it was declared rather than read from an interface.

    Raises:
        NoPublicAddressError: This server has no public address.
    """
    declared = [str(address) for address in Config().get("dns.public_addresses") or []]
    for address in declared:
        if is_public_address(address):
            return address, True
    found = sorted(
        (address for address in machine_addresses() if is_public_address(address)),
        key=lambda address: (ipaddress.ip_address(address).version, address),
    )
    if not found:
        raise NoPublicAddressError(
            "This server has no public address to make a name of",
            details="It is behind NAT, or its address is private. Declare the public one with: "
            "noust config set dns.public_addresses 203.0.113.10",
        )
    return found[0], False
