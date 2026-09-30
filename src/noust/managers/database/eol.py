# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When the upstream project stops supporting the engine version a server runs.

Noust installs the version the distribution ships and adds no third-party
repository to get another (spec 3.1 §6, decision 10). What it owes the
operator in exchange is the date: Ubuntu 22.04 ships PostgreSQL 14, whose
upstream support ends in November 2026, and nothing on the server says so.

The dates are the projects' own published policies, kept here as data:

- PostgreSQL: five years per major version
  (https://www.postgresql.org/support/versioning/).
- MySQL: 8.0 until April 2026, 8.4 LTS until April 2032
  (https://www.mysql.com/support/eol-notice.html).
- MariaDB: the long-term releases' published end dates
  (https://mariadb.org/about/#maintenance-policy).
- MongoDB: the server lifecycle schedule
  (https://www.mongodb.com/legal/support-policy/lifecycles).

Redis and Valkey publish no fixed end date per version, so none is invented:
the notice says there is none to show. A distribution keeps patching the
package it ships for as long as the distribution itself is supported, often
past the upstream date, and the message says that too rather than alarming an
operator whose distribution still backports security fixes.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

#: Days before the end of support from which the notice turns into a warning.
WARNING_WINDOW_DAYS = 180

#: Upstream end-of-support dates, by family and major version.
END_OF_LIFE: dict[str, dict[str, date]] = {
    "postgresql": {
        "11": date(2023, 11, 9),
        "12": date(2024, 11, 21),
        "13": date(2025, 11, 13),
        "14": date(2026, 11, 12),
        "15": date(2027, 11, 11),
        "16": date(2028, 11, 9),
        "17": date(2029, 11, 8),
        "18": date(2030, 11, 14),
    },
    "mysql": {
        "5.7": date(2023, 10, 31),
        "8.0": date(2026, 4, 30),
        "8.4": date(2032, 4, 30),
    },
    "mariadb": {
        "10.4": date(2024, 6, 18),
        "10.5": date(2025, 6, 24),
        "10.6": date(2026, 7, 6),
        "10.11": date(2028, 2, 16),
        "11.4": date(2029, 5, 29),
    },
    "mongodb": {
        "5.0": date(2024, 10, 31),
        "6.0": date(2025, 7, 31),
        "7.0": date(2027, 8, 31),
        "8.0": date(2029, 10, 31),
    },
}

#: Families whose major version is the first number alone.
_SINGLE_NUMBER_MAJORS = frozenset({"postgresql"})

SupportStatus = Literal["supported", "ending_soon", "ended", "unknown"]


@dataclass(frozen=True)
class SupportNotice:
    """
    Where an engine version stands in its upstream support.

    Attributes:
        family: ``postgresql``, ``mysql``, ``mariadb``, ``mongodb``, ``redis``
            or ``valkey``.
        version: The version the server reports.
        major: The major version the dates are published for.
        end_of_life: The upstream end-of-support date, ISO 8601, or None
            when the project publishes none.
        status: ``supported``, ``ending_soon`` (within
            :data:`WARNING_WINDOW_DAYS`), ``ended`` or ``unknown``.
        message: One sentence for the operator, in English.
    """

    family: str
    version: str | None
    major: str | None
    end_of_life: str | None
    status: SupportStatus
    message: str

    def to_dict(self) -> dict[str, Any]:
        """
        Render the notice as plain data.

        Returns:
            A JSON-serialisable dictionary.
        """
        return asdict(self)


def major_version(family: str, version: str | None) -> str | None:
    """
    Reduce a reported version to the major version dates are published for.

    Args:
        family: The engine family.
        version: The version as the server printed it, such as ``16.4`` or
            ``10.11.8``.

    Returns:
        ``16`` for PostgreSQL 16.4, ``10.11`` for MariaDB 10.11.8, or None
        when the text holds no version.
    """
    if not version:
        return None
    match = re.match(r"(\d+)(?:\.(\d+))?", version.strip())
    if not match:
        return None
    if family in _SINGLE_NUMBER_MAJORS or match.group(2) is None:
        return match.group(1)
    return f"{match.group(1)}.{match.group(2)}"


def support_notice(family: str, version: str | None, *, today: date | None = None) -> SupportNotice:
    """
    Say whether an engine version is still supported upstream.

    Args:
        family: The engine family (see :class:`SupportNotice`).
        version: The version the server reports, or None when unknown.
        today: The date to judge against. Defaults to today.

    Returns:
        The notice.
    """
    today = today or date.today()
    major = major_version(family, version)
    ends = END_OF_LIFE.get(family, {}).get(major or "")
    if ends is None:
        return SupportNotice(
            family=family,
            version=version,
            major=major,
            end_of_life=None,
            status="unknown",
            message=(
                "The upstream project publishes no end-of-support date for this version; "
                "security fixes come from your distribution's package."
            ),
        )

    left = (ends - today).days
    if left < 0:
        status: SupportStatus = "ended"
        message = (
            f"Upstream support for version {major} ended on {ends.isoformat()}. Your "
            "distribution may still ship security fixes for its package until the "
            "distribution itself reaches its end of life; plan the move to a newer release."
        )
    elif left <= WARNING_WINDOW_DAYS:
        status = "ending_soon"
        message = (
            f"Upstream support for version {major} ends on {ends.isoformat()}, in {left} "
            "days. Your distribution may keep patching its package after that; plan the "
            "move to a newer release."
        )
    else:
        status = "supported"
        message = f"Version {major} is supported upstream until {ends.isoformat()}."
    return SupportNotice(
        family=family,
        version=version,
        major=major,
        end_of_life=ends.isoformat(),
        status=status,
        message=message,
    )
