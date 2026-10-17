# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which response headers a site may send, and what a header's value may hold.

A value lands between quotes in a directive of a file the web server loads as
root, so the check is one place for the site's own headers
(:mod:`noust.managers.site_policy`) and for the headers of a single route
(:mod:`noust.managers.site_routes`): a quote or a backslash would end the string,
``$`` and ``%`` expand variables, and a newline would end the directive.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from noust.core.exceptions import ValidationError

#: The most response headers a site, or one route of it, may add or change.
MAX_HEADERS = 30

_HEADER_NAME = re.compile(r"[A-Za-z0-9-]{1,64}")

#: What may not be in a response header's value.
_UNSAFE_VALUE = re.compile(r'["\\$%\x00-\x1f\x7f-\x9f\u2028\u2029]')

#: Headers the web server or the connection owns; setting them breaks responses.
_FORBIDDEN_HEADERS = frozenset(
    {
        "connection",
        "content-encoding",
        "content-length",
        "content-range",
        "host",
        "keep-alive",
        "set-cookie",
        "transfer-encoding",
        "upgrade",
    }
)

#: What every site sends unless told otherwise.
DEFAULT_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Frame-Options", "SAMEORIGIN"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
)


def check_header_value(name: str, value: Any, *, field: str = "headers") -> str:
    """
    Check one response header's value.

    Args:
        name: The header.
        value: Its value.
        field: The field to name in an error.

    Returns:
        The value.

    Raises:
        ValidationError: The value could end or alter the directive it is written in.
    """
    text = str(value)
    if not text.strip() or len(text) > 1024 or _UNSAFE_VALUE.search(text):
        raise ValidationError(
            f"The value of {name} cannot be used",
            details='It must be one line of at most 1024 characters, without quotes, "\\", "$" or "%".',
            field=field,
        )
    return text


def check_headers(
    value: Any, *, field: str = "headers", allow_removal: bool = True
) -> dict[str, str | None]:
    """
    Check the response headers to set.

    Args:
        value: A mapping of header to value; None removes a default.
        field: The field to name in an error.
        allow_removal: Whether a value of None (stop sending a default) is accepted.

    Returns:
        The headers, names in their canonical case.

    Raises:
        ValidationError: A name or a value is not acceptable.
    """
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field} is a mapping of header to value", field=field)
    if len(value) > MAX_HEADERS:
        raise ValidationError(f"A site may set at most {MAX_HEADERS} headers", field=field)
    checked: dict[str, str | None] = {}
    for raw_name, raw_value in value.items():
        name = str(raw_name).strip()
        if not _HEADER_NAME.fullmatch(name):
            raise ValidationError(f"{raw_name!r} is not a header name", field=field)
        if name.lower() in _FORBIDDEN_HEADERS:
            raise ValidationError(
                f"{name} belongs to the web server and cannot be set here",
                field=field,
            )
        key = next((known for known, _ in DEFAULT_HEADERS if known.lower() == name.lower()), name)
        if raw_value is None:
            if not allow_removal:
                raise ValidationError(f"{name} needs a value", field=field)
            checked[key] = None
        else:
            checked[key] = check_header_value(name, raw_value, field=field)
    return checked
