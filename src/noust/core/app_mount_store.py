# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files and directories an application is given beyond its repository, as the store keeps them.

One row per mount in ``app_mounts`` (``schema_v14.RUNTIME_SQL``): a ``file`` is a
configuration file Noust keeps for the application (its content is the file on
disk, never a column), a ``path`` is a directory or file of the server bound into
a container application. A table of its own, not columns of ``apps``, so the
full-row write of a redeploy cannot touch it. This is the only module that reads or
writes the table (rule 3); what a mount does is :mod:`noust.managers.app_mounts`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from noust.core.store import NoustStore, get_store

KIND_FILE = "file"
KIND_PATH = "path"

#: The default mode of a file mount: only the application's account reads it.
DEFAULT_MODE = 0o600


@dataclass(frozen=True)
class MountRecord:
    """
    One mount of an application.

    Attributes:
        id: Its identifier.
        kind: ``file`` or ``path``.
        target: Where the application sees it: a path inside the application, or
            inside the container of a container application.
        source: For a ``path``, the path on the server; None for a ``file``.
        mode: For a ``file``, its permissions.
        read_only: For a mount into a container, whether it is mounted read-only.
        updated_by: Who set it last.
        updated_at: When, as an ISO timestamp.
    """

    id: int
    kind: str
    target: str
    source: str | None = None
    mode: int = DEFAULT_MODE
    read_only: bool = True
    updated_by: str | None = None
    updated_at: str | None = None


def list_mounts(domain: str, *, store: NoustStore | None = None) -> list[MountRecord]:
    """
    List an application's mounts.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its mounts by target.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT m.id, m.kind, m.target, m.source, m.mode, m.read_only, m.updated_by, "
            "m.updated_at FROM app_mounts m JOIN apps a ON a.id = m.app_id "
            "WHERE a.domain = ? ORDER BY m.target",
            (domain,),
        )
        return [
            MountRecord(
                id=row[0],
                kind=row[1],
                target=row[2],
                source=row[3],
                mode=row[4],
                read_only=bool(row[5]),
                updated_by=row[6],
                updated_at=row[7],
            )
            for row in cursor.fetchall()
        ]


def get_mount(domain: str, target: str, *, store: NoustStore | None = None) -> MountRecord | None:
    """
    Read one of an application's mounts.

    Args:
        domain: The application's domain.
        target: Where it is mounted.
        store: The store; the process-wide one when None.

    Returns:
        The mount, or None.
    """
    return next(
        (record for record in list_mounts(domain, store=store) if record.target == target), None
    )


def put_mount(
    domain: str,
    record: MountRecord,
    *,
    updated_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Store a mount, replacing the one at the same target.

    Args:
        domain: The application's domain.
        record: The mount; its ``id`` is ignored.
        updated_by: Who changed it, for the record.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    target = store or get_store()
    with target._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        found = cursor.fetchone()
        if found is None:
            return False
        cursor.execute(
            "INSERT INTO app_mounts (app_id, kind, target, source, mode, read_only, updated_by, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id, target) DO UPDATE SET kind = excluded.kind, "
            "source = excluded.source, mode = excluded.mode, read_only = excluded.read_only, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (
                found[0],
                record.kind,
                record.target,
                record.source,
                record.mode,
                1 if record.read_only else 0,
                updated_by,
                datetime.now().isoformat(),
            ),
        )
    return True


def delete_mount(domain: str, target: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget a mount.

    Args:
        domain: The application's domain.
        target: Where it is mounted.
        store: The store; the process-wide one when None.

    Returns:
        True when there was one.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_mounts WHERE target = ? AND "
            "app_id = (SELECT id FROM apps WHERE domain = ?)",
            (target, domain),
        )
        return cursor.rowcount > 0
