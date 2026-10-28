# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a request says about who sent it and from where: the client address, the origin and the credential it carries.
"""

from __future__ import annotations

import ipaddress
import logging

from starlette.requests import HTTPConnection

from noust.web.auth.config import SecurityConfig, get_security_config
from noust.web.auth.constants import WS_SUBPROTOCOLS, WS_TOKEN_PREFIXES

logger = logging.getLogger("noust.web.auth")


def _parse_networks(entries: list[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """
    Parse IP or CIDR strings, ignoring malformed entries.

    Args:
        entries: Addresses or networks from the configuration.

    Returns:
        The parsed networks.
    """
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in entries:
        try:
            networks.append(ipaddress.ip_network(entry.strip(), strict=False))
        except ValueError:
            logger.warning("Ignoring invalid IP or CIDR in configuration: %r", entry)
    return networks


def ip_matches(candidate: str, entries: list[str]) -> bool:
    """
    Check an address against a list of addresses or CIDRs.

    Args:
        candidate: The address to test.
        entries: Allowed addresses or networks.

    Returns:
        True when the address falls inside one of the entries.
    """
    if not entries:
        return False
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return any(address in network for network in _parse_networks(entries))


def _as_ip_address(value: str) -> str | None:
    """
    Return the value when it is a bare IP address.

    Args:
        value: A candidate address, possibly with surrounding whitespace or
            brackets around an IPv6 literal.

    Returns:
        The normalised address, or None when it is not an IP address at all.
    """
    candidate = value.strip()
    if candidate.startswith("[") and candidate.endswith("]"):
        candidate = candidate[1:-1]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def get_client_ip(connection: HTTPConnection, config: SecurityConfig | None = None) -> str:
    """
    Determine the client address that security decisions are keyed on.

    The policy, in one sentence: **the peer address is the truth unless the peer
    is a proxy we deployed, and even then only a parseable IP address is
    believed.** Two rules follow from it.

    - Forwarding headers are read only when the direct peer matches
      ``trusted_proxies``, which is empty by default. Otherwise any client could
      pick its own identity and rotate out of a lockout or a rate limit.
    - A hop that is not a valid IP address is discarded rather than used. A
      trusted proxy can be tricked into appending client-supplied garbage, and a
      free-form string as the limiter's key is the same unbounded-rotation bug
      one layer down.

    Args:
        connection: The incoming HTTP request or WebSocket handshake.
        config: Configuration to use; the installed one by default.

    Returns:
        The client's IP address, the peer address when no header applies, or
        ``"unknown"`` when there is no peer at all.
    """
    config = config or get_security_config()
    peer = connection.client.host if connection.client else ""

    if peer and config.trusted_proxies and ip_matches(peer, config.trusted_proxies):
        forwarded_for = connection.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Walk right to left: the rightmost entry that is not one of our own
            # proxies is the first address our infrastructure actually observed.
            for raw_hop in reversed(forwarded_for.split(",")):
                hop = _as_ip_address(raw_hop)
                if hop is None:
                    # Anything unparseable ends the walk: entries to its left
                    # were appended before it and are just as untrustworthy.
                    break
                if not ip_matches(hop, config.trusted_proxies):
                    return hop
        real_ip = connection.headers.get("X-Real-IP")
        if real_ip:
            parsed = _as_ip_address(real_ip)
            if parsed is not None:
                return parsed

    return peer or "unknown"


def is_secure_request(connection: HTTPConnection, config: SecurityConfig | None = None) -> bool:
    """
    Report whether the request reached the panel over TLS.

    Args:
        connection: The incoming request or WebSocket handshake.
        config: Configuration to use; the installed one by default.

    Returns:
        True when the connection is TLS, directly or through a trusted proxy
        that declared it with ``X-Forwarded-Proto``.
    """
    config = config or get_security_config()
    if connection.url.scheme in ("https", "wss"):
        return True

    peer = connection.client.host if connection.client else ""
    if peer and config.trusted_proxies and ip_matches(peer, config.trusted_proxies):
        return connection.headers.get("X-Forwarded-Proto", "").strip().lower() in ("https", "wss")

    return False


def _origin_host(origin: str) -> str | None:
    """
    Extract the host of an Origin header.

    Args:
        origin: The header value.

    Returns:
        The lowercase host with its port, or None when the value is opaque
        (``null``) or not an absolute origin.
    """
    value = origin.strip().lower()
    if not value or value == "null":
        return None
    _scheme, separator, remainder = value.partition("://")
    if not separator or not remainder:
        return None
    return remainder.split("/", 1)[0] or None


def is_allowed_origin(connection: HTTPConnection, config: SecurityConfig | None = None) -> bool:
    """
    Check the Origin of a handshake against the hosts allowed to open one.

    A ``SameSite=Strict`` cookie is still sent by a sibling subdomain, because
    same-site is not same-origin. Without this check an XSS anywhere under the
    parent domain could open ``/ws/logs/{domain}`` and read the root journal, a
    cross-site WebSocket hijack.

    Args:
        connection: The incoming handshake.
        config: Configuration to use; the installed one by default.

    Returns:
        True when the request carries no Origin (a non-browser client, which
        cannot be tricked into ambient authority), when it matches the Host the
        request was addressed to, or when it is explicitly configured.
    """
    config = config or get_security_config()
    origin = connection.headers.get("origin")
    if not origin:
        return True

    origin_host = _origin_host(origin)
    if origin_host is None:
        return False

    host_header = connection.headers.get("host", "").strip().lower()
    if host_header and origin_host == host_header:
        return True

    allowed = {value.strip().lower() for value in config.cors_origins}
    if origin.strip().lower() in allowed:
        return True
    return any(_origin_host(value) == origin_host for value in allowed)


def bearer_token(connection: HTTPConnection) -> str | None:
    """
    Extract a Bearer token from the Authorization header.

    Args:
        connection: The incoming request or handshake.

    Returns:
        The token, or None when the header is absent or not a Bearer header.
    """
    header = connection.headers.get("Authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer" or not credentials.strip():
        return None
    return credentials.strip()


def subprotocol_token(connection: HTTPConnection) -> str | None:
    """
    Extract a session token from the requested WebSocket subprotocols.

    Args:
        connection: The pending handshake.

    Returns:
        The token, or None when no subprotocol carries one.
    """
    header = connection.headers.get("sec-websocket-protocol", "")
    for entry in (part.strip() for part in header.split(",")):
        for prefix in WS_TOKEN_PREFIXES:
            if entry.startswith(prefix):
                return entry[len(prefix) :] or None
    return None


def accepted_subprotocol(connection: HTTPConnection) -> str | None:
    """
    Choose the subprotocol a WebSocket handshake is answered with.

    A browser fails a handshake whose answer names a subprotocol it did not offer, and one
    that offered some and gets none, so the answer is the first of ours that the client
    itself offered: ``noust.auth``, or the old spelling for a client written before 4.0.

    Args:
        connection: The pending handshake.

    Returns:
        The subprotocol to accept with, or None when the client offered neither.
    """
    offered = {
        part.strip() for part in connection.headers.get("sec-websocket-protocol", "").split(",")
    }
    return next((name for name in WS_SUBPROTOCOLS if name in offered), None)


def shared_address(client_ip: str, config: SecurityConfig | None = None) -> bool:
    """
    Report whether an address stands for many people rather than one.

    Loopback is where every operator reaching the console over ``ssh -L``
    arrives from, and a trusted proxy's own address is what a request that
    passed it without a usable forwarding header resolves to
    (:func:`get_client_ip`). Neither says who is on the other end.

    Args:
        client_ip: The resolved client address.
        config: Configuration to use; the installed one by default.

    Returns:
        True for a loopback address or one of ``trusted_proxies``.
    """
    try:
        address = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    if address.is_loopback:
        return True
    config = config or get_security_config()
    return ip_matches(str(address), config.trusted_proxies)
