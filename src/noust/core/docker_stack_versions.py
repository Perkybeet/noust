# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The text a stack's Compose document held, kept in the store.

One table of the Docker area of schema v18, ``docker_stack_versions``, kept like the history of a site's
file (:mod:`noust.core.site_history_store`): a version is what the document said at a moment, found
when Noust first looked at it, saved from the editor, put back after the stack would not come up, or
restored from an earlier version. A stack keeps its latest :data:`KEEP` versions, saving the text the
latest version already holds adds nothing, and a number is never reused. Each version says who saved it
and why, which is what makes a history something to read before going back.

:class:`StackVersionStore` is the only reader and writer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from noust.core.store import NoustStore, get_store

__all__ = ["KEEP", "REASON_LENGTH", "SOURCES", "StackVersion", "StackVersionStore", "sha256_of"]

#: How many versions of one stack are kept.
KEEP = 30

#: How a version came to be.
SOURCES = ("found", "edit", "revert", "restore")

#: The longest reason kept: a history is read a line at a time.
REASON_LENGTH = 200


@dataclass(frozen=True)
class StackVersion:
    """
    One version of a stack's document.

    Attributes:
        id: The row.
        domain: The stack's application.
        version: Its number for this stack, from 1, never reused.
        sha256: The hash of the text.
        size: The text's length in bytes.
        source: One of :data:`SOURCES`.
        actor: Who saved it, an actor label; None for what Noust did on its own.
        reason: Why, in a line.
        created_at: When, ISO 8601 UTC.
        text: The document; empty in a listing.
    """

    id: int
    domain: str
    version: int
    sha256: str
    size: int
    source: str
    actor: str | None
    reason: str
    created_at: str
    text: str = ""

    def to_dict(self) -> dict[str, Any]:
        """
        Returns:
            The version without its text, for a listing.
        """
        return {
            "version": self.version,
            "sha256": self.sha256,
            "size": self.size,
            "source": self.source,
            "actor": self.actor,
            "reason": self.reason,
            "created_at": self.created_at,
        }


def sha256_of(text: str) -> str:
    """
    Args:
        text: A document.

    Returns:
        Its hash, as the history compares versions.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _one_line(reason: str) -> str:
    """
    Args:
        reason: What a person wrote.

    Returns:
        Its first line, cut to :data:`REASON_LENGTH`.
    """
    first = reason.strip().splitlines()[0] if reason.strip() else ""
    return first.strip()[:REASON_LENGTH]


class StackVersionStore:
    """
    Read and write the versions of stacks' documents.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def latest(self, domain: str) -> StackVersion | None:
        """
        Args:
            domain: The stack's application.

        Returns:
            Its newest version, with its text; None when none was kept.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM docker_stack_versions WHERE domain = ? ORDER BY version DESC LIMIT 1",
                (domain,),
            )
            .fetchone()
        )
        return _version(row, with_text=True) if row is not None else None

    def add(
        self, domain: str, text: str, *, source: str, actor: str | None, reason: str
    ) -> StackVersion | None:
        """
        Keep a version, unless the newest already says the same.

        Args:
            domain: The stack's application.
            text: The document.
            source: How it came to be (one of :data:`SOURCES`).
            actor: Who saved it.
            reason: Why, in a line; what follows the first line is dropped.

        Returns:
            The new version, or None when the newest holds the same text.

        Raises:
            ValueError: ``source`` is not one the history knows.
        """
        if source not in SOURCES:
            raise ValueError(f"source must be one of {', '.join(SOURCES)}, not {source!r}")
        digest = sha256_of(text)
        said = _one_line(reason)
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT version, sha256 FROM docker_stack_versions WHERE domain = ? "
                "ORDER BY version DESC LIMIT 1",
                (domain,),
            )
            newest = cursor.fetchone()
            if newest is not None and newest["sha256"] == digest:
                return None
            number = int(newest["version"]) + 1 if newest is not None else 1
            now = datetime.now(timezone.utc).isoformat()
            size = len(text.encode("utf-8"))
            cursor.execute(
                "INSERT INTO docker_stack_versions "
                "(domain, version, sha256, size, source, actor, reason, created_at, text) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (domain, number, digest, size, source, actor, said, now, text),
            )
            row_id = int(cursor.lastrowid or 0)
            cursor.execute(
                "DELETE FROM docker_stack_versions WHERE domain = ? AND version <= ?",
                (domain, number - KEEP),
            )
        return StackVersion(row_id, domain, number, digest, size, source, actor, said, now, text)

    def versions(self, domain: str) -> list[StackVersion]:
        """
        Args:
            domain: The stack's application.

        Returns:
            Its versions, newest first, without their text.
        """
        rows = (
            self.store._get_connection()
            .execute(
                "SELECT id, domain, version, sha256, size, source, actor, reason, created_at "
                "FROM docker_stack_versions WHERE domain = ? ORDER BY version DESC",
                (domain,),
            )
            .fetchall()
        )
        return [_version(row, with_text=False) for row in rows]

    def get(self, domain: str, version: int) -> StackVersion | None:
        """
        Args:
            domain: The stack's application.
            version: The number.

        Returns:
            That version with its text; None when it was never kept or has been pruned.
        """
        row = (
            self.store._get_connection()
            .execute(
                "SELECT * FROM docker_stack_versions WHERE domain = ? AND version = ?",
                (domain, version),
            )
            .fetchone()
        )
        return _version(row, with_text=True) if row is not None else None

    def forget(self, domain: str) -> None:
        """
        Drop every version of a stack that is gone.

        Args:
            domain: The stack's application.
        """
        with self.store._transaction() as cursor:
            cursor.execute("DELETE FROM docker_stack_versions WHERE domain = ?", (domain,))


def _version(row: object, *, with_text: bool) -> StackVersion:
    """
    Args:
        row: A row of the table.
        with_text: Whether the row carries the text.

    Returns:
        The version.
    """
    data = dict(row)  # type: ignore[call-overload]
    return StackVersion(
        id=int(data["id"]),
        domain=str(data["domain"]),
        version=int(data["version"]),
        sha256=str(data["sha256"]),
        size=int(data["size"]),
        source=str(data["source"]),
        actor=data["actor"],
        reason=str(data["reason"]),
        created_at=str(data["created_at"]),
        text=str(data["text"]) if with_text else "",
    )
