# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a Noust that an app store installed gets its new versions from (item 199).

A central installed from the App Center of a NAS is a container the store made, and the store is what
replaces it with the next version: each version is a package of its own that the store reviews. Telling
that operator to run ``docker compose pull`` leaves the app in a state the store knows nothing about.
The package says where it came from in ``NOUST_INSTALLED_FROM``, one word from a closed list; the
version check and the self-update publish it, and the console says where the versions arrive.
"""

from __future__ import annotations

from noust.core import paths

__all__ = ["SOURCES", "UGREEN_APP_CENTER", "installed_from"]

#: The package of the UGREEN App Center (``packaging/ugreen/``).
UGREEN_APP_CENTER = "ugreen-app-center"

#: Every place an installation can say it came from, and how a sentence names it.
SOURCES: dict[str, str] = {UGREEN_APP_CENTER: "the UGREEN App Center"}


def installed_from() -> str | None:
    """
    Returns:
        The word of :data:`SOURCES` that ``NOUST_INSTALLED_FROM`` holds; None when it is not set or
        holds anything else (a name nobody here knows is no place to send an operator to).
    """
    value = paths.getenv("INSTALLED_FROM")
    return value if value in SOURCES else None
