# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ask a name server directly what a name resolves to.

The check that comes before ordering a certificate - does this name point here? - used
to ask the machine's own resolver, which on a server often is a cache that still holds
the answer from before the record was changed, or a private resolver that has never
heard of the public zone. The authority's validators ask the public DNS, so the check
asks it too: a resolver Noust is told (``dns.resolvers``, Cloudflare's and Google's by
default), falling back to the system's own when none answers.

A DNS client is some sixty lines and a dependency is a package on four distributions, so
this is the sixty lines: one UDP question per record type, the answer's A and AAAA
records read out of it, a timeout on every socket, and every malformed byte refused
rather than interpreted. It resolves A and AAAA only, which is all the check needs.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct
from collections.abc import Callable, Sequence

#: The resolvers asked when none is configured.
DEFAULT_RESOLVERS: tuple[str, ...] = ("1.1.1.1", "8.8.8.8")

#: Seconds a resolver has to answer one question.
TIMEOUT = 3.0

#: The most bytes of an answer read.
_MAX_ANSWER = 4096

_A, _AAAA = 1, 28

#: Sends one question and returns the answer's bytes: ``(server, packet, timeout)``.
Transport = Callable[[str, bytes, float], bytes]


def _udp(server: str, packet: bytes, timeout: float) -> bytes:
    """
    Send one datagram and read the answer.

    Args:
        server: The resolver's address.
        packet: The question.
        timeout: Seconds to wait.

    Returns:
        The answer.

    Raises:
        OSError: The resolver did not answer in time.
    """
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, 53))
        return sock.recv(_MAX_ANSWER)


def _question(name: str, qtype: int, ident: int) -> bytes:
    """
    Build the question for a name.

    Args:
        name: A host name.
        qtype: ``1`` for A, ``28`` for AAAA.
        ident: The question's id, which the answer must echo.

    Returns:
        The datagram.

    Raises:
        ValueError: The name has a label that is empty or longer than 63 bytes.
    """
    labels = name.rstrip(".").encode("idna").split(b".")
    if any(not 0 < len(label) <= 63 for label in labels):
        raise ValueError(f"not a host name: {name!r}")
    header = struct.pack(">HHHHHH", ident, 0x0100, 1, 0, 0, 0)
    body = b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"
    return header + body + struct.pack(">HH", qtype, 1)


def _skip_name(data: bytes, offset: int) -> int:
    """
    Step over a (possibly compressed) name.

    Args:
        data: The answer.
        offset: Where the name starts.

    Returns:
        The offset after it.

    Raises:
        ValueError: The name runs past the answer.
    """
    while True:
        if offset >= len(data):
            raise ValueError("truncated name")
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length


def parse_addresses(data: bytes, ident: int, qtype: int) -> list[str]:
    """
    Read the addresses out of an answer.

    Args:
        data: The answer.
        ident: The id of the question it must answer.
        qtype: The type that was asked.

    Returns:
        The addresses of the answer's A (or AAAA) records. Empty for an answer that
        is not for this question, is an error, or carries none.

    Raises:
        ValueError: The answer is malformed.
    """
    if len(data) < 12:
        raise ValueError("short answer")
    got, flags, questions, answers = struct.unpack(">HHHH", data[:8])
    if got != ident or not flags & 0x8000 or flags & 0x000F:
        return []
    offset = 12
    for _ in range(questions):
        offset = _skip_name(data, offset) + 4
    found: list[str] = []
    for _ in range(answers):
        offset = _skip_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("truncated record")
        rtype, _cls, _ttl, length = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        rdata = data[offset : offset + length]
        offset += length
        if rtype == qtype == _A and length == 4:
            found.append(str(ipaddress.IPv4Address(rdata)))
        elif rtype == qtype == _AAAA and length == 16:
            found.append(str(ipaddress.IPv6Address(rdata)))
    return found


def resolve(
    name: str,
    resolvers: Sequence[str] = DEFAULT_RESOLVERS,
    *,
    transport: Transport = _udp,
    timeout: float = TIMEOUT,
) -> tuple[list[str], str | None]:
    """
    Resolve a name's A and AAAA records through the first resolver that answers.

    Args:
        name: A host name.
        resolvers: Resolver addresses, tried in order.
        transport: Sends a question and returns the answer (tests replace it).
        timeout: Seconds each resolver has per question.

    Returns:
        ``(addresses, resolver)``: the addresses, and the resolver that answered. A
        name that does not exist is an answer, empty. ``([], None)`` when none answered.
    """
    for server in resolvers:
        try:
            addresses: list[str] = []
            for qtype in (_A, _AAAA):
                ident = secrets.randbits(16)
                answer = transport(server, _question(name, qtype, ident), timeout)
                if int.from_bytes(answer[:2], "big") != ident or len(answer) < 12:
                    # Not a reply to this question: a stray datagram, or an attacker
                    # guessing. It is not an answer, so the next resolver is asked.
                    raise ValueError("not an answer to this question")
                addresses.extend(parse_addresses(answer, ident, qtype))
        except (OSError, ValueError, struct.error):
            continue
        return sorted(set(addresses)), server
    return [], None
