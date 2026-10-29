# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a person may write in a zone of Noust DNS.

The zone editor turns text a person typed into a zone file the name server reads, and a zone file
is a language: a newline starts another record, ``$INCLUDE`` reads a file from the server, ``$ORIGIN``
moves where every name below it points. So nothing here is passed through. Each record is read
into the fields its type has (an address, a name, a number, a string), each field is judged on its
own against an allowlist, and the zone file is written from the fields (see
:mod:`noust.managers.authdns.zonefile`), never from the text. What :func:`check_record` returns is
that canonical content: the same record always has the same text, which is also what makes
"already there" a comparison and not a guess.

The names follow RFC 1035's hostname alphabet (letters, digits, hyphen) for a zone and for the
targets of records, and add the underscore to the names of records because ``_dmarc``, ``_acme-challenge``
and every SRV name begin with one. Names are lower case, without the final dot, at most 253
characters, each label at most 63.
"""

from __future__ import annotations

import ipaddress
import re

from noust.core.exceptions import ValidationError

__all__ = [
    "MAX_NAMESERVERS",
    "MAX_TTL",
    "MIN_TTL",
    "RECORD_TYPES",
    "check_contact",
    "check_hostname",
    "check_nameservers",
    "check_record",
    "check_record_name",
    "check_record_type",
    "check_ttl",
    "check_zone_name",
]

#: The record types the editor offers. SOA is Noust's, and the NS records at a zone's apex are the
#: zone's name servers (:func:`check_nameservers`); a DNSSEC record is the engine's.
RECORD_TYPES: tuple[str, ...] = ("A", "AAAA", "CNAME", "MX", "NS", "TXT", "SRV", "CAA", "PTR", "DS")

#: The smallest and the largest TTL a record may carry, in seconds.
MIN_TTL = 30
MAX_TTL = 604800

#: The most name servers a zone names.
MAX_NAMESERVERS = 8

#: The longest a TXT value is, in bytes.
MAX_TXT_BYTES = 2048

#: The longest a name is, with its dots.
MAX_NAME = 253

_ZONE_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
_HOST_LABEL = _ZONE_LABEL
_RECORD_LABEL = re.compile(r"[a-z0-9_](?:[a-z0-9_-]{0,61}[a-z0-9_])?")
_UNSIGNED = re.compile(r"[0-9]{1,10}")
_HEX = re.compile(r"[0-9a-fA-F]{2,128}")
_CAA_TAG = re.compile(r"[a-z0-9]{1,15}")
_EMAIL_LOCAL = re.compile(r"[A-Za-z0-9._+-]{1,64}")

#: Control characters, line and paragraph separators and the characters that reorder text.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u2028\u2029\u202a-\u202e\u2066-\u2069\ufeff]")


def _text(value: object, field: str) -> str:
    """
    Args:
        value: What was given.
        field: The request field it came in, for the error.

    Returns:
        It, when it is text with no control character (a line break is how a record would end and
        another begin). Only spaces are trimmed at the ends.

    Raises:
        ValidationError: It is not text, or holds a control character.
    """
    if not isinstance(value, str):
        raise ValidationError(f"The {field} must be text", field=field)
    if _CONTROL.search(value):
        raise ValidationError(
            f"The {field} cannot hold a line break or another control character", field=field
        )
    return value.strip(" ")


def check_zone_name(value: object, *, field: str = "zone") -> str:
    """
    Judge the name of a zone.

    Args:
        value: What the person wrote, ``example.com`` (a final dot is allowed).
        field: The request field, for the error.

    Returns:
        The name in lower case, without the final dot.

    Raises:
        ValidationError: It is not a name Noust DNS serves.
    """
    name = _text(value, field).strip().lower().removesuffix(".")
    labels = name.split(".")
    if not name or len(name) > MAX_NAME - 40 or len(labels) < 2:
        raise ValidationError(
            f"{name or 'That'!r} is not a zone Noust DNS can serve",
            details="A zone is a domain such as example.com, of at least two labels, at most "
            f"{MAX_NAME - 40} characters. Write internationalised names in punycode (xn--...).",
            field=field,
        )
    if not all(_ZONE_LABEL.fullmatch(label) for label in labels):
        raise ValidationError(
            f"{name!r} is not a zone Noust DNS can serve",
            details="Each part is letters, digits and hyphens, not starting or ending with a "
            "hyphen, at most 63 characters.",
            field=field,
        )
    if labels[-1].isdigit() or labels[-1] == "invalid":
        raise ValidationError(
            f"{name!r} is not a zone Noust DNS can serve",
            details="The last part of a zone is not a number (an address), and .invalid is the "
            "name Noust DNS uses between its own servers.",
            field=field,
        )
    return name


def check_record_name(value: object, zone: str, *, field: str = "name") -> str:
    """
    Judge the name of a record, which is relative to its zone.

    Args:
        value: ``@`` for the zone itself, ``www``, ``*.apps`` or ``_dmarc``. A name with a final
            dot is complete (``www.example.com.``) and must be inside the zone.
        zone: The zone, already judged.
        field: The request field, for the error.

    Returns:
        The name relative to the zone, in lower case: ``@`` for the zone itself.

    Raises:
        ValidationError: It is not a name inside the zone.
    """
    name = _text(value, field).strip().lower()
    if name.endswith("."):
        full = name.removesuffix(".")
        if full == zone:
            return "@"
        if not full.endswith(f".{zone}"):
            raise ValidationError(
                f"{name!r} is not inside {zone}",
                details="Write the name relative to the zone (www), or complete and inside it "
                f"(www.{zone}.).",
                field=field,
            )
        name = full[: -len(zone) - 1]
    if name == "@":
        return "@"
    labels = name.split(".")
    for number, label in enumerate(labels):
        if label == "*" and number == 0:
            continue
        if not _RECORD_LABEL.fullmatch(label):
            raise ValidationError(
                f"{name or 'That'!r} is not a name a record may have",
                details="Use letters, digits, hyphens and underscores in each part, at most 63 "
                "characters each; a * only as the first part.",
                field=field,
            )
    if len(name) + len(zone) + 1 > MAX_NAME:
        raise ValidationError(
            f"{name!r} is too long once the zone is added",
            details="At most 253 characters.",
            field=field,
        )
    return name


def check_hostname(value: object, *, field: str = "target") -> str:
    """
    Judge a host name that a record points at.

    Args:
        value: ``mail.example.net`` (a final dot is allowed), or ``.`` where the record type
            says "nothing" (a null MX).
        field: The request field, for the error.

    Returns:
        The name in lower case without the final dot, or ``.``.

    Raises:
        ValidationError: It is not a host name.
    """
    text = _text(value, field).strip().lower()
    if text == ".":
        return "."
    name = text.removesuffix(".")
    labels = name.split(".")
    if (
        not name
        or len(name) > MAX_NAME
        or len(labels) < 2
        or not all(_HOST_LABEL.fullmatch(label) for label in labels)
    ):
        raise ValidationError(
            f"{text or 'That'!r} is not a host name a record can point at",
            details="Write the whole name, such as mail.example.net: letters, digits and "
            "hyphens, at least two parts.",
            field=field,
        )
    return name


def check_nameservers(values: object, *, field: str = "nameservers") -> list[str]:
    """
    Judge the name servers a zone names.

    Args:
        values: A list of host names.
        field: The request field.

    Returns:
        The names, lower case, without duplicates, in the order given.

    Raises:
        ValidationError: There are none, too many, or one is not a host name.
    """
    if not isinstance(values, list | tuple) or not 1 <= len(values) <= MAX_NAMESERVERS:
        raise ValidationError(
            f"A zone names from 1 to {MAX_NAMESERVERS} name servers",
            details="Give the names of the servers, such as ns1.example.net.",
            field=field,
        )
    found: list[str] = []
    for value in values:
        name = check_hostname(value, field=field)
        if name == ".":
            raise ValidationError("A name server has a name", field=field)
        if name not in found:
            found.append(name)
    return found


def check_contact(value: object, *, field: str = "contact") -> str:
    """
    Judge the address people write to about a zone (the SOA's contact).

    Args:
        value: ``hostmaster@example.com``.
        field: The request field.

    Returns:
        The address, with the domain in lower case.

    Raises:
        ValidationError: It is not an address.
    """
    text = _text(value, field).strip()
    local, at, domain = text.rpartition("@")
    if not at or not _EMAIL_LOCAL.fullmatch(local) or local.startswith("."):
        raise ValidationError(
            f"{text or 'That'!r} is not an email address",
            details="Write it as hostmaster@example.com.",
            field=field,
        )
    return f"{local}@{check_hostname(domain, field=field)}"


def check_ttl(value: object, *, field: str = "ttl") -> int:
    """
    Judge a time to live.

    Args:
        value: Seconds.
        field: The request field.

    Returns:
        The seconds.

    Raises:
        ValidationError: It is not a whole number from 30 to 604800.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not MIN_TTL <= value <= MAX_TTL:
        raise ValidationError(
            f"A TTL is a whole number of seconds from {MIN_TTL} to {MAX_TTL}",
            details="3600 is an hour.",
            field=field,
        )
    return value


def check_record_type(value: object, *, field: str = "type") -> str:
    """
    Judge a record type.

    Args:
        value: ``A``, ``mx``...
        field: The request field.

    Returns:
        The type in upper case.

    Raises:
        ValidationError: Noust DNS does not edit that type.
    """
    kind = _text(value, field).strip().upper()
    if kind not in RECORD_TYPES:
        raise ValidationError(
            f"Noust DNS does not edit {kind or 'that'} records",
            details="It edits: " + ", ".join(RECORD_TYPES) + ".",
            field=field,
        )
    return kind


def _number(value: str, high: int, what: str, field: str) -> int:
    """
    Args:
        value: Digits.
        high: The largest value.
        what: What it is, for the error.
        field: The request field.

    Returns:
        The number.

    Raises:
        ValidationError: It is not a whole number from 0 to ``high``.
    """
    if not _UNSIGNED.fullmatch(value) or int(value) > high:
        raise ValidationError(
            f"The {what} is a whole number from 0 to {high}, not {value[:20]!r}", field=field
        )
    return int(value)


def _fields(content: str, count: int, shape: str, field: str) -> list[str]:
    """
    Args:
        content: A record's content.
        count: The fields it has.
        shape: What it looks like, for the error.
        field: The request field.

    Returns:
        The content split on spaces.

    Raises:
        ValidationError: It has another number of fields.
    """
    parts = content.split(" ")
    parts = [part for part in parts if part]
    if len(parts) != count:
        raise ValidationError(
            f"That is not the content of this record: {shape}",
            details=f"Write {shape}.",
            field=field,
        )
    return parts


def _plain_text(text: str, field: str, what: str) -> str:
    """
    Args:
        text: A free string of a record.
        field: The request field.
        what: What it is, for the error.

    Returns:
        The text, unchanged.

    Raises:
        ValidationError: It holds a control character or is too long.
    """
    if _CONTROL.search(text) or len(text.encode()) > MAX_TXT_BYTES or not text:
        raise ValidationError(
            f"The {what} cannot be used",
            details=f"It is 1 to {MAX_TXT_BYTES} bytes with no line breaks or control characters.",
            field=field,
        )
    return text


def check_record(record_type: str, content: object, *, field: str = "content") -> str:
    """
    Read the content of a record into its canonical form.

    Args:
        record_type: The type, already judged (:func:`check_record_type`).
        content: What the person wrote.
        field: The request field.

    Returns:
        The canonical content: addresses written one way, names in lower case without the final
        dot, numbers without leading zeros, fields separated by one space. A TXT is its text.

    Raises:
        ValidationError: It is not the content of a record of that type.
    """
    text = _text(content, field)
    if record_type == "A":
        try:
            return str(ipaddress.IPv4Address(text.strip()))
        except ValueError:
            raise ValidationError(
                f"{text[:40]!r} is not an IPv4 address",
                details="Write it like 203.0.113.7.",
                field=field,
            ) from None
    if record_type == "AAAA":
        try:
            return str(ipaddress.IPv6Address(text.strip()))
        except ValueError:
            raise ValidationError(
                f"{text[:40]!r} is not an IPv6 address",
                details="Write it like 2001:db8::7.",
                field=field,
            ) from None
    if record_type in ("CNAME", "NS", "PTR"):
        target = check_hostname(text, field=field)
        if target == ".":
            raise ValidationError(f"A {record_type} record points at a name", field=field)
        return target
    if record_type == "MX":
        preference, target = _fields(
            text, 2, "a preference and a host name, such as 10 mail.example.net", field
        )
        return f"{_number(preference, 65535, 'preference', field)} {check_hostname(target, field=field)}"
    if record_type == "SRV":
        priority, weight, port, target = _fields(
            text,
            4,
            "a priority, a weight, a port and a host name, such as 10 5 5060 sip.example.net",
            field,
        )
        return (
            f"{_number(priority, 65535, 'priority', field)} {_number(weight, 65535, 'weight', field)} "
            f"{_number(port, 65535, 'port', field)} {check_hostname(target, field=field)}"
        )
    if record_type == "CAA":
        flags, tag, rest = ([part for part in text.split(" ", 2) if part != ""] + ["", "", ""])[:3]
        value = rest.strip()
        if len(value) >= 2 and value[0] == value[-1] == '"':
            value = value[1:-1]
        tag = tag.lower()
        if not _CAA_TAG.fullmatch(tag) or not value:
            raise ValidationError(
                "That is not the content of a CAA record",
                details='Write a flag, a tag and a value, such as: 0 issue "letsencrypt.org".',
                field=field,
            )
        _plain_text(value, field, "value")
        if '"' in value or "\\" in value:
            raise ValidationError("A CAA value has no quotes or backslashes", field=field)
        return f'{_number(flags, 255, "flag", field)} {tag} "{value}"'
    if record_type == "DS":
        key_tag, algorithm, digest_type, digest = _fields(
            text, 4, "a key tag, an algorithm, a digest type and the digest in hexadecimal", field
        )
        if not _HEX.fullmatch(digest) or len(digest) % 2:
            raise ValidationError("The digest of a DS record is hexadecimal", field=field)
        return (
            f"{_number(key_tag, 65535, 'key tag', field)} {_number(algorithm, 255, 'algorithm', field)} "
            f"{_number(digest_type, 255, 'digest type', field)} {digest.upper()}"
        )
    # TXT: the text itself. One pair of quotes round all of it is how a provider's documentation
    # writes it, and is not part of the value.
    value = text
    if len(value) >= 2 and value[0] == value[-1] == '"' and '"' not in value[1:-1]:
        value = value[1:-1]
    return _plain_text(value, field, "text")
