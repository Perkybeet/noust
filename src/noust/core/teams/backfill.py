# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The data step of the move to teams: what an existing server is given.

:mod:`noust.core.schema_v14` only declares tables and columns and rewrites no
row. This is the other half, run by the store's migration to v14 (and by a
store stamped v14 before its teams existed), inside the migration's own
transaction. It is idempotent, so running it again changes nothing:

1. The default team, project and environment exist (:func:`ensure_default`).
2. Every application, site, database and backup schedule with no owner is given
   one: its application's when it has one, the default's otherwise.
3. When this run created the default team, every account that may join a team
   becomes a member of it with the role that matches its instance role, so an
   upgrade changes nobody's powers (a viewer still reads, an operator still
   deploys, an admin still manages). ``security`` and ``auditor`` accounts stay
   outside teams: they work across all of them (I-10).
"""

from __future__ import annotations

import logging
import sqlite3

from noust.core.teams import guards
from noust.core.teams.model import TEAM_ROLE_FOR_INSTANCE_ROLE, now
from noust.core.teams.ownership import ensure_default, teams_ready

logger = logging.getLogger(__name__)


def run(cursor: sqlite3.Cursor) -> None:
    """
    Give an existing server its default team and an owner to every row.

    Args:
        cursor: A cursor inside the migration's transaction.
    """
    if not teams_ready(cursor):
        # A store whose v14 fragment was replaced (a test of the migration
        # machinery itself) has nothing to fill.
        return
    target, created = ensure_default(cursor)
    pair = (target.project_id, target.environment_id)
    # The database refuses an environment of another project from here on, including
    # the pairs this step writes.
    guards.install(cursor)

    # Applications first, since everything else follows the application it
    # belongs to: a database linked to an app of another team must not be left
    # behind in the default one.
    _fill(cursor, "apps", pair)
    cursor.execute(
        "UPDATE sites SET project_id = (SELECT project_id FROM apps WHERE apps.id = sites.app_id), "
        "environment_id = (SELECT environment_id FROM apps WHERE apps.id = sites.app_id) "
        "WHERE (project_id IS NULL OR environment_id IS NULL) AND app_id IN "
        "(SELECT id FROM apps WHERE project_id IS NOT NULL AND environment_id IS NOT NULL)"
    )
    cursor.execute(
        "UPDATE databases SET "
        "project_id = (SELECT project_id FROM apps WHERE apps.id = databases.app_id), "
        "environment_id = (SELECT environment_id FROM apps WHERE apps.id = databases.app_id) "
        "WHERE (project_id IS NULL OR environment_id IS NULL) AND app_id IN "
        "(SELECT id FROM apps WHERE project_id IS NOT NULL AND environment_id IS NOT NULL)"
    )
    cursor.execute(
        "UPDATE backup_schedules SET "
        "project_id = (SELECT project_id FROM apps WHERE apps.domain = backup_schedules.app_domain), "
        "environment_id = (SELECT environment_id FROM apps "
        "WHERE apps.domain = backup_schedules.app_domain) "
        "WHERE (project_id IS NULL OR environment_id IS NULL) AND app_domain IN "
        "(SELECT domain FROM apps WHERE project_id IS NOT NULL AND environment_id IS NOT NULL)"
    )
    for table in ("sites", "databases", "backup_schedules"):
        _fill(cursor, table, pair)

    if created:
        _enrol_accounts(cursor, target.team_id)


def _enrol_accounts(cursor: sqlite3.Cursor, team_id: int) -> None:
    """
    Make every account that may be a member one of the default team.

    Args:
        cursor: A cursor inside the migration's transaction.
        team_id: The default team.
    """
    stamp = now()
    for instance_role, team_role in TEAM_ROLE_FOR_INSTANCE_ROLE.items():
        cursor.execute(
            "INSERT OR IGNORE INTO team_members (team_id, account_id, role, created_at, created_by) "
            "SELECT ?, id, ?, ?, 'migration' FROM accounts WHERE role = ?",
            (team_id, team_role, stamp, instance_role),
        )


def _fill(cursor: sqlite3.Cursor, table: str, pair: tuple[int, int]) -> None:
    """
    Give the default owner to the rows of a table that have none.

    Args:
        cursor: A cursor inside the migration's transaction.
        table: One of the four tables that carry ownership.
        pair: The default ``(project_id, environment_id)``.
    """
    cursor.execute(
        f"UPDATE {table} SET project_id = ?, environment_id = ? "  # noqa: S608 - fixed names
        "WHERE project_id IS NULL OR environment_id IS NULL",
        pair,
    )
    if cursor.rowcount > 0:
        logger.info("Gave %d %s row(s) to the default project", cursor.rowcount, table)
