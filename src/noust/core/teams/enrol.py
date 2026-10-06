# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an account's team membership follows from its instance role.

An account created the way every account was created before teams (``noust
user create``, an invitation without a team) joins the default team with the
team role that matches its instance role: it sees what the default holds,
which on a server nobody organised in teams is everything, so nothing about
how such a server is used changes. A person invited to a team joins that team
only.

Both functions work on the cursor of the transaction that writes the account,
so the account and its membership exist together or not at all.
"""

from __future__ import annotations

import sqlite3

from noust.core.teams.model import INSTANCE_ONLY_ROLES, TEAM_ROLE_FOR_INSTANCE_ROLE, now
from noust.core.teams.ownership import ensure_default, teams_ready


def enrol_in_default(cursor: sqlite3.Cursor, account_id: int, instance_role: str) -> None:
    """
    Make an account a member of the default team, when its role may be.

    Args:
        cursor: A cursor inside the transaction that writes the account.
        account_id: The account.
        instance_role: Its instance role. A security or auditor account joins
            no team; neither does an account in a store without teams.
    """
    team_role = TEAM_ROLE_FOR_INSTANCE_ROLE.get(instance_role)
    if team_role is None or not teams_ready(cursor):
        return
    target, _ = ensure_default(cursor)
    cursor.execute(
        "INSERT OR IGNORE INTO team_members (team_id, account_id, role, created_at, created_by) "
        "VALUES (?, ?, ?, ?, 'account')",
        (target.team_id, account_id, team_role, now()),
    )


def follow_instance_role(cursor: sqlite3.Cursor, account_id: int, instance_role: str) -> None:
    """
    Keep an account's memberships consistent with a change of its instance role.

    A role that never joins a team leaves every team (I-10); an account that
    comes out of one (a security officer made an operator) and belongs to no
    team joins the default, as a new account does, so it is not left with
    nothing to see.

    Args:
        cursor: A cursor inside the transaction that writes the role.
        account_id: The account.
        instance_role: Its new instance role.
    """
    if not teams_ready(cursor):
        return
    if instance_role in INSTANCE_ONLY_ROLES:
        cursor.execute("DELETE FROM team_members WHERE account_id = ?", (account_id,))
        cursor.execute("DELETE FROM account_active_team WHERE account_id = ?", (account_id,))
        return
    cursor.execute("SELECT 1 FROM team_members WHERE account_id = ?", (account_id,))
    if cursor.fetchone() is None:
        enrol_in_default(cursor, account_id, instance_role)
