# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where a new row lives, and the one default every server has.

The store sets the project and the environment of every application, site,
database and backup schedule it inserts, so a row never reaches the table
without an owner (the columns are nullable in SQL only because SQLite cannot
add them otherwise). Who owns it is decided here, in this order:

1. The application it belongs to: a site, a database linked to an
   application and a backup schedule live with their application, and so does
   a preview with the application it previews.
2. The target the code asking has bound (:func:`ownership_target`): a deploy
   job queued by a member of a team runs inside the project that member chose.
3. The default: team ``default``, project ``default``, environment
   ``production``, which every server has (:func:`ensure_default`).

Nothing here imports the store: it works on the cursor the store is already
inside, which is what lets it run during a migration.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from noust.core.teams.model import (
    DEFAULT_NAME,
    DEFAULT_SLUG,
    PRODUCTION_NAME,
    PRODUCTION_SLUG,
    Target,
    now,
)

_target: ContextVar[tuple[int, int] | None] = ContextVar("noust_ownership_target", default=None)


@contextmanager
def ownership_target(project_id: int, environment_id: int) -> Iterator[None]:
    """
    Make what the block creates belong to a project and an environment.

    Args:
        project_id: The project.
        environment_id: One of its environments; the database refuses a pair
            that does not match.

    Yields:
        Nothing; the rows created inside take the target.
    """
    token = _target.set((project_id, environment_id))
    try:
        yield
    finally:
        _target.reset(token)


def bound_target() -> tuple[int, int] | None:
    """
    Returns:
        The ``(project_id, environment_id)`` bound by :func:`ownership_target`,
        or None.
    """
    return _target.get()


def teams_ready(cursor: sqlite3.Cursor) -> bool:
    """
    Args:
        cursor: A cursor on the store.

    Returns:
        Whether the teams tables exist: false only while a migration to v14
        has not run, or for a store a test built by hand.
    """
    cursor.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'environments'")
    return cursor.fetchone() is not None


def ensure_default(cursor: sqlite3.Cursor) -> tuple[Target, bool]:
    """
    Find the default team, project and environment, creating what is missing.

    Idempotent. The default cannot be deleted through the managers, so what is
    created here is only ever the first of them: a server that never had one.

    Args:
        cursor: A cursor inside the caller's transaction.

    Returns:
        The default target, and whether this call created the default team
        (the migration gives accounts a membership only then).
    """
    created = False
    cursor.execute("SELECT id FROM teams WHERE slug = ?", (DEFAULT_SLUG,))
    row = cursor.fetchone()
    stamp = now()
    if row is None:
        cursor.execute(
            "INSERT INTO teams (slug, name, created_at) VALUES (?, ?, ?)",
            (DEFAULT_SLUG, DEFAULT_NAME, stamp),
        )
        team_id = int(cursor.lastrowid or 0)
        created = True
    else:
        team_id = int(row[0])
    cursor.execute(
        "SELECT id FROM projects WHERE team_id = ? AND slug = ?", (team_id, DEFAULT_SLUG)
    )
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO projects (team_id, slug, name, created_at) VALUES (?, ?, ?, ?)",
            (team_id, DEFAULT_SLUG, DEFAULT_NAME, stamp),
        )
        project_id = int(cursor.lastrowid or 0)
    else:
        project_id = int(row[0])
    cursor.execute(
        "SELECT id FROM environments WHERE project_id = ? AND slug = ?",
        (project_id, PRODUCTION_SLUG),
    )
    row = cursor.fetchone()
    if row is None:
        cursor.execute(
            "INSERT INTO environments (project_id, slug, name, created_at) VALUES (?, ?, ?, ?)",
            (project_id, PRODUCTION_SLUG, PRODUCTION_NAME, stamp),
        )
        environment_id = int(cursor.lastrowid or 0)
    else:
        environment_id = int(row[0])
    return Target(team_id, project_id, environment_id), created


def _owner_of_app(
    cursor: sqlite3.Cursor, *, app_id: int | None = None, domain: str | None = None
) -> tuple[int, int] | None:
    """
    Args:
        cursor: A cursor on the store.
        app_id: An application id, or None.
        domain: An application's domain, or None.

    Returns:
        The application's ``(project_id, environment_id)`` when it has both.
    """
    if app_id is not None:
        cursor.execute("SELECT project_id, environment_id FROM apps WHERE id = ?", (app_id,))
    elif domain is not None:
        cursor.execute("SELECT project_id, environment_id FROM apps WHERE domain = ?", (domain,))
    else:
        return None
    row = cursor.fetchone()
    if row is None or row[0] is None or row[1] is None:
        return None
    return int(row[0]), int(row[1])


def new_row_owner(
    cursor: sqlite3.Cursor,
    *,
    app_id: int | None = None,
    app_domain: str | None = None,
) -> tuple[int, int]:
    """
    Decide where a row about to be inserted lives.

    Args:
        cursor: A cursor inside the insert's transaction.
        app_id: The application the row belongs to, when it does.
        app_domain: The same, by domain (a backup schedule or a preview).

    Returns:
        ``(project_id, environment_id)``, both always set.
    """
    inherited = _owner_of_app(cursor, app_id=app_id, domain=app_domain)
    if inherited is not None:
        return inherited
    bound = bound_target()
    if bound is not None:
        return bound
    target, _ = ensure_default(cursor)
    return target.project_id, target.environment_id
