# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the last look at the registries found of each image a container runs.

One table of the Docker area of schema v18, ``docker_image_checks``: a row per image reference as a
container runs it, replaced whole by the next look. Asking a registry is something the operator does on
purpose (the console's "look for new versions", ``noust docker images check``), so what the summary of the
Docker zone and the images page say is what was found then, with when. :class:`ImageVersionStore` is the
only reader and writer.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from noust.core.store import NoustStore, get_store

__all__ = ["CHANGES", "STATES", "ImageVersionStore", "VersionRecord"]

#: What a look can find: the image is the registry's latest, there is a newer one, or it could not be told.
STATES = ("current", "newer", "unknown")

#: What the newer version is to the one that runs; empty when there is none or it cannot be said.
CHANGES = ("", "patch", "minor", "major", "unknown")


@dataclass(frozen=True)
class VersionRecord:
    """
    What a look found of one image.

    Attributes:
        reference: The image as a container runs it, with the tag Docker gives one that has none.
        state: One of :data:`STATES`.
        local_digest: The digest the registry knows the running image by; None when it came from nowhere.
        remote_digest: The digest the registry has for the tag now; None when it could not be asked.
        current_version: The version that runs, as far as its tag or its own labels say.
        latest_version: The version the tag now points at, as far as its own labels say.
        latest_created: When that image was built, as the registry's image says.
        change: One of :data:`CHANGES`.
        floating: The tag is one that moves (``latest``, ``stable``, ``alpine``): no number says what
            it will be.
        method: How the registry was asked: ``imagetools`` (its manifest, nothing downloaded) or ``pull``.
        detail: One sentence saying why it could not be told, or how it was found.
        checked_at: When, ISO 8601 UTC.
    """

    reference: str
    state: str
    local_digest: str | None
    remote_digest: str | None
    current_version: str | None
    latest_version: str | None
    latest_created: str | None
    change: str
    floating: bool
    method: str
    detail: str
    checked_at: str


class ImageVersionStore:
    """
    Read and write what the looks at the registries found.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store."""
        return self._store or get_store()

    def save(self, record: VersionRecord) -> None:
        """
        Keep what a look found, replacing the row of the same image.

        Args:
            record: What was found.

        Raises:
            ValueError: ``state`` or ``change`` is not one the table knows.
        """
        if record.state not in STATES:
            raise ValueError(f"state must be one of {', '.join(STATES)}, not {record.state!r}")
        if record.change not in CHANGES:
            raise ValueError(f"change must be one of {', '.join(CHANGES)}, not {record.change!r}")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT OR REPLACE INTO docker_image_checks (reference, state, local_digest, "
                "remote_digest, current_version, latest_version, latest_created, change, floating, "
                "method, detail, checked_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.reference,
                    record.state,
                    record.local_digest,
                    record.remote_digest,
                    record.current_version,
                    record.latest_version,
                    record.latest_created,
                    record.change,
                    int(record.floating),
                    record.method,
                    record.detail,
                    record.checked_at,
                ),
            )

    def get(self, reference: str) -> VersionRecord | None:
        """
        Args:
            reference: An image as a container runs it.

        Returns:
            What the last look found of it; None when nobody looked.
        """
        row = (
            self.store._get_connection()
            .execute("SELECT * FROM docker_image_checks WHERE reference = ?", (reference,))
            .fetchone()
        )
        return _record(row) if row is not None else None

    def all(self) -> list[VersionRecord]:
        """
        Returns:
            Every record, by reference.
        """
        rows = (
            self.store._get_connection()
            .execute("SELECT * FROM docker_image_checks ORDER BY reference")
            .fetchall()
        )
        return [_record(row) for row in rows]

    def keep_only(self, references: Iterable[str]) -> None:
        """
        Forget the images that no container runs any more.

        Args:
            references: The images in use now; every other record is dropped.
        """
        keep = set(references)
        with self.store._transaction() as cursor:
            cursor.execute("SELECT reference FROM docker_image_checks")
            stale = [row["reference"] for row in cursor.fetchall() if row["reference"] not in keep]
            for reference in stale:
                cursor.execute("DELETE FROM docker_image_checks WHERE reference = ?", (reference,))


def _record(row: object) -> VersionRecord:
    """
    Args:
        row: A row of ``docker_image_checks``.

    Returns:
        The record.
    """
    data = dict(row)  # type: ignore[call-overload]
    return VersionRecord(
        reference=str(data["reference"]),
        state=str(data["state"]),
        local_digest=data["local_digest"],
        remote_digest=data["remote_digest"],
        current_version=data["current_version"],
        latest_version=data["latest_version"],
        latest_created=data["latest_created"],
        change=str(data["change"]),
        floating=bool(data["floating"]),
        method=str(data["method"]),
        detail=str(data["detail"]),
        checked_at=str(data["checked_at"]),
    )
