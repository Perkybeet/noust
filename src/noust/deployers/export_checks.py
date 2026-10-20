# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The checks of one field of an export document, shared by every part that reads one.

An export is untrusted input: it comes from a file, from another server or from a
client of the API. Each part of it (:mod:`noust.deployers.app_export` for the
application itself, :mod:`noust.deployers.export_runtime` for what it mounts and how
a container is run) checks its own fields with the same few questions, and all of
them refuse in the same words, naming the field as a path (``mounts[1].mode``), so
there is one place that decides what "wrong type" says.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from noust.core.exceptions import ValidationError


def fail(where: str, what: str) -> ValidationError:
    """
    Build the refusal for one field of a document.

    Args:
        where: The field, as a path (``app.port``).
        what: What is wrong with it.

    Returns:
        The error.
    """
    return ValidationError(
        f"The export document is not valid: {where} {what}",
        details="Export it again with 'noust app export', or fix the field.",
        field=where,
    )


def check_object(data: Any, where: str, *, optional: bool = False) -> dict[str, Any]:
    """
    Check an object field; an absent optional one reads as empty.

    Args:
        data: The value.
        where: The field, as a path.
        optional: Whether ``None`` is allowed.

    Returns:
        The object.

    Raises:
        ValidationError: It is not an object.
    """
    if data is None and optional:
        return {}
    if not isinstance(data, dict):
        raise fail(where, "must be an object")
    return data


def check_text(
    data: Mapping[str, Any], key: str, where: str, *, required: bool = False
) -> str | None:
    """
    Check a string field.

    Args:
        data: The object that holds it.
        key: Its name.
        where: The object, as a path.
        required: Whether it must be there and not blank.

    Returns:
        The string, or None when it is absent and not required.

    Raises:
        ValidationError: It is missing when required, or not a string.
    """
    value = data.get(key)
    if value is None:
        if required:
            raise fail(f"{where}.{key}", "is missing")
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise fail(f"{where}.{key}", "must be a string")
    return value


def check_number(data: Mapping[str, Any], key: str, where: str) -> int | None:
    """
    Check a whole-number field.

    Args:
        data: The object that holds it.
        key: Its name.
        where: The object, as a path.

    Returns:
        The number, or None when it is absent.

    Raises:
        ValidationError: It is not a whole number.
    """
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(f"{where}.{key}", "must be a whole number")
    return int(value)


def check_flag(data: Mapping[str, Any], key: str, where: str, default: bool = False) -> bool:
    """
    Check a boolean field.

    Args:
        data: The object that holds it.
        key: Its name.
        where: The object, as a path.
        default: What an absent field reads as.

    Returns:
        The flag.

    Raises:
        ValidationError: It is not true or false.
    """
    value = data.get(key, default)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise fail(f"{where}.{key}", "must be true or false")
    return value


def check_names(data: Mapping[str, Any], key: str, where: str) -> list[str]:
    """
    Check a list-of-strings field.

    Args:
        data: The object that holds it.
        key: Its name.
        where: The object, as a path.

    Returns:
        The strings; empty when absent.

    Raises:
        ValidationError: It is not a list of strings.
    """
    value = data.get(key) or []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise fail(f"{where}.{key}", "must be a list of strings")
    return list(value)
