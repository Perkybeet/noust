# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a database instance Noust runs in a container may be called, and how much it may hold.

An instance's name becomes a unit name, a Compose project, a container, two
volumes, a directory and the part of an engine key after the ``@``, so it is an
allowlist as narrow as the narrowest of them: lower-case words joined by single
dashes, which is also what fits a URL segment and a file name. The same checks
run when the store writes a row and when a manager uses one, so a row edited by
hand cannot reach ``docker`` or ``systemctl``.

The field names are the API's (``name``, ``memory_mb``, ``port``), carried in
the error so a client can put the refusal next to its input.
"""

from __future__ import annotations

import re

from noust.core.exceptions import ValidationError

#: Longest name: ``noust-db-`` and the name must stay a short unit name.
MAX_NAME_LENGTH = 24

#: Lower-case words joined by single dashes, starting with a letter.
_NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*")

#: ``512m``, ``2g``, ``1536``: a number and an optional unit; bare numbers are megabytes.
_MEMORY = re.compile(r"(?P<number>[0-9]{1,7})(?P<unit>(?:[mg]b?)?)", re.IGNORECASE)

#: The lowest and highest port an instance is published on, on 127.0.0.1.
MIN_PORT = 1024
MAX_PORT = 65535


def check_instance_name(name: object) -> str:
    """
    Accept the name an operator gives an instance.

    Args:
        name: The candidate.

    Returns:
        The name, unchanged.

    Raises:
        ValidationError: It is not lower-case words joined by dashes, or it is
            longer than :data:`MAX_NAME_LENGTH`.
    """
    if not isinstance(name, str) or len(name) > MAX_NAME_LENGTH or _NAME.fullmatch(name) is None:
        raise ValidationError(
            f"{name!r} cannot be the name of a database instance",
            details=(
                f"Use lower-case letters, digits and single dashes, starting with a letter, "
                f"up to {MAX_NAME_LENGTH} characters: pg16, analytics, shop-cache."
            ),
            field="name",
        )
    return name


def parse_memory(value: object) -> int:
    """
    Read an amount of memory as megabytes.

    Args:
        value: A whole number (megabytes), or text such as ``512m``, ``1g`` or
            ``2048``. ``mb`` and ``gb`` are accepted; a gigabyte is 1024 MB.

    Returns:
        The megabytes.

    Raises:
        ValidationError: It is not a positive amount in one of those forms.
    """
    if isinstance(value, bool):
        raise _bad_memory(value)
    if isinstance(value, int):
        megabytes = value
    elif isinstance(value, str):
        found = _MEMORY.fullmatch(value.strip())
        if found is None:
            raise _bad_memory(value)
        unit = found.group("unit").lower()
        megabytes = int(found.group("number")) * (1024 if unit.startswith("g") else 1)
    else:
        raise _bad_memory(value)
    if megabytes <= 0:
        raise _bad_memory(value)
    return megabytes


def _bad_memory(value: object) -> ValidationError:
    """
    Build the refusal of an amount of memory nobody can read.

    Args:
        value: What was given.

    Returns:
        The error, about the ``memory_mb`` field.
    """
    return ValidationError(
        f"{value!r} is not an amount of memory",
        details="Give a whole number of megabytes, or a number with m or g: 512m, 1g, 2048.",
        field="memory_mb",
    )


def check_memory(value: object, *, minimum: int, maximum: int) -> int:
    """
    Accept the memory limit of an instance.

    Args:
        value: The amount, as :func:`parse_memory` reads it.
        minimum: The least the engine runs in, in megabytes.
        maximum: The most this server can give, in megabytes.

    Returns:
        The megabytes.

    Raises:
        ValidationError: It cannot be read, is below what the engine needs, or
            is above what the server can give.
    """
    megabytes = parse_memory(value)
    if megabytes < minimum:
        raise ValidationError(
            f"{megabytes} MB is too little memory for this engine",
            details=f"It needs at least {minimum} MB to start and answer.",
            field="memory_mb",
        )
    if megabytes > maximum:
        raise ValidationError(
            f"{megabytes} MB is more memory than this server can give an instance",
            details=f"At most {maximum} MB fit once the other instances and the server's own "
            "needs are kept. Give it less, or stop an instance that is not in use.",
            field="memory_mb",
        )
    return megabytes


def check_host_port(port: object) -> int:
    """
    Accept the port an instance is published on, on 127.0.0.1.

    Args:
        port: The candidate.

    Returns:
        The port.

    Raises:
        ValidationError: It is not a port from :data:`MIN_PORT` to :data:`MAX_PORT`.
    """
    if isinstance(port, bool) or not isinstance(port, int) or not MIN_PORT <= port <= MAX_PORT:
        raise ValidationError(
            f"{port!r} is not a port an instance can be published on",
            details=f"Use a port from {MIN_PORT} to {MAX_PORT}; it is bound to 127.0.0.1 only.",
            field="port",
        )
    return port
