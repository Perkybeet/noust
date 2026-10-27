# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where Noust may be told to send a request, in one place.

Several features let a person with a credential name a host the machine will
then talk to: a notification webhook, a remote backup destination, an archive
to download a source from, a repository index. Each of those turns "configure a
URL" into "make this machine, which runs as root and sits inside the operator's
network, send a request", the shape of a server-side request forgery. The guard
is the same for all of them, so it lives here and is called from the point every
request passes through (rule 3 and 4 of CONTRIBUTING.md), not copied into each
caller:

- a destination is resolved, and every address it resolves to is judged, so a
  name with one public and one internal record is refused;
- an address that is only an IPv4 address in an IPv6 wrapper (IPv4-mapped,
  NAT64, 6to4) is judged by the address inside it;
- the networks refused are this machine and its neighbours: loopback, private
  (RFC 1918), carrier-grade NAT, link-local (which includes the cloud metadata
  address 169.254.169.254), the unspecified address and the IPv6 equivalents;
- an operator who really wants an internal destination names it in
  ``security.allow_private_hosts``, an explicit decision, not a default.

A configured host is judged when it is configured and again when it is used,
because a name can be made to resolve elsewhere in between. The second look
asks a narrower question (:func:`require_public_host` with ``narrow=True``):
only the addresses that can never be a destination, such as the metadata
service, so a backup that has always run to a NAS on the operator's LAN
keeps running the night after an upgrade.

The notifier, the package index and the backup destinations call this module;
:func:`resolve_host` is the one function a test replaces so that the suite never
resolves a real name.

