# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the plain HTTP port that only redirects may take from a request (item 199).

The port is a door in cleartext, open to whoever can reach it, and what it answers is a ``Location``
built from the request. So nothing of the request is repeated unless it passed an allowlist here: the
host is a name, an IPv4 address or an IPv6 address in brackets (with the port the browser wrote
after it, which is dropped), the target is a path with a query and nothing else, and the ports of
its two settings are decimal digits. Each pattern is applied with ``fullmatch``: ``$`` matches before
a trailing newline, which is how a name with a line break once got through a validator.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from noust.core.exceptions import ValidationError
from noust.core.server_text import say

__all__ = [
    "MAX_HOST_LENGTH",
    "MAX_TARGET_LENGTH",
    "validate_listener_port",
    "validate_redirect_host",
    "validate_redirect_target",
]

#: The longest name a host may be (RFC 1035), and room for a port after it.
MAX_HOST_LENGTH = 253 + len(":65535")

#: The longest path and query read. Longer than any address the console has, and a browser's own
#: limit is far above it, so a request this long is not a person's.
MAX_TARGET_LENGTH = 2048

_LABEL = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
_NAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})*")
_IPV4 = re.compile(r"[0-9]{1,3}(?:\.[0-9]{1,3}){3}")
_IPV6 = re.compile(r"\[([0-9A-Fa-f:.]{2,45})\]")
_PORT = re.compile(r"[0-9]{1,5}")
#: The last label of something a browser reads as an address and not as a name (WHATWG URL: "ends in
#: a number"): decimal, or hexadecimal with ``0x``.
_NUMBER = re.compile(r"[0-9]+|0[xX][0-9A-Fa-f]*")
#: What a request's path and query may be made of: visible ASCII only, the way a browser writes it
#: (everything else arrives percent-encoded), without the backslash that some browsers read as a
#: slash and without ``#``, which a request never carries.
_TARGET = re.compile(r"/[\x21\x22\x24-\x5b\x5d-\x7e]*")


def _port_of(text: str) -> bool:
    """
    Args:
        text: What followed the colon of a ``Host`` header.

    Returns:
        Whether it is a port number in decimal.
    """
    return _PORT.fullmatch(text) is not None and 1 <= int(text) <= 65535


def validate_redirect_host(value: Any) -> str:
    """
    Judge the ``Host`` a browser sent to the redirecting port.

    Args:
        value: The header's value, exactly as it arrived.

    Returns:
        The host to redirect to: a lower-case name, an IPv4 address, or an IPv6 address in
        brackets. A port after it is checked and dropped, because the redirect goes to the
        console's port, not the one the browser used for this.

    Raises:
        ValidationError: It is not a name, an address or an address in brackets, with or without
            a port.
    """
    if not isinstance(value, str) or not value or len(value) > MAX_HOST_LENGTH:
        raise ValidationError.from_text(say("redirect.host"), field="host")
    host, port = value, None
    if value.startswith("["):
        end = value.find("]")
        host, rest = value[: end + 1], value[end + 1 :]
        if end == -1 or (rest and not (rest.startswith(":") and _port_of(rest[1:]))):
            raise ValidationError.from_text(say("redirect.host"), field="host")
    elif ":" in value:
        host, _, port = value.partition(":")
        if not _port_of(port):
            raise ValidationError.from_text(say("redirect.host"), field="host")
    if (bracketed := _IPV6.fullmatch(host)) is not None:
        try:
            return f"[{ipaddress.IPv6Address(bracketed.group(1)).compressed}]"
        except ValueError:
            raise ValidationError.from_text(say("redirect.host"), field="host") from None
    if _IPV4.fullmatch(host):
        try:
            return str(ipaddress.IPv4Address(host))
        except ValueError:
            raise ValidationError.from_text(say("redirect.host"), field="host") from None
    # A name whose last label is a number is not a name (1.2.3 and 0x7f000001 are read as addresses by
    # browsers, and the numbers of a top-level domain are reserved): refused, so no spelling of an
    # address other than the dotted one gets through as a name.
    if len(host) > 253 or not _NAME.fullmatch(host) or _NUMBER.fullmatch(host.rsplit(".", 1)[-1]):
        raise ValidationError.from_text(say("redirect.host"), field="host")
    return host.lower()


def validate_redirect_target(value: Any) -> str:
    """
    Judge the path and query of a request to the redirecting port.

    Args:
        value: The request target, exactly as it arrived.

    Returns:
        The same text. Nothing is rewritten: a target that is not what a browser sends for a page
        is refused instead of being repaired into something else.

    Raises:
        ValidationError: It does not start with one slash, it is too long, or it holds anything
            but visible ASCII (a space, a control character, a backslash, a ``#``).
    """
    if (
        not isinstance(value, str)
        or len(value) > MAX_TARGET_LENGTH
        or _TARGET.fullmatch(value) is None
    ):
        raise ValidationError.from_text(say("redirect.target"), field="target")
    return value


def validate_listener_port(name: str, value: Any) -> int:
    """
    Judge a port that comes from an environment variable.

    Args:
        name: The variable, for the error.
        value: Its text.

    Returns:
        The port.

    Raises:
        ValidationError: It is not 1 to 65535 in ASCII decimal digits. ``int`` alone would take
            ``" 8442 "``, ``"+8442"``, ``"8_442"`` and the digits of other scripts.
    """
    if not isinstance(value, str) or not _port_of(value):
        raise ValidationError.from_text(say("redirect.port", name=name), field=name)
    return int(value)
