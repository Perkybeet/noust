# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Schema v15: what the store holds that carried the old product's name.

A backup destination made without naming a folder stored the default one, and that default
was called after the old product. From 4.0 the default is ``noust-backups``: a destination
that still holds the old default moves to the new one, and keeps the old one as
``legacy_path`` so what is already there stays listed, restorable and subject to its
retention until it has expired (see :mod:`noust.managers.backup_destinations`).

The step is a plain function over a cursor, idempotent, and runs inside the transaction of the
climb to v15, so a crash leaves the store at v14 and the step to run again.
"""

from __future__ import annotations

import json
import sqlite3

from noust.core import legacy_names

__all__ = ["VERSION", "apply_v15"]

#: The schema version this fragment brings the store to.
VERSION = 15


def apply_v15(cursor: sqlite3.Cursor) -> None:
    """
    Move the destinations that hold the old default folder to the new one.

    Args:
        cursor: Cursor of the transaction the climb runs in.
    """
    try:
        rows = cursor.execute("SELECT name, settings FROM backup_destinations").fetchall()
    except sqlite3.OperationalError:
        # A store without the table (a fragment of a test) has nothing to move.
        return
    for row in rows:
        name, text = row[0], row[1]
        try:
            settings = json.loads(text or "{}")
        except ValueError:
            continue
        if not isinstance(settings, dict) or settings.get("path") != legacy_names.BACKUP_FOLDER:
            continue
        settings["path"] = legacy_names.CURRENT_BACKUP_FOLDER
        settings.setdefault("legacy_path", legacy_names.BACKUP_FOLDER)
        cursor.execute(
            "UPDATE backup_destinations SET settings = ? WHERE name = ?",
            (json.dumps(settings), name),
        )