The host is only half of where a request goes. A client of a platform's API
(GitHub, GitLab, Gitea, Coolify) fixes the host and builds the rest from a
repository, a group or a project somebody named, and a name such as ``..`` moves
the request to another endpoint of the same host, with the same credential:
:func:`path_segment` makes a name one segment and :func:`api_path` is what
every such client asks of the path it is about to send.
"""

from __future__ import annotations

import ipaddress
import re
import socket
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import quote, unquote

#: Address family alias.
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address
Network = ipaddress.IPv4Network | ipaddress.IPv6Network

#: Networks a destination must not resolve into unless its host is explicitly
#: trusted. Not the whole IANA special-purpose registry: the ranges that turn a
#: request into a way to reach the machine's own network. Loopback, private
#: networks (RFC 1918), carrier-grade NAT (RFC 6598), link-local (where
#: 169.254.169.254, the cloud metadata address, lives), "this network"
#: (0.0.0.0/8), IPv6's loopback, unique-local and link-local equivalents, and
#: the unspecified address (``::``), which Linux treats as the local host on
#: connect. An address that reaches one of the IPv4 ranges wrapped in an
#: IPv4-mapped, NAT64 or 6to4 IPv6 form is caught by :func:`embedded_ipv4`.
FORBIDDEN_NETWORKS: tuple[Network, ...] = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)

#: The few that are never what an operator means: the aliases of this machine
#: (``0.0.0.0/8``, ``::``) and link-local, where a cloud's metadata service
#: answers. Judged on every use of a destination, whoever configured it and
#: whenever; a LAN, which legitimate destinations live on, is not in this list.
NEVER_A_DESTINATION: tuple[Network, ...] = tuple(
    ipaddress.ip_network(cidr) for cidr in ("0.0.0.0/8", "169.254.0.0/16", "::/128", "fe80::/10")
)

#: RFC 6052's well-known prefix: an address in this /96 carries an IPv4 address
#: in its low 32 bits, so a request to it is a request to that address.
NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")

#: The setting an operator names a trusted internal destination in. A list of
#: host names or address literals, compared exactly (case-insensitively).
ALLOW_SETTING = "security.allow_private_hosts"


#: What a path sent to a platform's API is made of: printable ASCII, nothing that is whitespace
#: or a control character (a header or a second request line in the making), no backslash (some
#: servers read it as a slash) and no ``#``. A name that is not ASCII reaches the path already
#: percent-encoded, by :func:`path_segment`.
_NOT_IN_A_PATH = re.compile(r"[^\x21-\x7e]|[\\#]")

#: What no single name holds, whatever is done with it afterwards.
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]")


class UnsafePath(ValueError):
    """A request path, or a part of one, that would not stay where its caller meant it to."""


class DestinationRefused(ValueError):
    """
    A destination the machine must not send a request to.

    Attributes:
        host: The host that was judged.
        reason: ``internal`` (an address inside a forbidden network),
            ``unresolved`` (the name did not resolve) or ``no_address``.
        address: The address that decided it, for ``internal``.
    """

    def __init__(self, message: str, *, host: str, reason: str, address: str | None = None) -> None:
        super().__init__(message)
        self.host = host
        self.reason = reason
        self.address = address


def resolve_host(host: str) -> tuple[str, ...]:
    """
    Resolve a host to the literal addresses it points at.

    A module-level function so a test replaces it instead of opening a real
    socket: DNS is nondeterministic, reaches outside the sandbox, and would make
    the guard's own tests as slow and flaky as the network they avoid.

    Args:
        host: Hostname or address literal.

    Returns:
        Every address the name resolves to; a literal resolves to itself
        without a lookup.

    Raises:
        OSError: When the name cannot be resolved.
    """
    try:
        return (str(ipaddress.ip_address(host)),)
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None)
    # A link-local sockaddr carries a %scope suffix ipaddress refuses.
    return tuple(sorted({str(info[4][0]).split("%", 1)[0] for info in infos}))


def embedded_ipv4(address: IPAddress) -> ipaddress.IPv4Address | None:
    """
    Extract the IPv4 address an IPv6 address maps or tunnels, if any.

    IPv4-mapped (``::ffff:0:0/96``), NAT64 (``64:ff9b::/96``) and 6to4
    (``2002::/16``) carry an IPv4 address that a network stack dials exactly as
    if it had been given directly, so each is a second route to an address the
    forbidden networks would otherwise catch.

    Args:
        address: A resolved destination address.

    Returns:
        The embedded IPv4 address, or None when ``address`` is IPv4 already or
        carries none.
    """
    if not isinstance(address, ipaddress.IPv6Address):
        return None
    if address.ipv4_mapped is not None:
        return address.ipv4_mapped
    if address.sixtofour is not None:
        return address.sixtofour
    if address in NAT64_PREFIX:
        return ipaddress.IPv4Address(int(address) & 0xFFFFFFFF)
    return None


def _inside(address: IPAddress, networks: Iterable[Network]) -> bool:
    """
    Whether an address, or the IPv4 address it wraps, is in any of the networks.

    Args:
        address: The address to judge.
        networks: The networks to look in.

    Returns:
        True when it, or the IPv4 address inside it, is in one of them.
    """
    networks = tuple(networks)
    if any(address.version == network.version and address in network for network in networks):
        return True
    embedded = embedded_ipv4(address)
    return embedded is not None and _inside(embedded, networks)


def is_forbidden(address: IPAddress) -> bool:
    """
    Report whether an address is inside :data:`FORBIDDEN_NETWORKS`.

    Args:
        address: A resolved destination address.

    Returns:
        True when the address, or the IPv4 address it embeds, is not one a
        request may be sent to by default.
    """
    return _inside(address, FORBIDDEN_NETWORKS)


def is_never_a_destination(address: IPAddress) -> bool:
    """
    Report whether an address is inside :data:`NEVER_A_DESTINATION`.

    Args:
        address: A resolved destination address.

    Returns:
        True for this machine's aliases and link-local addresses.
    """
    return _inside(address, NEVER_A_DESTINATION)


def trusted_hosts(config: Any | None = None) -> frozenset[str]:
    """
    Read the hosts the operator has declared trustworthy though internal.

    Args:
        config: A :class:`~noust.core.config.Config`; the current one when
            omitted.

    Returns:
        The entries of ``security.allow_private_hosts``, lowercased.
    """
    if config is None:
        from noust.core.config import Config

        config = Config()
    return frozenset(str(entry).lower() for entry in (config.get(ALLOW_SETTING) or []))


def require_public_host(
    host: str,
    *,
    allowed: Iterable[str] = (),
    resolver: Callable[[str], tuple[str, ...]] | None = None,
    narrow: bool = False,
    unresolved_ok: bool = False,
) -> None:
    """
    Refuse a host that resolves inside the machine's own networks.

    Args:
        host: A host name or address literal (no scheme, port or path).
        allowed: Hosts exempted by the operator; compared exactly,
            case-insensitively.
        resolver: How to resolve; :func:`resolve_host` (looked up now, so a
            test that replaces it is honoured) when omitted.
        narrow: Judge only :data:`NEVER_A_DESTINATION`, for a destination that
            was accepted when configured and is being used again.
        unresolved_ok: Let a name that does not resolve pass. For a
            destination configured while it is down; it is judged again when
            it is used, and a name that does not resolve cannot be reached.

    Raises:
        DestinationRefused: When the host does not resolve (unless
            ``unresolved_ok``), resolves to nothing, or any address it
            resolves to is in a refused network and the host is not allowed.
    """
    if not host:
        raise DestinationRefused("There is no host to check", host=host, reason="no_address")
    if host.lower() in {entry.lower() for entry in allowed}:
        return

    resolve = resolver if resolver is not None else resolve_host
    try:
        addresses = resolve(host)
    except OSError as exc:
        if unresolved_ok:
            return
        raise DestinationRefused(
            f"Could not resolve {host!r}: {exc}", host=host, reason="unresolved"
        ) from exc
    if not addresses:
        if unresolved_ok:
            return
        raise DestinationRefused(
            f"{host!r} did not resolve to any address", host=host, reason="no_address"
        )

    refuses = is_never_a_destination if narrow else is_forbidden
    for raw in addresses:
        address = ipaddress.ip_address(raw)
        if refuses(address):
            raise DestinationRefused(
                f"{host!r} resolves to {address}, inside a private or internal network",
                host=host,
                reason="internal",
                address=str(address),
            )


def api_path(path: str) -> str:
    """
    Check the path of a request to a platform's API host before it is sent.

    Args:
        path: What follows the host: starts with one ``/`` and may end in a
            query string.

    Returns:
        The path, unchanged.

    Raises:
        UnsafePath: It does not start with a single ``/`` (a second one makes
            the rest a host), holds whitespace, a control character, a
            backslash or ``#`` (or is not printable ASCII), or has a segment that is ``.`` or ``..``
            (written out or percent-encoded), which would reach another
            endpoint of the same host.
    """
    if not path.startswith("/") or path.startswith("//"):
        raise UnsafePath(f"Not a path on the platform's own host: {path!r}")
    if _NOT_IN_A_PATH.search(path):
        raise UnsafePath(f"A path holds a character no path may hold: {path!r}")
    for segment in path.split("?", 1)[0].split("/"):
        if unquote(segment) in (".", ".."):
            raise UnsafePath(f"A path may not move up or stay in place: {path!r}")
    return path


def path_segment(value: str | int) -> str:
    """
    Turn a name into one segment of a request path.

    Args:
        value: A repository, a group, a project or an id somebody named.

    Returns:
        The value percent-encoded, ``/`` included, so that it cannot become
        two segments.

    Raises:
        UnsafePath: It is empty, ``.``, ``..`` or holds a control character.
    """
    text = str(value)
    if text in ("", ".", "..") or _CONTROL.search(text):
        raise UnsafePath(f"Not a name that can be one part of a path: {text!r}")
    return quote(text, safe="")
