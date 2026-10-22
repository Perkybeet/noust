# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A small LDAP v3 client: bind and search, over TLS, with the standard library only.

An organisation under the ENS often keeps its people in a directory (Active Directory,
OpenLDAP, FreeIPA, 389). Signing them in against it needs four operations of RFC 4511: StartTLS,
a simple bind, a search and an unbind. That is a few hundred lines of BER, which is what this is,
rather than a dependency every target distribution would have to carry (CONTRIBUTING, Packaging
notes); where an operation is not here, it says so instead of guessing.

What it does on purpose:

- **TLS or nothing.** ``ldaps`` or StartTLS, with the certificate and the host name verified; plain
  text only to a loopback address, for a directory on the same machine and for tests. A password
  never crosses a network in clear text.
- **An empty password is never sent.** A simple bind with a name and no password is an
  *unauthenticated* bind (RFC 4513 section 5.1.2), which many directories answer with success. It
  is refused before it leaves.
- **The host is judged once and used as judged.** The name is resolved, every address is held to
  :mod:`noust.core.outbound`, and the connection goes to an address that was judged, so a name
  cannot be made to point somewhere else between the check and the connect.
- **A hostile server cannot make it allocate.** Every message length is checked against a ceiling
  before it is read, nesting is bounded, and a whole exchange has one deadline.
- **A filter is built from a template and escaped values.** What a person typed goes through
  :func:`escape_filter_value` (RFC 4515) or :func:`escape_dn_value` (RFC 4514); the template is the
  operator's.

