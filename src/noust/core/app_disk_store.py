# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What an application may take of the disk, and what it last took, as the store keeps it.

One row per application in ``app_disk`` (``schema_v14.RUNTIME_SQL``, checklist B-63): the limit
Noust holds it to and the last measurement, which a job took because nothing measures a tree in a
request. The two are written independently: setting the limit keeps the measurement and measuring
keeps the limit. A row with neither is removed.

A table of its own, not columns of ``apps``, for the reason ``app_hardening`` is one: the full-row
write of a redeploy cannot put back what an operator changed meanwhile. This is the only module
that reads or writes it (rule 3).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class DiskRecord:
    """
    What is recorded of an application's disk.

    Attributes:
        soft_mb: The limit in mebibytes; None for none.
        usage: The last measurement, as :meth:`noust.managers.app_disk.DiskUsage.to_dict` gave it;
            None before the first.
        measured_at: When it was taken, as an ISO timestamp.
        changed_by: Who set the limit last.
        changed_at: When the row last changed.
        kernel_project: The project id the limit was given to the kernel under; None when it
            was not.
        kernel_mount: The mount point that project quota lives on.
    """

    soft_mb: int | None = None
    usage: dict[str, Any] | None = None
    measured_at: str | None = None
    changed_by: str | None = None
    changed_at: str | None = None
    kernel_project: int | None = None
    kernel_mount: str | None = None


def get_record(domain: str, *, store: NoustStore | None = None) -> DiskRecord | None:
    """
    Read what is recorded of an application's disk.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        Its record, or None for an application with neither a limit nor a measurement.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT d.soft_mb, d.usage, d.measured_at, d.changed_by, d.changed_at, "
            "d.kernel_project, d.kernel_mount "
            "FROM app_disk d JOIN apps a ON a.id = d.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    if row is None:
        return None
    usage: dict[str, Any] | None = None
    if row[1]:
        try:
            loaded = json.loads(row[1])
        except (ValueError, TypeError):
            loaded = None
        usage = loaded if isinstance(loaded, dict) else None
    return DiskRecord(
        soft_mb=int(row[0]) if row[0] is not None else None,
        usage=usage,
        measured_at=row[2],
        changed_by=row[3],
        changed_at=row[4],
        kernel_project=int(row[5]) if row[5] is not None else None,
        kernel_mount=row[6],
    )


def set_limit(
    domain: str,
    soft_mb: int | None,
    *,
    changed_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Set the limit of an application, keeping its last measurement.

    Args:
        domain: The application's domain.
        soft_mb: The limit in mebibytes, already validated by
            :mod:`noust.managers.app_disk`; None removes it.
        changed_by: Who changed it, for the record.
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
            "INSERT INTO app_disk (app_id, soft_mb, changed_by, changed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET soft_mb = excluded.soft_mb, "
            "changed_by = excluded.changed_by, changed_at = excluded.changed_at",
            (found[0], soft_mb, changed_by, datetime.now().isoformat()),
        )
        _drop_if_empty(cursor, found[0])
    return True


def set_usage(
    domain: str,
    usage: dict[str, Any],
    *,
    measured_at: str,
    store: NoustStore | None = None,
) -> bool:
    """
    Record a measurement, keeping the limit.

    Args:
        domain: The application's domain.
        usage: The measurement as plain data.
        measured_at: When it was taken.
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
            "INSERT INTO app_disk (app_id, usage, measured_at, changed_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET usage = excluded.usage, "
            "measured_at = excluded.measured_at",
            (found[0], json.dumps(usage, sort_keys=True), measured_at, datetime.now().isoformat()),
        )
    return True


def set_kernel(
    domain: str,
    project: int | None,
    mount: str | None,
    *,
    store: NoustStore | None = None,
) -> bool:
    """
    Record that the limit was given to the kernel, or that it no longer is.

    Args:
        domain: The application's domain.
        project: The project id the quota is under; None when it was taken off.
        mount: The mount point it lives on.
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
            "INSERT INTO app_disk (app_id, kernel_project, kernel_mount, changed_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "kernel_project = excluded.kernel_project, kernel_mount = excluded.kernel_mount",
            (found[0], project, mount, datetime.now().isoformat()),
        )
        _drop_if_empty(cursor, found[0])
    return True


def _drop_if_empty(cursor: Any, app_id: int) -> None:
    """
    Remove a row that records neither a limit nor a measurement.

    Args:
        cursor: The transaction's cursor.
        app_id: The application's id.
    """
    cursor.execute(
        "DELETE FROM app_disk WHERE app_id = ? AND soft_mb IS NULL AND usage IS NULL "
        "AND kernel_project IS NULL",
        (app_id,),
    )


def forget_record(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget an application's limit and measurement.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a row.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM app_disk WHERE app_id = (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0
