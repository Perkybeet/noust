# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When each operating system stops receiving security updates.

A table shipped with Noust, not a lookup: asking a website on every visit to the
page would put a network call in the way of a page that has to work when the
network does not, and would tell that website which distribution and version
every Noust runs. The table is reviewed at every release, from the
distributions' own lifecycle pages and https://endoflife.date, and every row
says which date it is (standard support, not the paid extended kind).

A distribution that states its own end (``SUPPORT_END`` in ``/etc/os-release``,
which Fedora, RHEL and openSUSE now set) is believed before this table: it is the
distribution's word about itself.

The status has a shape for a server that has already passed its date, because
that is the case the page exists to shout about: nothing on it gets a security
fix any more, and a fix for every other check on the page is pointless if the
system underneath is unpatched.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

from noust.managers.server.host import OsRelease

#: When this table was last checked against the distributions' pages.
REVIEWED = "2026-09-29"

#: A system this close to its end date is a warning, in days: six months.
WARN_DAYS = 183

#: ``(os-release ID, VERSION_ID prefix) -> last day of standard support``.
#: A version matches by the longest prefix: ``9`` covers RHEL ``9.5``.
END_OF_LIFE: dict[tuple[str, str], str] = {
    # Debian: the end of the LTS period, which is when security fixes stop.
    ("debian", "10"): "2024-06-30",
    ("debian", "11"): "2026-08-31",
    ("debian", "12"): "2028-06-30",
    ("debian", "13"): "2030-06-30",
    # Ubuntu: standard security maintenance of the LTS releases.
    ("ubuntu", "18.04"): "2023-05-31",
    ("ubuntu", "20.04"): "2025-05-31",
    ("ubuntu", "22.04"): "2027-06-01",
    ("ubuntu", "24.04"): "2029-05-31",
    # Red Hat Enterprise Linux and the rebuilds that follow its lifecycle.
    ("rhel", "8"): "2029-05-31",
    ("rhel", "9"): "2032-05-31",
    ("rhel", "10"): "2035-05-31",
    ("almalinux", "8"): "2029-05-31",
    ("almalinux", "9"): "2032-05-31",
    ("almalinux", "10"): "2035-05-31",
    ("rocky", "8"): "2029-05-31",
    ("rocky", "9"): "2032-05-31",
    ("rocky", "10"): "2035-05-31",
    ("centos", "9"): "2027-05-31",
    # Fedora: about thirteen months from release.
    ("fedora", "39"): "2024-11-12",
    ("fedora", "40"): "2025-05-13",
    ("fedora", "41"): "2025-12-15",
    ("fedora", "42"): "2026-05-27",
    ("fedora", "43"): "2026-12-09",
    # openSUSE Leap.
    ("opensuse-leap", "15.5"): "2024-12-31",
    ("opensuse-leap", "15.6"): "2026-04-30",
    ("opensuse-leap", "16.0"): "2027-10-31",
    # Amazon Linux.
    ("amzn", "2023"): "2029-06-30",
    ("amzn", "2"): "2026-06-30",
}

#: Distributions that have no end: they move forward until they are replaced.
ROLLING = frozenset({"opensuse-tumbleweed", "arch", "gentoo"})


@dataclass(frozen=True)
class EolStatus:
    """
    Where an operating system is in its support.

    Attributes:
        status: ``ok``, ``warn`` (ends within six months), ``expired``,
            ``rolling`` or ``unknown``.
        end_date: The last day of security updates, ISO date, when known.
        days_left: Days until it, negative once past; None when unknown.
        source: ``os-release`` when the distribution says it, ``table`` when it
            comes from Noust's table, ``none`` otherwise.
    """

    status: str
    end_date: str | None = None
    days_left: int | None = None
    source: str = "none"

    def to_dict(self) -> dict[str, str | int | None]:
        """
        Render the status as JSON-serialisable data.

        Returns:
            Every field, by name.
        """
        return asdict(self)


def lookup(os_id: str, version_id: str) -> str | None:
    """
    Find the end date of a version in the table.

    Args:
        os_id: ``ID`` from ``/etc/os-release``.
        version_id: ``VERSION_ID``.

    Returns:
        The ISO date of the longest matching row, or None.
    """
    best: tuple[int, str] | None = None
    for (row_id, prefix), end in END_OF_LIFE.items():
        matches = version_id == prefix or version_id.startswith(prefix + ".")
        if row_id == os_id and matches and (best is None or len(prefix) > best[0]):
            best = (len(prefix), end)
    return best[1] if best else None


def status_of(os_release: OsRelease, *, today: date | None = None) -> EolStatus:
    """
    Say whether an operating system is still supported.

    Args:
        os_release: The system's identity.
        today: The current date; injectable for tests.

    Returns:
        The status.
    """
    now = today or date.today()
    if os_release.id in ROLLING:
        return EolStatus("rolling", source="table")

    source = "os-release"
    end = os_release.support_end or None
    if end is None:
        end = lookup(os_release.id, os_release.version_id)
        source = "table"
    if end is None:
        return EolStatus("unknown")
    try:
        end_date = date.fromisoformat(end)
    except ValueError:
        return EolStatus("unknown")

    left = (end_date - now).days
    status = "expired" if left < 0 else "warn" if left <= WARN_DAYS else "ok"
    return EolStatus(status, end, left, source)
