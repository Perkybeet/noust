# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The page a question to the built-in assistant is asked from (item 171).

The console says which page the person is on, so that Noust reads what that page shows and gives it to
the assistant to explain. What it says is text from a browser and becomes part of a call to the API,
so it is judged here against an allowlist, as every name that becomes part of a path is: a kind from a
closed list, and for each kind the one value it takes, in the shape of what it names.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from noust.core.exceptions import ValidationError

__all__ = ["PAGE_KINDS", "validate_page"]

#: The pages Noust knows how to read for the assistant, and the field each is about.
PAGE_KINDS: dict[str, str] = {
    "deployment": "id",
    "diagnosis": "domain",
    "alert": "rule",
    "page": "route",
}

_DOMAIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,99}")
_ROUTE = re.compile(r"/[A-Za-z0-9_.\-/]{0,199}")
_SERVER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]{0,63}")
_LARGEST_ID = 2**31 - 1


def _text(value: Any, pattern: re.Pattern[str], field: str, what: str) -> str:
    """
    Args:
        value: What the browser said.
        pattern: What it must be.
        field: The field, for the error.
        what: What it names, for the error.

    Returns:
        The value.

    Raises:
        ValidationError: It is not text of that shape.
    """
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValidationError(
            f"{field} is not {what}",
            details=f"Give {what}, using letters, digits, dots and hyphens only.",
            field=field,
        )
    return value


def validate_page(page: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """
    Judge the page a question is asked from.

    Args:
        page: ``{"kind": ..., <the one field of the kind>: ..., "server"?: ...}``, or None for a
            question that is not about a page.

    Returns:
        The page as it will be used, or None.

    Raises:
        ValidationError: A kind that is not known, a field that does not belong, or a value that is
            not the shape of what it names.
    """
    if page is None:
        return None
    if not isinstance(page, Mapping):
        raise ValidationError("page must be an object", field="page")
    kind = page.get("kind")
    if not isinstance(kind, str) or kind not in PAGE_KINDS:
        raise ValidationError(
            f"{kind!r} is not a page Noust can read for the assistant",
            details=f"Use one of: {', '.join(PAGE_KINDS)}.",
            field="page.kind",
        )
    field = PAGE_KINDS[kind]
    unknown = sorted(set(page) - {"kind", field, "server"})
    if unknown:
        raise ValidationError(
            f"A page of kind {kind} takes {field}, not {', '.join(unknown)}", field="page"
        )
    value = page.get(field)
    checked: dict[str, Any] = {"kind": kind}
    if field == "id":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= _LARGEST_ID:
            raise ValidationError("id is not the number of a deployment", field="page.id")
        checked["id"] = value
    elif field == "domain":
        checked["domain"] = _text(value, _DOMAIN, "domain", "a domain name")
    elif field == "rule":
        checked["rule"] = _text(value, _NAME, "rule", "the name of an alert rule")
    else:
        checked["route"] = _text(value, _ROUTE, "route", "a route of the console")
    server = page.get("server")
    if server is not None:
        checked["server"] = _text(server, _SERVER, "server", "the name of a server")
    return checked
