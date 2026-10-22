# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The system packages an application needs, as the store keeps them.

One row per application in the ``app_packages`` table (``schema_v14.SOURCES_SQL``): the names
of the distribution packages its build or its process uses (a image library, a font, a
converter), set by the operator and installed by Noust before the next build
(:mod:`noust.managers.app_packages`). A table of its own, not a key of the build block, because
a build block may come from the repository and these names make root run the package manager:
nothing a repository says reaches this table. It is also not a column of ``apps``, so a deploy's
full-row write cannot put back a list an operator changed while it ran. This is the only module
that reads or writes the table (rule 3).
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime

from noust.core.store import NoustStore, get_store


@dataclass(frozen=True)
class PackageRecord:
    """
    One application's declared packages.

    Attributes:
        domain: The application.
        packages: The package names, in the order they were declared.
        updated_at: When the list last changed, ISO 8601.
        updated_by: Who changed it, for the record.
    """

    domain: str
    packages: tuple[str, ...]
    updated_at: str
    updated_by: str | None


def get(domain: str, *, store: NoustStore | None = None) -> PackageRecord | None:
    """
    Read an application's declared packages.

    Args:
        domain: The application's domain.
        store: The store; the process-wide one when None.

    Returns:
        The record, or None when the application declares none.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT a.domain, p.packages, p.updated_at, p.updated_by FROM app_packages p "
            "JOIN apps a ON a.id = p.app_id WHERE a.domain = ?",
            (domain,),
        )
        row = cursor.fetchone()
    return _record(row) if row is not None else None


def list_all(*, store: NoustStore | None = None) -> list[PackageRecord]:
    """
    List every application that declares packages.

    Args:
        store: The store; the process-wide one when None.

    Returns:
        The records, by domain.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute(
            "SELECT a.domain, p.packages, p.updated_at, p.updated_by FROM app_packages p "
            "JOIN apps a ON a.id = p.app_id ORDER BY a.domain"
        )
        return [_record(row) for row in cursor.fetchall()]


def put(
    domain: str,
    packages: tuple[str, ...],
    *,
    updated_by: str | None,
    store: NoustStore | None = None,
) -> bool:
    """
    Replace an application's declared packages; an empty list forgets them.

    The names are validated by the caller (:func:`noust.managers.app_packages.validate_packages`):
    this table is where they are kept, not where they are judged.

    Args:
        domain: The application's domain.
        packages: The names.
        updated_by: Who changed the list.
        store: The store; the process-wide one when None.

    Returns:
        True when the application exists.
    """
    with (store or get_store())._transaction() as cursor:
        cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
        row = cursor.fetchone()
        if row is None:
            return False
        if not packages:
            cursor.execute("DELETE FROM app_packages WHERE app_id = ?", (row[0],))
            return True
        cursor.execute(
            "INSERT INTO app_packages (app_id, packages, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(app_id) DO UPDATE SET "
            "packages = excluded.packages, updated_at = excluded.updated_at, "
            "updated_by = excluded.updated_by",
            (row[0], json.dumps(list(packages)), datetime.now().isoformat(), updated_by),
        )
    return True


def _record(row: sqlite3.Row) -> PackageRecord:
    """
    Args:
        row: A row of the joined query.

    Returns:
        The record; a list that cannot be read counts as none, so a damaged row installs nothing.
    """
    try:
        parsed = json.loads(row["packages"])
    except ValueError:
        parsed = []
    names = tuple(str(item) for item in parsed) if isinstance(parsed, list) else ()
    return PackageRecord(
        domain=str(row["domain"]),
        packages=names,
        updated_at=str(row["updated_at"]),
        updated_by=row["updated_by"],
    )