Not here: SASL, referrals, paged results, controls, nested group matching rules (a group is read
from the attribute the directory keeps on the user, or searched directly).
"""

from __future__ import annotations

import re
import socket
import ssl
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from noust.core import outbound
from noust.core.exceptions import NoustError

#: The ceiling of one message. A search result with a thousand groups fits in a fraction of it.
MAX_MESSAGE = 1 << 20
#: How deep BER may nest. A search entry is four deep.
MAX_DEPTH = 8
DEFAULT_TIMEOUT = 10.0

TLS_LDAPS = "ldaps"
TLS_STARTTLS = "starttls"
TLS_NONE = "none"
TLS_MODES = (TLS_LDAPS, TLS_STARTTLS, TLS_NONE)

SCOPE_BASE = 0
SCOPE_ONE = 1
SCOPE_SUBTREE = 2

#: Result codes the callers tell apart (RFC 4511 appendix A).
RESULT_SUCCESS = 0
RESULT_SIZE_LIMIT = 4
RESULT_NO_SUCH_OBJECT = 32
RESULT_INVALID_CREDENTIALS = 49
RESULT_INSUFFICIENT_ACCESS = 50

OID_STARTTLS = "1.3.6.1.4.1.1466.20037"

# BER tags (X.690) and LDAP application tags (RFC 4511).
_SEQUENCE = 0x30
_SET = 0x31
_INTEGER = 0x02
_OCTET_STRING = 0x04
_ENUMERATED = 0x0A
_BOOLEAN = 0x01
_BIND_REQUEST = 0x60
_BIND_RESPONSE = 0x61
_UNBIND_REQUEST = 0x42
_SEARCH_REQUEST = 0x63
_SEARCH_ENTRY = 0x64
_SEARCH_DONE = 0x65
_SEARCH_REFERENCE = 0x73
_EXTENDED_REQUEST = 0x77
_EXTENDED_RESPONSE = 0x78
_SIMPLE_AUTH = 0x80

_ATTRIBUTE = re.compile(r"[A-Za-z][A-Za-z0-9.;-]{0,63}|[0-9]+(?:\.[0-9]+)*")


#: What the result codes a directory answers with mean, for the sentence an operator reads.
RESULT_NAMES: dict[int, str] = {
    1: "operations error",
    2: "protocol error",
    3: "time limit exceeded",
    4: "size limit exceeded",
    7: "authentication method not supported",
    8: "strong authentication required",
    10: "referral",
    11: "administrative limit exceeded",
    13: "confidentiality required",
    16: "no such attribute",
    17: "undefined attribute type",
    19: "constraint violation",
    32: "no such object",
    34: "invalid DN syntax",
    48: "inappropriate authentication",
    49: "invalid credentials",
    50: "insufficient access rights",
    51: "busy",
    52: "unavailable",
    53: "unwilling to perform",
    80: "other",
}

#: What an operator is told to look at, by result code, when the directory's own text is no help.
RESULT_HINTS: dict[int, str] = {
    32: "The search base does not exist, or the account that searches may not see it (many "
    "directories answer 'no such object' to hide what an account may not read).",
    34: "A distinguished name is not written the way the directory reads it.",
    49: "The name or password is wrong.",
    50: "The account that searches may not read what was asked: give it read access to the "
    "people and group bases.",
    53: "The directory will not do this over this connection; it may require TLS.",
}


def result_name(code: int | None) -> str:
    """
    Args:
        code: An LDAP result code.

    Returns:
        Its name, or the number when this client has no word for it.
    """
    if code is None:
        return ""
    return RESULT_NAMES.get(code, f"result {code}")


class LdapError(NoustError):
    """
    A directory could not be reached, spoke wrongly, or refused something.

    Attributes:
        code: The LDAP result code when the server answered with one, else None.
        diagnostic: What the server said, verbatim, for the audit log.
    """

    def __init__(
        self,
        message: str,
        details: str = "",
        *,
        code: int | None = None,
        diagnostic: str = "",
    ) -> None:
        """
        Args:
            message: What happened.
            details: How to fix it.
            code: The LDAP result code, when there was one.
            diagnostic: The server's own message.
        """
        super().__init__(message, details)
        self.code = code
        self.diagnostic = diagnostic


# --------------------------------------------------------------------------- BER


def encode_length(size: int) -> bytes:
    """
    Args:
        size: The length of a value.

    Returns:
        Its BER length octets: short form below 128, long form above.
    """
    if size < 0x80:
        return bytes([size])
    octets = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(octets)]) + octets


def tlv(tag: int, content: bytes) -> bytes:
    """
    Args:
        tag: The identifier octet.
        content: The value.

    Returns:
        The element: tag, length, value.
    """
    return bytes([tag]) + encode_length(len(content)) + content


def encode_integer(value: int, tag: int = _INTEGER) -> bytes:
    """
    Args:
        value: A signed integer.
        tag: INTEGER, or ENUMERATED.

    Returns:
        Its minimal two's-complement encoding.
    """
    width = (
        max(1, (value.bit_length() + 8) // 8) if value >= 0 else (value + 1).bit_length() // 8 + 1
    )
    return tlv(tag, value.to_bytes(width, "big", signed=True))


def encode_string(value: bytes | str, tag: int = _OCTET_STRING) -> bytes:
    """
    Args:
        value: Text (UTF-8) or bytes.
        tag: OCTET STRING, or a context tag.

    Returns:
        The element.
    """
    return tlv(tag, value if isinstance(value, bytes) else value.encode("utf-8"))


def encode_boolean(value: bool) -> bytes:
    """
    Args:
        value: A flag.

    Returns:
        BOOLEAN, with ``0xFF`` for true as BER's canonical form asks.
    """
    return tlv(_BOOLEAN, b"\xff" if value else b"\x00")


class Reader:
    """
    Read BER elements from a buffer, refusing whatever does not fit in it.

    Args:
        data: The bytes.
        depth: How deeply this reader is nested, for the ceiling.
    """

    def __init__(self, data: bytes, depth: int = 0) -> None:
        if depth > MAX_DEPTH:
            raise LdapError("The directory's answer is nested too deeply")
        self.data = data
        self.position = 0
        self.depth = depth

    @property
    def done(self) -> bool:
        """Whether every byte was read."""
        return self.position >= len(self.data)

    def read(self) -> tuple[int, bytes]:
        """
        Returns:
            The next element's tag and value.

        Raises:
            LdapError: When it is cut short, uses an indefinite or an absurd length, or a tag
                number that does not fit one octet.
        """
        data, at = self.data, self.position
        if at + 2 > len(data):
            raise LdapError("The directory's answer is cut short")
        tag = data[at]
        if tag & 0x1F == 0x1F:
            raise LdapError("The directory's answer uses a tag this client does not read")
        first = data[at + 1]
        at += 2
        if first < 0x80:
            size = first
        else:
            count = first & 0x7F
            if count == 0 or count > 4 or at + count > len(data):
                raise LdapError("The directory's answer has a length this client does not read")
            size = int.from_bytes(data[at : at + count], "big")
            at += count
        if size > MAX_MESSAGE or at + size > len(data):
            raise LdapError("The directory's answer is longer than it says or than is accepted")
        self.position = at + size
        return tag, data[at : at + size]

    def expect(self, tag: int) -> bytes:
        """
        Args:
            tag: The tag the next element must have.

        Returns:
            Its value.

        Raises:
            LdapError: When it has another.
        """
        found, value = self.read()
        if found != tag:
            raise LdapError(f"The directory sent an element {found:#04x} where {tag:#04x} was due")
        return value

    def sub(self, value: bytes) -> Reader:
        """
        Args:
            value: The content of an element of this one.

        Returns:
            A reader for it, one level deeper.
        """
        return Reader(value, self.depth + 1)


def decode_integer(value: bytes) -> int:
    """
    Args:
        value: The content of an INTEGER or ENUMERATED.

    Returns:
        The number.

    Raises:
        LdapError: When it is empty or absurdly long.
    """
    if not value or len(value) > 8:
        raise LdapError("The directory sent a number this client does not read")
    return int.from_bytes(value, "big", signed=True)


def decode_text(value: bytes) -> str:
    """
    Args:
        value: The content of an OCTET STRING that holds text.

    Returns:
        It as UTF-8; undecodable bytes are replaced, never raised on: a directory's
        diagnostic must not be able to break the sign-in that reports it.
    """
    return value.decode("utf-8", errors="replace")


# ----------------------------------------------------------------------- escaping


def escape_filter_value(value: str) -> str:
    """
    Make text safe to put in a search filter (RFC 4515 section 3).

    Args:
        value: What a person typed.

    Returns:
        It with ``\\``, ``*``, ``(``, ``)`` and NUL as ``\\XX``; nothing a person types can add
        a condition, close a group or match more than they named.
    """
    out = []
    for char in value:
        if char in "\\*()\x00":
            out.append(f"\\{ord(char):02x}")
        else:
            out.append(char)
    return "".join(out)


def escape_dn_value(value: str) -> str:
    """
    Make text safe to put in a distinguished name (RFC 4514 section 2.4).

    Args:
        value: What a person typed.

    Returns:
        It with the characters that separate or end a name escaped, and a leading ``#`` or space
        and a trailing space as the standard asks.
    """
    out = []
    for index, char in enumerate(value):
        if char in ',+"\\<>;=' or char == "\x00":
            out.append("\\00" if char == "\x00" else "\\" + char)
        elif (index == 0 and char in "# ") or (index == len(value) - 1 and char == " "):
            out.append("\\" + char)
        else:
            out.append(char)
    return "".join(out)


def rdn_value(dn: str) -> str:
    """
    Args:
        dn: A distinguished name, such as ``cn=ops,ou=groups,dc=acme,dc=test``.

    Returns:
        The value of its first component (``ops``), unescaped; empty when it has none.
    """
    first = ""
    escaped = False
    for char in dn:
        if escaped:
            first += char
            escaped = False
        elif char == "\\":
            escaped = True
            first += char
        elif char == ",":
            break
        else:
            first += char
    _, _, value = first.partition("=")
    return re.sub(r"\\(.)", r"\1", value).strip()


# ------------------------------------------------------------------------ filters


def _unescape_assertion(text: str) -> bytes:
    """
    Args:
        text: The value of a filter item, with ``\\XX`` escapes.

    Returns:
        The octets it stands for.

    Raises:
        LdapError: For an escape that is not two hex digits.
    """
    out = bytearray()
    raw = text.encode("utf-8")
    index = 0
    while index < len(raw):
        if raw[index] == 0x5C:
            digits = raw[index + 1 : index + 3]
            if len(digits) != 2 or not re.fullmatch(rb"[0-9A-Fa-f]{2}", digits):
                raise LdapError("A filter has an escape that is not \\ and two hex digits")
            out.append(int(digits, 16))
            index += 3
        else:
            out.append(raw[index])
            index += 1
    return bytes(out)


def _split_filters(text: str) -> list[str]:
    """
    Args:
        text: The inside of an ``&`` or ``|``: a run of parenthesised filters.

    Returns:
        Each, with its parentheses.

    Raises:
        LdapError: When the parentheses do not balance.
    """
    parts: list[str] = []
    depth = 0
    start = 0
    escaped = False
    for index, char in enumerate(text):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "(":
            if depth == 0:
                start = index
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                raise LdapError("A filter has a closing parenthesis too many")
            if depth == 0:
                parts.append(text[start : index + 1])
        elif depth == 0:
            raise LdapError("A filter has text outside its parentheses")
    if depth != 0:
        raise LdapError("A filter has an opening parenthesis too many")
    return parts


def encode_filter(text: str, depth: int = 0) -> bytes:
    """
    Turn a search filter (RFC 4515) into BER.

    Args:
        text: ``(&(objectClass=person)(uid=maria))``. Equality, presence, substrings, ``>=``,
            ``<=``, ``~=`` and the ``&``, ``|`` and ``!`` of them.
        depth: How deep this filter is nested.

    Returns:
        The encoded filter.

    Raises:
        LdapError: For a filter that is malformed, nested too deeply, or uses an extensible match.
    """
    if depth > MAX_DEPTH:
        raise LdapError("A filter is nested too deeply")
    stripped = text.strip()
    if len(stripped) < 3 or stripped[0] != "(" or stripped[-1] != ")":
        raise LdapError(f"A filter is written in parentheses: {text[:60]!r}")
    inner = stripped[1:-1]
    if inner[:1] in ("&", "|"):
        parts = _split_filters(inner[1:])
        if not parts:
            raise LdapError("An & or | filter needs at least one condition")
        body = b"".join(encode_filter(part, depth + 1) for part in parts)
        return tlv(0xA0 if inner[0] == "&" else 0xA1, body)
    if inner[:1] == "!":
        parts = _split_filters(inner[1:])
        if len(parts) != 1:
            raise LdapError("A ! filter negates exactly one condition")
        return tlv(0xA2, encode_filter(parts[0], depth + 1))
    match = re.fullmatch(r"([^=<>~:]+?)(~=|>=|<=|=)(.*)", inner, re.DOTALL)
    if match is None or ":" in match.group(1):
        raise LdapError(f"A filter condition is not attribute=value: {inner[:60]!r}")
    attribute, operator, value = match.groups()
    if not _ATTRIBUTE.fullmatch(attribute):
        raise LdapError(f"Not an attribute name: {attribute[:40]!r}")
    if "(" in value or ")" in value or "\x00" in value:
        raise LdapError(
            "A filter value has a parenthesis or a NUL that is not escaped",
            "Escape what a person typed with escape_filter_value before it goes in a filter.",
        )
    if operator == "~=":
        return tlv(0xA8, encode_string(attribute) + encode_string(_unescape_assertion(value)))
    if operator == ">=":
        return tlv(0xA5, encode_string(attribute) + encode_string(_unescape_assertion(value)))
    if operator == "<=":
        return tlv(0xA6, encode_string(attribute) + encode_string(_unescape_assertion(value)))
    if value == "*":
        return encode_string(attribute, 0x87)
    if "*" in re.sub(r"\\..", "", value):
        return _substring(attribute, value)
    return tlv(0xA3, encode_string(attribute) + encode_string(_unescape_assertion(value)))


def _substring(attribute: str, value: str) -> bytes:
    """
    Args:
        attribute: The attribute.
        value: A value with ``*`` in it: ``ma*``, ``*ria``, ``m*r*a``.

    Returns:
        The substring filter.
    """
    pieces = re.split(r"(?<!\\)\*", value)
    parts = b""
    if pieces[0]:
        parts += encode_string(_unescape_assertion(pieces[0]), 0x80)
    for middle in pieces[1:-1]:
        if middle:
            parts += encode_string(_unescape_assertion(middle), 0x81)
    if pieces[-1]:
        parts += encode_string(_unescape_assertion(pieces[-1]), 0x82)
    if not parts:
        raise LdapError("A substring filter needs something to match")
    return tlv(0xA4, encode_string(attribute) + tlv(_SEQUENCE, parts))


# ---------------------------------------------------------------------- messages


def encode_bind_request(message_id: int, dn: str, password: str) -> bytes:
    """
    Args:
        message_id: The message number.
        dn: The name to bind as.
        password: Its password.

    Returns:
        A simple bind, LDAP version 3.
    """
    body = encode_integer(3) + encode_string(dn) + encode_string(password, _SIMPLE_AUTH)
    return tlv(_SEQUENCE, encode_integer(message_id) + tlv(_BIND_REQUEST, body))


def encode_unbind_request(message_id: int) -> bytes:
    """
    Args:
        message_id: The message number.

    Returns:
        An unbind, which ends the session.
    """
    return tlv(_SEQUENCE, encode_integer(message_id) + tlv(_UNBIND_REQUEST, b""))


def encode_search_request(
    message_id: int,
    base: str,
    scope: int,
    filter_text: str,
    attributes: Iterable[str],
    *,
    size_limit: int,
    time_limit: int,
) -> bytes:
    """
    Args:
        message_id: The message number.
        base: Where to search from.
        scope: :data:`SCOPE_BASE`, :data:`SCOPE_ONE` or :data:`SCOPE_SUBTREE`.
        filter_text: The filter.
        attributes: The attributes to return; none for the entry's name only.
        size_limit: The most entries to return.
        time_limit: Seconds the server may take.

    Returns:
        A search that never follows aliases and returns values, not only types.
    """
    body = (
        encode_string(base)
        + encode_integer(scope, _ENUMERATED)
        + encode_integer(0, _ENUMERATED)
        + encode_integer(size_limit)
        + encode_integer(time_limit)
        + encode_boolean(False)
        + encode_filter(filter_text)
        + tlv(_SEQUENCE, b"".join(encode_string(a) for a in attributes))
    )
    return tlv(_SEQUENCE, encode_integer(message_id) + tlv(_SEARCH_REQUEST, body))


def encode_starttls_request(message_id: int) -> bytes:
    """
    Args:
        message_id: The message number.

    Returns:
        The extended request that asks the server to switch the connection to TLS.
    """
    body = encode_string(OID_STARTTLS, 0x80)
    return tlv(_SEQUENCE, encode_integer(message_id) + tlv(_EXTENDED_REQUEST, body))


@dataclass(frozen=True)
class LdapResult:
    """
    An LDAPResult (RFC 4511 section 4.1.9).

    Attributes:
        code: The result code.
        matched_dn: How much of a name the server matched.
        diagnostic: The server's own words.
    """

    code: int
    matched_dn: str = ""
    diagnostic: str = ""


def decode_result(value: bytes, reader: Reader) -> LdapResult:
    """
    Args:
        value: The content of a response whose first components are an LDAPResult.
        reader: The reader the response was read with, for nesting.

    Returns:
        The result.
    """
    inner = reader.sub(value)
    code = decode_integer(inner.expect(_ENUMERATED))
    matched = decode_text(inner.expect(_OCTET_STRING))
    diagnostic = decode_text(inner.expect(_OCTET_STRING))
    return LdapResult(code, matched, diagnostic)


@dataclass(frozen=True)
class LdapEntry:
    """
    One entry a search returned.

    Attributes:
        dn: Its distinguished name.
        attributes: Its attributes by lower-case name, each a list of raw values.
    """

    dn: str
    attributes: dict[str, list[bytes]] = field(default_factory=dict)

    def values(self, name: str) -> list[str]:
        """
        Args:
            name: An attribute name, in any case.

        Returns:
            Its values as text.
        """
        return [decode_text(v) for v in self.attributes.get(name.lower(), [])]

    def first(self, name: str) -> str | None:
        """
        Args:
            name: An attribute name, in any case.

        Returns:
            Its first value as text, or None when it has none or the first is empty.
        """
        found = self.values(name)
        return found[0] if found and found[0] else None

    def identifier(self, name: str) -> str | None:
        """
        Args:
            name: The attribute that is the entry's stable id.

        Returns:
            Its first value as printable text, or as hexadecimal when it is binary (Active
            Directory's ``objectGUID`` is 16 raw bytes).
        """
        raw = self.attributes.get(name.lower())
        if not raw or not raw[0]:
            return None
        value = raw[0]
        try:
            text = value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
        return text if text.isprintable() and text == text.strip() else value.hex()


def decode_entry(value: bytes, reader: Reader) -> LdapEntry:
    """
    Args:
        value: The content of a SearchResultEntry.
        reader: The reader it was read with.

    Returns:
        The entry.
    """
    inner = reader.sub(value)
    dn = decode_text(inner.expect(_OCTET_STRING))
    attributes: dict[str, list[bytes]] = {}
    listing = inner.sub(inner.expect(_SEQUENCE))
    while not listing.done:
        one = listing.sub(listing.expect(_SEQUENCE))
        name = decode_text(one.expect(_OCTET_STRING)).lower()
        values = one.sub(one.expect(_SET))
        found = attributes.setdefault(name, [])
        while not values.done:
            found.append(values.expect(_OCTET_STRING))
    return LdapEntry(dn, attributes)


# ------------------------------------------------------------------- connection


def _is_loopback(host: str) -> bool:
    """
    Args:
        host: A host name or address literal.

    Returns:
        Whether it is this machine, the one place plain text is accepted.
    """
    from noust.core.accounts.oidc_model import is_loopback_host

    return is_loopback_host(host)


class LdapConnection:
    """
    One connection to a directory: connect, bind, search, close.

    Args:
        host: The directory's name or address.
        port: Its port.
        tls: :data:`TLS_LDAPS`, :data:`TLS_STARTTLS` or :data:`TLS_NONE` (loopback only).
        ca_file: A certificate authority bundle to trust instead of the system's.
        timeout: Seconds for the whole exchange.
        guard: Judges the host before it is reached; :func:`noust.core.outbound.require_public_host`
            with the operator's allowed hosts by default.
        resolver: How a name becomes addresses (replaced by tests).
        clock: The time source (replaced by tests).
    """

    def __init__(
        self,
        host: str,
        port: int,
        tls: str = TLS_LDAPS,
        *,
        ca_file: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        guard: Callable[[str, tuple[str, ...]], None] | None = None,
        resolver: Callable[[str], tuple[str, ...]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if tls not in TLS_MODES:
            raise LdapError(f"Unknown TLS mode {tls!r}", f"Use one of: {', '.join(TLS_MODES)}.")
        if tls == TLS_NONE and not _is_loopback(host):
            raise LdapError(
                "A directory over the network must use TLS",
                "Use ldaps or StartTLS: a password never travels in clear text. Plain text is "
                "accepted only for a directory on this machine.",
            )
        self.host, self.port, self.tls = host, port, tls
        self.ca_file = ca_file
        self.timeout = timeout
        self._guard = guard
        self._resolver = resolver
        self._clock = clock
        self._deadline = clock() + timeout
        self._socket: socket.socket | ssl.SSLSocket | None = None
        self._next_id = 1

    # -- plumbing

    def _context(self) -> ssl.SSLContext:
        """
        Returns:
            A TLS context that verifies the certificate and the host name.
        """
        context = (
            ssl.create_default_context(cafile=self.ca_file)
            if self.ca_file
            else (ssl.create_default_context())
        )
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        return context

    def connect(self) -> None:
        """
        Judge the host, reach one of the addresses that was judged, and speak TLS if asked to.

        Raises:
            LdapError: When the host is refused, unreachable, or its certificate is not trusted.
        """
        resolve = self._resolver or outbound.resolve_host
        try:
            addresses = resolve(self.host)
        except OSError as exc:
            raise LdapError(f"Could not resolve the directory {self.host!r}: {exc}") from exc
        if not addresses:
            raise LdapError(f"The directory {self.host!r} resolves to no address")
        try:
            if self._guard is not None:
                self._guard(self.host, addresses)
            else:
                outbound.require_public_host(
                    self.host,
                    allowed=outbound.trusted_hosts(),
                    resolver=lambda _name: addresses,
                )
        except outbound.DestinationRefused as exc:
            raise LdapError(
                str(exc),
                "A directory on a private network is allowed by naming it in "
                "security.allow_private_hosts.",
            ) from exc
        last: OSError | None = None
        for address in addresses:
            try:
                raw = socket.create_connection((address, self.port), timeout=self.timeout)
            except OSError as exc:
                last = exc
                continue
            raw.settimeout(self.timeout)
            self._socket = raw
            break
        if self._socket is None:
            raise LdapError(
                f"Could not connect to the directory {self.host}:{self.port}: {last}",
                "Check the address, the port and the firewall.",
            )
        if self.tls == TLS_LDAPS:
            self._wrap()
        elif self.tls == TLS_STARTTLS:
            self._start_tls()

    def _wrap(self) -> None:
        """
        Raises:
            LdapError: When the TLS handshake fails or the certificate is not trusted.
        """
        if self._socket is None:
            raise LdapError("Not connected to the directory")
        try:
            self._socket = self._context().wrap_socket(self._socket, server_hostname=self.host)
        except (ssl.SSLError, OSError) as exc:
            self.close()
            raise LdapError(
                f"The directory's certificate or TLS was refused: {exc}",
                "Trust its certificate authority with ca_file, and use the name on the certificate.",
            ) from exc

    def _start_tls(self) -> None:
        """
        Raises:
            LdapError: When the server does not accept StartTLS, or the handshake fails.
        """
        message_id = self._id()
        self._send(encode_starttls_request(message_id))
        _, tag, value, reader = self._receive()
        if tag != _EXTENDED_RESPONSE:
            raise LdapError("The directory did not answer StartTLS as an extended response")
        result = decode_result(value, reader)
        if result.code != RESULT_SUCCESS:
            raise LdapError(
                "The directory refused StartTLS",
                "Use ldaps, or enable StartTLS on the directory.",
                code=result.code,
                diagnostic=result.diagnostic,
            )
        self._wrap()

    def _id(self) -> int:
        """
        Returns:
            The next message number.
        """
        number = self._next_id
        self._next_id += 1
        return number

    def _send(self, data: bytes) -> None:
        """
        Args:
            data: One encoded message.

        Raises:
            LdapError: When it cannot be sent.
        """
        if self._socket is None:
            raise LdapError("Not connected to the directory")
        try:
            self._socket.sendall(data)
        except OSError as exc:
            raise LdapError(f"The directory connection failed: {exc}") from exc

    def _read_exactly(self, count: int) -> bytes:
        """
        Args:
            count: How many bytes.

        Returns:
            Exactly that many.

        Raises:
            LdapError: At the deadline, or when the connection closes.
        """
        if self._socket is None:
            raise LdapError("Not connected to the directory")
        chunks = bytearray()
        while len(chunks) < count:
            remaining = self._deadline - self._clock()
            if remaining <= 0:
                raise LdapError("The directory did not answer in time")
            self._socket.settimeout(min(self.timeout, remaining))
            try:
                part = self._socket.recv(min(65536, count - len(chunks)))
            except (TimeoutError, OSError) as exc:
                raise LdapError(f"The directory did not answer: {exc}") from exc
            if not part:
                raise LdapError("The directory closed the connection")
            chunks += part
        return bytes(chunks)

    def _receive(self) -> tuple[int, int, bytes, Reader]:
        """
        Returns:
            The message id, the operation's tag and value, and a reader for nesting.

        Raises:
            LdapError: For a message that is malformed or longer than is accepted.
        """
        head = self._read_exactly(2)
        if head[0] != _SEQUENCE:
            raise LdapError("The directory sent something that is not an LDAP message")
        size = head[1]
        if size >= 0x80:
            count = size & 0x7F
            if count == 0 or count > 4:
                raise LdapError("The directory's message has a length this client does not read")
            size = int.from_bytes(self._read_exactly(count), "big")
        if size > MAX_MESSAGE:
            raise LdapError("The directory's message is longer than is accepted")
        body = self._read_exactly(size)
        reader = Reader(body)
        message_id = decode_integer(reader.expect(_INTEGER))
        tag, value = reader.read()
        return message_id, tag, value, reader

    # -- operations

    def bind(self, dn: str, password: str) -> None:
        """
        Prove a name and password to the directory.

        Args:
            dn: The name to bind as; empty for an anonymous bind (no password then).
            password: Its password. Empty is refused unless ``dn`` is empty too.

        Raises:
            LdapError: When the password is empty, the server refuses the bind (``code`` 49 for
                a wrong password), or answers wrongly.
        """
        if dn and not password:
            raise LdapError(
                "An empty password is not a password",
                "A bind with a name and no password is an anonymous one, which most directories "
                "accept; it is never sent.",
            )
        if "\x00" in dn or "\x00" in password:
            raise LdapError("A name or password with a NUL character is not accepted")
        message_id = self._id()
        self._send(encode_bind_request(message_id, dn, password))
        answered, tag, value, reader = self._receive()
        if answered != message_id or tag != _BIND_RESPONSE:
            raise LdapError("The directory did not answer the bind")
        result = decode_result(value, reader)
        if result.code != RESULT_SUCCESS:
            raise LdapError(
                "The directory refused the bind",
                code=result.code,
                diagnostic=result.diagnostic,
            )

    def search(
        self,
        base: str,
        filter_text: str,
        attributes: Iterable[str],
        *,
        scope: int = SCOPE_SUBTREE,
        size_limit: int = 2,
        time_limit: int = 10,
    ) -> list[LdapEntry]:
        """
        Search the directory.

        Args:
            base: Where to search from.
            filter_text: The filter (build it from a template and :func:`escape_filter_value`).
            attributes: The attributes to return.
            scope: :data:`SCOPE_SUBTREE` by default.
            size_limit: The most entries to accept; a server that returns more has the search cut.
            time_limit: Seconds the server may take.

        Returns:
            The entries, at most ``size_limit``. Reaching the limit is not an error: the caller
            that needs exactly one asks for two.

        Raises:
            LdapError: When the server refuses the search or answers wrongly.
        """
        message_id = self._id()
        self._send(
            encode_search_request(
                message_id,
                base,
                scope,
                filter_text,
                attributes,
                size_limit=size_limit,
                time_limit=time_limit,
            )
        )
        entries: list[LdapEntry] = []
        while True:
            answered, tag, value, reader = self._receive()
            if answered != message_id:
                raise LdapError("The directory answered another request")
            if tag == _SEARCH_ENTRY:
                if len(entries) < size_limit:
                    entries.append(decode_entry(value, reader))
            elif tag == _SEARCH_REFERENCE:
                continue
            elif tag == _SEARCH_DONE:
                result = decode_result(value, reader)
                if result.code not in (RESULT_SUCCESS, RESULT_SIZE_LIMIT):
                    raise LdapError(
                        "The directory refused the search",
                        code=result.code,
                        diagnostic=result.diagnostic,
                    )
                return entries
            else:
                raise LdapError("The directory sent something unexpected in a search")

    def close(self) -> None:
        """Unbind politely and close; never raises."""
        sock, self._socket = self._socket, None
        if sock is None:
            return
        try:
            sock.sendall(encode_unbind_request(self._id()))
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def __enter__(self) -> LdapConnection:
        self.connect()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
