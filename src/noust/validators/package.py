# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a package name is, judged in one place.

A name reaches a package manager from two directions: an application declares the libraries it
needs (``noust app packages``), and a removal names the packages of a piece of software. Either
way it becomes an argument of ``apt-get``, ``dnf`` or ``zypper`` running as root, so the one
thing that matters is that it can never be read as an option, a path, a pattern or a version pin.
"""

from __future__ import annotations

import re

from noust.core.exceptions import ValidationError

__all__ = ["validate_package"]

#: What a package name is: a word of letters, digits and ``+._-`` that does not begin with a
#: symbol, so it can never be read as an option, a path or a version pin.
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9+._-]{0,99}")


def validate_package(name: str) -> str:
    """
    Check that a text is a package name.

    Args:
        name: What the operator typed.

    Returns:
        The name, stripped.

    Raises:
        ValidationError: It is not a name of letters, digits and ``+._-`` starting with a
            letter or digit; versions, paths, options and spaces are not names.
    """
    text = name.strip() if isinstance(name, str) else ""
    if not _NAME.fullmatch(text):
        raise ValidationError(
            f"{name!r} is not a package name",
            details="A package name is letters, digits and + . _ - only, starting with a letter "
            "or a digit (libvips-dev, fonts-noto-core). A version, a path or an option is not "
            "a name.",
            field="packages",
        )
    return text
