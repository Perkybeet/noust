# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The zone file of a zone of Noust DNS, written from the zone's records.

The file is the only thing the name server reads of a zone, and a zone file is a language: a
newline ends a record, ``$INCLUDE`` reads a file from the machine, ``$ORIGIN`` moves where every
name below it points. So this module does not pass text through. Each record arrives already read
into the canonical content of its type (:func:`noust.validators.dns_zone.check_record`), a name
is written with its trailing dot where the format wants one, and a free string is always written
quoted with its quotes, backslashes and bytes outside printable ASCII escaped, so nothing a
string holds can end it or add a line. The same zone is always the same file, whatever order its
records were made in, so a file that has not changed is not rewritten and the name server is not
asked to read it again.

The serial in the SOA is only the first one: the name server owns it from then on (it signs the
zone, increments the serial when it changes it, and keeps it across restarts in its journal).
"""

from __future__ import annotations

from collections.abc import Iterable

from noust.core import paths
from noust.core.authdns_store import AuthDnsRecord, AuthDnsZone

__all__ = ["ZONE_FILE_MARKER", "render_txt", "render_zone", "soa_contact"]

#: The first line of every zone file Noust writes.
ZONE_FILE_MARKER = f"{paths.UNIT_MARKER} (noust dns zone): changes made here are overwritten"

#: The timers of the SOA, in seconds: how often a secondary asks when it was not notified, how
#: long before it asks again after a failure, how long it serves a zone it cannot refresh, and
#: how long a name that does not exist is remembered.
_SOA_TIMERS = (7200, 3600, 1209600, 3600)

#: The longest a string of a TXT record is, in bytes; longer text is several strings.
_TXT_CHUNK = 255


def soa_contact(contact: str) -> str:
    """
    Write the address of a zone's contact the way an SOA names it.

    Args:
        contact: ``first.last@example.org``.

    Returns:
        ``first\\.last.example.org.``: the dot of the local part is escaped, the ``@`` is a dot.
    """
    local, _, domain = contact.rpartition("@")
    return f"{local.replace('.', chr(92) + '.')}.{domain.lower()}."


def render_txt(text: str) -> str:
    """
    Write a text as the strings of a TXT record.

    Args:
        text: The value.

    Returns:
        One or more quoted strings of at most 255 bytes each, separated by a space. A quote, a
        backslash and every byte outside printable ASCII are escaped.
    """
    raw = text.encode()
    chunks = [raw[start : start + _TXT_CHUNK] for start in range(0, len(raw), _TXT_CHUNK)] or [b""]
    strings = []
    for chunk in chunks:
        out = []
        for byte in chunk:
            if byte in (0x22, 0x5C):
                out.append("\\" + chr(byte))
            elif 0x20 <= byte < 0x7F:
                out.append(chr(byte))
            else:
                out.append(f"\\{byte:03d}")
        strings.append('"' + "".join(out) + '"')
    return " ".join(strings)


def _rdata(kind: str, content: str) -> str:
    """
    Args:
        kind: A record type.
        content: Its canonical content.

    Returns:
        What follows the type on the line.
    """
    if kind in ("CNAME", "NS", "PTR"):
        return f"{content}."
    if kind == "TXT":
        return render_txt(content)
    if kind in ("MX", "SRV"):
        *numbers, target = content.split(" ")
        return " ".join([*numbers, target if target == "." else f"{target}."])
    return content


def _order(record: AuthDnsRecord) -> tuple[bool, str, str, str]:
    return (record.name != "@", record.name, record.type, record.content)


def render_zone(zone: AuthDnsZone, records: Iterable[AuthDnsRecord], *, serial: int) -> str:
    """
    Write a zone.

    Args:
        zone: The zone.
        records: Its records, in any order.
        serial: The SOA's serial. The name server takes the count over after it first loads the
            file, so a time makes a zone that is made again (after being removed) start above
            the serial its secondaries last had.

    Returns:
        The zone file: ``$ORIGIN`` and ``$TTL``, the SOA, the zone's name servers, then the
        records, the apex first.
    """
    refresh, retry, expire, minimum = _SOA_TIMERS
    out = [
        f"; {ZONE_FILE_MARKER}",
        f"$ORIGIN {zone.name}.",
        f"$TTL {zone.default_ttl}",
        f"@ IN SOA {zone.nameservers[0]}. {soa_contact(zone.contact)} "
        f"{serial} {refresh} {retry} {expire} {minimum}",
    ]
    out.extend(f"@ IN NS {server}." for server in zone.nameservers)
    for record in sorted(records, key=_order):
        ttl = f"{record.ttl} " if record.ttl is not None else ""
        out.append(f"{record.name} {ttl}IN {record.type} {_rdata(record.type, record.content)}")
    return "\n".join(out) + "\n"
