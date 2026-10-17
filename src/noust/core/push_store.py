# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which applications deploy from ``git push``, as the store keeps it.

One row per application in the ``push_repos`` table (``schema_v14.SOURCES_SQL``): the
branch a push deploys, and the source and branch the application had before, so that turning
push off puts it back where it was. A table of its own, not columns of ``apps``, so a deploy's
full-row write of ``apps`` cannot put back what an operator changed while it ran. This is the
only module that reads or writes the table (rule 3).

The repositories themselves, the hook and the keys of who may push are on disk
(:mod:`noust.pushdeploy`); this is the record of which applications have them.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class PushRecord:
    """
    One application's push deployment.

    Attributes:
        domain: The application.
        branch: The branch a push to the repository deploys.
        previous_source: The source the application had before push was turned on; None when
            it had none.
        previous_branch: The branch it was pinned to then; None when it was not.
        enabled_at: When push was turned on, ISO 8601.
        enabled_by: Who did, for the record.
    """

    domain: str
    branch: str
    previous_source: str | None
    previous_branch: str | None
    enabled_at: str
    enabled_by: str | None


def get(domain: str, *, store: NoustStore | None = None) -> PushRecord | None:
    """
    Read an application's push record.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The record, or None when the application does not deploy from push.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT a.domain, p.branch, p.previous_source, p.previous_branch, "
            "p.enabled_at, p.enabled_by FROM push_repos p JOIN apps a ON a.id = p.app_id "
            "WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    return _record(row) if row is not None else None


def list_all(*, store: NoustStore | None = None) -> list[PushRecord]:
    """
    List every application that deploys from push.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        The records, by domain.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT a.domain, p.branch, p.previous_source, p.previous_branch, "
            "p.enabled_at, p.enabled_by FROM push_repos p JOIN apps a ON a.id = p.app_id "
            "ORDER BY a.domain"
        )
        return [_record(row) for row in cursor.fetchall()]


def put(
    domain: str,
    *,
    branch: str,
    previous_source: str | None,
    previous_branch: str | None,
    enabled_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Record that an application deploys from push, or change the branch it deploys.

    The source and branch it had before are kept from the first time: turning push on again
    must not make ``push:`` the thing to go back to.

    Args:
        domain: The application's domain.
        branch: The branch a push deploys.
        previous_source: The application's source before push.
        previous_branch: Its pinned branch before push.
        enabled_by: Who did.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    now = datetime.now().isoformat()
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            return False
        cursor.execute(
            "INSERT INTO push_repos (app_id, branch, previous_source, previous_branch, "
            "enabled_at, enabled_by) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(app_id) DO UPDATE SET branch = excluded.branch",
            (row[0], branch, previous_source, previous_branch, now, enabled_by),
        )
    return True


def remove(domain: str, *, store: NoustStore | None = None) -> bool:
    """
    Forget that an application deploys from push.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        True when there was a record.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "DELETE FROM push_repos WHERE app_id IN (SELECT id FROM apps WHERE domain = ?)",
            (domain,),
        )
        return cursor.rowcount > 0


def _record(row: sqlite3.Row) -> PushRecord:
    """
    Args:
        row: A row of the joined query.

    Returns:
        The record.
    """
    return PushRecord(
        domain=str(row["domain"]),
        branch=str(row["branch"]),
        previous_source=row["previous_source"],
        previous_branch=row["previous_branch"],
        enabled_at=str(row["enabled_at"]),
        enabled_by=row["enabled_by"],
    )
