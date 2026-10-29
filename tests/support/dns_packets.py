# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
DNS on the wire, for the tests of the delegation check: a builder of answers and a network of
name servers that answer from a table, in the packet format :mod:`noust.managers.authdns.dnsquery`
reads, so the check is exercised from the question it sends to the sentence it says.
"""

from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass, field

from noust.managers.authdns.dnsquery import TYPES, _read_name, encode_name

__all__ = ["FakeNetwork", "Reply", "a_record", "ds_record", "ns_record", "soa_record"]

#: A record to put in an answer: its type, then its data in the wire form.
Rr = tuple[str, bytes]


def a_record(address: str) -> Rr:
    return ("A" if ":" not in address else "AAAA", ipaddress.ip_address(address).packed)


def ns_record(name: str) -> Rr:
    return ("NS", encode_name(name))


def soa_record(
    serial: int, mname: str = "ns1.example.net", rname: str = "hostmaster.example.com"
) -> Rr:
    return (
        "SOA",
        encode_name(mname)
        + encode_name(rname)
        + struct.pack(">IIIII", serial, 7200, 3600, 1209600, 3600),
    )


def ds_record(tag: int, algorithm: int, digest_type: int, digest: str) -> Rr:
    return ("DS", struct.pack(">HBB", tag, algorithm, digest_type) + bytes.fromhex(digest))


@dataclass
class Reply:
    """
    What a name server answers to one question.

    Attributes:
        answers: The answer section.
        authoritative: The ``aa`` flag.
        recursion_available: The ``ra`` flag.
        rcode: The response code.
        authority: The authority section.
    """

    answers: list[Rr] = field(default_factory=list)
    authoritative: bool = False
    recursion_available: bool = False
    rcode: int = 0
    authority: list[Rr] = field(default_factory=list)


def build(question: bytes, reply: Reply) -> bytes:
    """
    Args:
        question: The packet that was sent.
        reply: What to answer.

    Returns:
        The answer in the wire form, the owner of each record a pointer to the question's name.
    """
    ident = question[:2]
    flags = (
        0x8000
        | (0x0400 if reply.authoritative else 0)
        | (0x0080 if reply.recursion_available else 0)
    )
    flags |= reply.rcode
    end = 12
    while question[end] != 0:
        end += 1 + question[end]
    question_section = question[12 : end + 1 + 4]

    def section(records: list[Rr]) -> bytes:
        out = b""
        for kind, data in records:
            out += b"\xc0\x0c" + struct.pack(">HHIH", TYPES[kind], 1, 300, len(data)) + data
        return out

    header = struct.pack(">HHHHHH", 0, flags, 1, len(reply.answers), len(reply.authority), 0)
    return ident + header[2:] + question_section + section(reply.answers) + section(reply.authority)


@dataclass
class FakeNetwork:
    """
    Name servers that answer from a table.

    Attributes:
        replies: ``(server address, name, type)`` to what it answers; an exception to raise
            instead (a server that is silent is an ``OSError``); a missing key is silence too.
        asked: Every question, in order, as ``(server, name, type, recursion)``.
    """

    replies: dict[tuple[str, str, str], Reply | Exception] = field(default_factory=dict)
    asked: list[tuple[str, str, str, bool]] = field(default_factory=list)

    def __call__(self, server: str, port: int, packet: bytes, timeout: float) -> bytes:
        name, _ = _read_name(packet, 12)
        offset = 12 + len(encode_name(name))
        qtype = struct.unpack(">H", packet[offset : offset + 2])[0]
        kind = next(k for k, v in TYPES.items() if v == qtype)
        flags = struct.unpack(">H", packet[2:4])[0]
        self.asked.append((server, name, kind, bool(flags & 0x0100)))
        reply = self.replies.get((server, name, kind))
        if reply is None:
            raise OSError("timed out")
        if isinstance(reply, Exception):
            raise reply
        return build(packet, reply)
