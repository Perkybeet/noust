# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the database itself refuses about where a row lives.

A row carries a project and an environment, and the environment must be one of
that project. The managers above check it, but the store is the chokepoint for
what a row may say about itself: these triggers refuse the pair whoever writes
it (a manager that forgot, a script, a restore), the way the columns' RESTRICT
references refuse deleting a project that still holds something.

They are installed by the data step (:mod:`noust.core.teams.backfill`) and not
declared in the v14 schema fragment, whose statements are run one by one split
at their semicolons: a trigger's body has semicolons of its own.
"""

from __future__ import annotations

import sqlite3

from noust.core.teams.model import OWNED_TABLES

#: The refusal every trigger raises.
MESSAGE = "the environment does not belong to the project"

_TRIGGER = """
CREATE TRIGGER IF NOT EXISTS trg_{table}_environment_{event}
BEFORE {action} ON {table}
WHEN NEW.environment_id IS NOT NULL
    AND NEW.project_id IS NOT (SELECT project_id FROM environments WHERE id = NEW.environment_id)
BEGIN
    SELECT RAISE(ABORT, '{message}');
END
"""


def install(cursor: sqlite3.Cursor) -> None:
    """
    Create the triggers that keep an environment inside its project.

    Idempotent. One per owned table for an insert and one for a change of
    either column.

    Args:
        cursor: A cursor inside the migration's transaction.
    """
    for table in OWNED_TABLES:
        for event, action in (
            ("insert", "INSERT"),
            ("update", "UPDATE OF project_id, environment_id"),
        ):
            cursor.execute(
                _TRIGGER.format(table=table, event=event, action=action, message=MESSAGE)
            )
