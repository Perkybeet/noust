# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Ask a name server one question and read the answer, for the delegation check.

``managers.dns.resolver`` resolves addresses through a public resolver and nothing else; the check
of a delegation needs more: the name servers and DS records resolvers see for a domain, and
whether a given server answers for a zone as its authority (the ``aa`` flag) and with which
serial. It is the same sixty lines of DNS, extended: one UDP question, a timeout on the socket,
every malformed byte refused and a bound on how much of a name is followed (a compression pointer
that loops is how a hostile server hangs a reader).

It only asks. Nothing it reads is ever written anywhere but into the report of the check.
"""

from __future__ import annotations

import ipaddress
import secrets
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["Answer", "DnsError", "Record", "ask"]

#: Seconds a server has to answer.
TIMEOUT = 3.0

#: The largest answer read, and the EDNS size announced (the size that avoids fragmentation).
MAX_ANSWER = 4096
EDNS_SIZE = 1232

#: Compression pointers followed at most in one name.
MAX_POINTERS = 20

TYPES = {
    "A": 1,
    "NS": 2,
    "CNAME": 5,
    "SOA": 6,
    "MX": 15,
    "TXT": 16,
    "AAAA": 28,
    "DS": 43,
    "RRSIG": 46,
    "DNSKEY": 48,
}
_NAMES = {number: name for name, number in TYPES.items()}

#: Sends one question and returns the answer's bytes: ``(server, port, packet, timeout)``.
Transport = Callable[[str, int, bytes, float], bytes]


class DnsError(Exception):
    """A server did not answer, or its answer was not an answer to the question."""


@dataclass(frozen=True)
class Record:
    """
    One record of an answer.

    Attributes:
        name: Its owner, lower case, without the final dot.
        type: ``A``, ``NS``, ``SOA``, ``DS``... or ``TYPE<n>`` for one not read.
        ttl: Seconds.
        data: What it holds in the form Noust DNS keeps it (a name without its dot, ``tag
            algorithm digest-type digest`` for a DS, ``serial`` after the names for an SOA).
    """

    name: str
    type: str
    ttl: int
    data: str


@dataclass(frozen=True)
class Answer:
    """
    What a server answered.

    Attributes:
        rcode: 0 for no error, 2 for a failure, 3 for a name that does not exist, 5 for refused.
        authoritative: The server says it is the authority for the name (the ``aa`` flag).
        recursion_available: The server says it recurses (the ``ra`` flag).
        truncated: The answer did not fit and a client would ask again over TCP.
        answers: The answer section.
        authority: The authority section.
    """

    rcode: int
    authoritative: bool
    recursion_available: bool
    truncated: bool
    answers: tuple[Record, ...]
    authority: tuple[Record, ...]

    def of_type(self, record_type: str) -> list[Record]:
        """
        Args:
            record_type: A type.

        Returns:
            The records of the answer section of that type.
        """
        return [record for record in self.answers if record.type == record_type]


def _udp(server: str, port: int, packet: bytes, timeout: float) -> bytes:
    """
    Args:
        server: The server's address.
        port: Its port.
        packet: The question.
        timeout: Seconds to wait.

    Returns:
        The answer.

    Raises:
        OSError: The server did not answer in time.
    """
    family = socket.AF_INET6 if ":" in server else socket.AF_INET
    with socket.socket(family, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, (server, port))
        return sock.recv(MAX_ANSWER)


def encode_name(name: str) -> bytes:
    """
    Args:
        name: A host name.

    Returns:
        It in the wire form.

    Raises:
        ValueError: A label is empty or longer than 63 bytes.
    """
    labels = name.rstrip(".").encode("idna").split(b".") if name.rstrip(".") else []
    if any(not 0 < len(label) <= 63 for label in labels):
        raise ValueError(f"not a host name: {name!r}")
    return b"".join(bytes([len(label)]) + label for label in labels) + b"\x00"


def _question(name: str, qtype: int, ident: int, *, recursion: bool, dnssec: bool) -> bytes:
    flags = 0x0100 if recursion else 0
    header = struct.pack(">HHHHHH", ident, flags, 1, 0, 0, 1 if dnssec else 0)
    body = encode_name(name) + struct.pack(">HH", qtype, 1)
    opt = b""
    if dnssec:
        opt = b"\x00" + struct.pack(">HHIH", 41, EDNS_SIZE, 0x00008000, 0)
    return header + body + opt


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """
    Args:
        data: The answer.
        offset: Where the name starts.

    Returns:
        The name (lower case, no final dot) and the offset after it in the record.

    Raises:
        ValueError: The name runs past the answer, points forward, or loops.
    """
    labels: list[str] = []
    end: int | None = None
    pointers = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated name")
        length = data[offset]
        if length == 0:
            offset += 1
            break
        if length & 0xC0 == 0xC0:
            if offset + 1 >= len(data):
                raise ValueError("truncated pointer")
            pointer = ((length & 0x3F) << 8) | data[offset + 1]
            if end is None:
                end = offset + 2
            pointers += 1
            if pointers > MAX_POINTERS or pointer >= offset:
                raise ValueError("a name that loops")
            offset = pointer
            continue
        if length & 0xC0:
            raise ValueError("a label of a kind not in use")
        if offset + 1 + length > len(data):
            raise ValueError("truncated label")
        labels.append(data[offset + 1 : offset + 1 + length].decode("ascii", "replace").lower())
        offset += 1 + length
    return ".".join(labels), (end if end is not None else offset)


def _rdata(data: bytes, offset: int, length: int, rtype: int) -> str:
    rdata = data[offset : offset + length]
    if rtype == 1 and length == 4:
        return str(ipaddress.IPv4Address(rdata))
    if rtype == 28 and length == 16:
        return str(ipaddress.IPv6Address(rdata))
    if rtype in (2, 5):
        return _read_name(data, offset)[0]
    if rtype == 15 and length > 2:
        return f"{struct.unpack('>H', rdata[:2])[0]} {_read_name(data, offset + 2)[0]}"
    if rtype == 6:
        mname, after = _read_name(data, offset)
        rname, after = _read_name(data, after)
        serial = struct.unpack(">I", data[after : after + 4])[0]
        return f"{mname} {rname} {serial}"
    if rtype == 43 and length > 4:
        tag, algorithm, digest_type = struct.unpack(">HBB", rdata[:4])
        return f"{tag} {algorithm} {digest_type} {rdata[4:].hex()}"
    if rtype == 16:
        parts, position = [], 0
        while position < len(rdata):
            size = rdata[position]
            parts.append(rdata[position + 1 : position + 1 + size].decode("utf-8", "replace"))
            position += 1 + size
        return "".join(parts)
    return ""


def _records(data: bytes, offset: int, count: int) -> tuple[list[Record], int]:
    found: list[Record] = []
    for _ in range(count):
        name, offset = _read_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError("truncated record")
        rtype, _cls, ttl, length = struct.unpack(">HHIH", data[offset : offset + 10])
        offset += 10
        if offset + length > len(data):
            raise ValueError("truncated record data")
        found.append(
            Record(
                name, _NAMES.get(rtype, f"TYPE{rtype}"), ttl, _rdata(data, offset, length, rtype)
            )
        )
        offset += length
    return found, offset


def ask(
    server: str,
    name: str,
    record_type: str,
    *,
    port: int = 53,
    recursion: bool = False,
    dnssec: bool = False,
    transport: Transport = _udp,
    timeout: float = TIMEOUT,
) -> Answer:
    """
    Ask a server one question.

    Args:
        server: The server's IP address.
        name: The name.
        record_type: ``A``, ``NS``, ``SOA``, ``DS``...
        port: The server's port.
        recursion: Set the recursion-desired flag (a public resolver is asked with it).
        dnssec: Ask for the DNSSEC records too.
        transport: Sends the question (tests replace it).
        timeout: Seconds the server has.

    Returns:
        The answer.

    Raises:
        DnsError: The server did not answer, or what came back was not an answer to this question.
    """
    ident = secrets.randbits(16)
    try:
        packet = _question(name, TYPES[record_type], ident, recursion=recursion, dnssec=dnssec)
    except (ValueError, KeyError, UnicodeError) as exc:
        raise DnsError(f"cannot ask for {record_type} of {name!r}: {exc}") from None
    try:
        data = transport(server, port, packet, timeout)
    except OSError as exc:
        raise DnsError(f"{server} did not answer ({exc})") from None
    try:
        got, flags, questions, answers, authority, _extra = struct.unpack(">HHHHHH", data[:12])
        if got != ident or not flags & 0x8000:
            raise ValueError("not an answer to this question")
        offset = 12
        for _ in range(questions):
            offset = _read_name(data, offset)[1] + 4
        found, offset = _records(data, offset, answers)
        more, _offset = _records(data, offset, authority)
    except (ValueError, struct.error, IndexError) as exc:
        raise DnsError(f"{server} sent an answer that cannot be read ({exc})") from None
    return Answer(
        rcode=flags & 0x000F,
        authoritative=bool(flags & 0x0400),
        recursion_available=bool(flags & 0x0080),
        truncated=bool(flags & 0x0200),
        answers=tuple(found),
        authority=tuple(more),
    )
