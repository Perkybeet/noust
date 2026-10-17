# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The text a site's configuration file held, kept in the store.

One table of the networking fragment of schema v14, ``site_config_versions``. A version is
what the file said at a moment: rendered by a deployer, saved from the editor, put back from an
earlier version, or found on disk different from anything Noust wrote (a hand edit about to be
overwritten by the next deploy). A site keeps its latest :data:`KEEP` versions; saving the
text the latest version already holds adds nothing.

:class:`SiteHistoryStore` is the only reader and writer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from noust.core.store import NoustStore, get_store

#: How many versions of one site are kept.
KEEP = 30

#: How a version came to be.
SOURCES = ("generated", "edit", "restore", "found")


@dataclass(frozen=True)
class SiteVersion:
    """
    One version of a site's file.

    Attributes:
        id: The row.
        domain: The name the site is addressed by.
        webserver: ``nginx`` or ``apache``.
        version: Its number for this site, from 1, never reused.
        sha256: The hash of the text.
        size: The text's length in bytes.
        source: ``generated``, ``edit``, ``restore`` or ``found``.
        actor: Who saved it, an actor label; None for a deployer's.
        created_at: When, ISO 8601 UTC.
        text: The file's text; empty in a listing.
    """

    id: int
    domain: str
    webserver: str
    version: int
    sha256: str
    size: int
    source: str
    actor: str | None
    created_at: str
    text: str = ""


def sha256_of(text: str) -> str:
    """
    Args:
        text: A file's text.

    Returns:
        Its hash, as the history compares versions.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SiteHistoryStore:
    """
    Read and write the versions of sites' files.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def latest(self, domain: str, webserver: str) -> SiteVersion | None:
        """
        Args:
            domain: The site's name.
            webserver: ``nginx`` or ``apache``.

        Returns:
            Its newest version, with its text; None when none was kept.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM site_config_versions WHERE domain = ? AND webserver = ? "
                "ORDER BY version DESC LIMIT 1",
                (domain, webserver),
            )
            .fetchone()
        )
        return _version(row, with_text=True) if row is not None else None

    def add(
        self, domain: str, webserver: str, text: str, *, source: str, actor: str | None
    ) -> SiteVersion | None:
        """
        Keep a version, unless the newest already says the same.

        Args:
            domain: The site's name.
            webserver: ``nginx`` or ``apache``.
            text: The file's text.
            source: How it came to be (one of :data:`SOURCES`).
            actor: Who saved it.

        Returns:
            The new version, or None when the newest holds the same text.
        """
        digest = sha256_of(text)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT version, sha256 FROM site_config_versions "
                "WHERE domain = ? AND webserver = ? ORDER BY version DESC LIMIT 1",
                (domain, webserver),
            )
            newest = cursor.fetchone()
            if newest is not None and newest["sha256"] == digest:
                return None
            number = int(newest["version"]) + 1 if newest is not None else 1
            now = datetime.now(timezone.utc).isoformat()
            cursor.execute(
                "INSERT INTO site_config_versions "
                "(domain, webserver, version, sha256, size, source, actor, created_at, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    domain,
                    webserver,
                    number,
                    digest,
                    len(text.encode("utf-8")),
                    source,
                    actor,
                    now,
                    text,
                ),
            )
            row_id = int(cursor.lastrowid or 0)
            cursor.execute(
                "DELETE FROM site_config_versions WHERE domain = ? AND webserver = ? AND version <= ?",
                (domain, webserver, number - KEEP),
            )
        return SiteVersion(
            id=row_id,
            domain=domain,
            webserver=webserver,
            version=number,
            sha256=digest,
            size=len(text.encode("utf-8")),
            source=source,
            actor=actor,
            created_at=now,
            text=text,
        )

    def versions(self, domain: str, webserver: str) -> list[SiteVersion]:
        """
        Args:
            domain: The site's name.
            webserver: ``nginx`` or ``apache``.

        Returns:
            Its versions, newest first, without their text.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT id, domain, webserver, version, sha256, size, source, actor, created_at "
                "FROM site_config_versions WHERE domain = ? AND webserver = ? "
                "ORDER BY version DESC",
                (domain, webserver),
            )
            .fetchall()
        )
        return [_version(row, with_text=False) for row in rows]

    def get(self, domain: str, webserver: str, version: int) -> SiteVersion | None:
        """
        Args:
            domain: The site's name.
            webserver: ``nginx`` or ``apache``.
            version: The number.

        Returns:
            That version with its text; None when it was never kept or has been pruned.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM site_config_versions "
                "WHERE domain = ? AND webserver = ? AND version = ?",
                (domain, webserver, version),
            )
            .fetchone()
        )
        return _version(row, with_text=True) if row is not None else None

    def newest_by_site(self, webserver: str) -> dict[str, tuple[str, str]]:
        """
        Read the newest version of every site in one query.

        Args:
            webserver: ``nginx`` or ``apache``.

        Returns:
            Site name to ``(sha256, source)`` of its newest version.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT v.domain, v.sha256, v.source FROM site_config_versions v "
                "JOIN (SELECT domain, MAX(version) AS version FROM site_config_versions "
                "      WHERE webserver = ? GROUP BY domain) m "
                "ON m.domain = v.domain AND m.version = v.version WHERE v.webserver = ?",
                (webserver, webserver),
            )
            .fetchall()
        )
        return {str(row["domain"]): (str(row["sha256"]), str(row["source"])) for row in rows}

    def forget(self, domain: str) -> None:
        """
        Drop every version of a site that is gone.

        Args:
            domain: The site's name.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM site_config_versions WHERE domain = ?", (domain,))


def _version(row: object, *, with_text: bool) -> SiteVersion:
    """
    Args:
        row: A row of the table.
        with_text: Whether the row carries the text.

    Returns:
        The version.
    """
    data = dict(row)  # type: ignore[call-overload]
    return SiteVersion(
        id=int(data["id"]),
        domain=str(data["domain"]),
        webserver=str(data["webserver"]),
        version=int(data["version"]),
        sha256=str(data["sha256"]),
        size=int(data["size"]),
        source=str(data["source"]),
        actor=data["actor"],
        created_at=str(data["created_at"]),
        text=str(data["text"]) if with_text else "",
    )
