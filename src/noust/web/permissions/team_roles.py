# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What each team role holds, and how a principal's permissions meet a team.

The four team roles map onto the permission sets of the instance roles, cut
down to what a team can own. A team is a boundary of permissions and of
organisation, not of execution: applications still share one kernel. So a team
role never holds what reaches the machine itself (``server.*``,
``root_equivalent``, ``settings.*``, ``fleet.*``) - a team admin who could
write a raw nginx directive or a systemd unit would be root, and everything
below this module would be decoration:

- ``viewer`` reads the team's applications, databases and backups.
- ``operator`` also runs what exists: restart, deploy, roll back, back up.
- ``admin`` also creates, configures and deletes inside the team, reads its
  secrets and manages its databases and backups.
- ``owner`` also governs the team: its members, projects and environments.

A principal holds, on a resource of a team, its instance role's permissions
and what its role in that team adds (``admin`` is unaffected: it sees every
team with its own permissions, which a team role can only equal). The instance
role is the floor, the team role raises it inside the team.

``security`` and ``auditor`` are instance-only roles (I-10) and have no team
role at all.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from noust.core.store import StoreError
from noust.core.teams import Scope, TeamStore
from noust.core.teams.model import (
    INSTANCE_WIDE_ROLES,
    TEAM_ROLE_ADMIN,
    TEAM_ROLE_OPERATOR,
    TEAM_ROLE_OWNER,
    TEAM_ROLE_VIEWER,
)
from noust.web.permissions import Permission
from noust.web.permissions.principal import permissions_of
from noust.web.permissions.roles import ADMIN, OPERATOR, VIEWER

logger = logging.getLogger(__name__)

P = Permission

#: What a team can own: the permissions that are about applications,
#: databases and backups, and none of those that are about the server.
TENANT_PERMISSIONS: frozenset[str] = frozenset(
    {
        P.SELF,
        P.APPS_READ,
        P.APPS_OPERATE,
        P.APPS_DEPLOY,
        P.APPS_MANAGE,
        P.APPS_TERMINAL,
        P.SECRETS_REVEAL,
        P.DATABASES_READ,
        P.DATABASES_WRITE,
        P.DATABASES_MANAGE,
        P.BACKUPS_READ,
        P.BACKUPS_RUN,
        P.BACKUPS_MANAGE,
        P.TEAMS_READ,
    }
)

TEAM_VIEWER: frozenset[str] = VIEWER & TENANT_PERMISSIONS
TEAM_OPERATOR: frozenset[str] = OPERATOR & TENANT_PERMISSIONS
TEAM_ADMIN: frozenset[str] = ADMIN & TENANT_PERMISSIONS
TEAM_OWNER: frozenset[str] = TEAM_ADMIN | {P.TEAMS_MANAGE}

#: Team role to what it holds in its team.
TEAM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    TEAM_ROLE_VIEWER: TEAM_VIEWER,
    TEAM_ROLE_OPERATOR: TEAM_OPERATOR,
    TEAM_ROLE_ADMIN: TEAM_ADMIN,
    TEAM_ROLE_OWNER: TEAM_OWNER,
}

#: A role this version has not heard of holds nothing in its team.
_NOTHING: frozenset[str] = frozenset()


def permissions_for_team_role(role: str | None) -> frozenset[str]:
    """
    Args:
        role: A team role.

    Returns:
        What it holds in its team; nothing for a role this version does not know.
    """
    return TEAM_ROLE_PERMISSIONS.get(str(role or ""), _NOTHING)


def team_permissions_union(team_roles: Mapping[Any, str]) -> frozenset[str]:
    """
    Args:
        team_roles: Team id to the principal's role there.

    Returns:
        Everything the principal holds in any of its teams: the ceiling a
        route is first held to, before the team of the resource narrows it.
    """
    held: set[str] = set()
    for role in team_roles.values():
        held |= permissions_for_team_role(role)
    return frozenset(held)


def instance_permissions_of(payload: Mapping[str, Any]) -> frozenset[str]:
    """
    What a principal holds outside any team.

    Args:
        payload: An authenticated payload.

    Returns:
        Its ``instance_permissions`` when it names them (an account's payload
        does); otherwise everything it holds, which is the case of a
        credential that is not a team member at all: the master token, a
        fleet token, a token nobody owns.
    """
    declared = payload.get("instance_permissions")
    return frozenset(declared) if declared is not None else permissions_of(payload)


def permissions_in_team(payload: Mapping[str, Any], team_id: int | None) -> frozenset[str]:
    """
    What a principal may do to a resource of one team.

    Args:
        payload: An authenticated payload.
        team_id: The team the resource belongs to, or None for a resource of
            none (a row not yet given an owner).

    Returns:
        Its instance permissions plus its team role's, never more than the
        credential holds (an API token narrowed below its owner stays narrow).
    """
    held = permissions_of(payload)
    if "team_roles" not in payload:
        return held
    roles: Mapping[Any, str] = payload["team_roles"]
    extra = permissions_for_team_role(roles.get(team_id)) if team_id is not None else _NOTHING
    return held & (instance_permissions_of(payload) | extra)


def team_fields(
    account_id: int,
    instance_role: str,
    instance_permissions: frozenset[str],
    store: Any = None,
) -> dict[str, Any]:
    """
    What a payload says about the teams of the account behind it.

    The one place a payload learns its teams, called wherever an account's
    credential is turned into one (a session, a token an account owns), so the
    two cannot disagree. Read on every request, so removing someone from a team
    takes effect on their next call.

    Args:
        account_id: The account.
        instance_role: Its instance role.
        instance_permissions: What that role holds outside any team.
        store: The store; the process-wide one by default.

    Returns:
        ``team_roles`` and ``sees_all_teams`` (read back by
        :func:`noust.web.permissions.principal.scope_of`),
        ``instance_permissions``, and ``permissions``: the instance role's plus
        everything its team roles add anywhere, the ceiling a route is held to
        before the resource's own team narrows it. If the teams cannot be read
        the account is given none: it sees nothing rather than everything.
    """
    try:
        scope = TeamStore(store).scope_for(account_id, instance_role)
    except (StoreError, sqlite3.Error) as exc:
        logger.error("Cannot read the teams of account %s: %s", account_id, exc)
        scope = Scope(
            team_roles={},
            sees_all=instance_role in INSTANCE_WIDE_ROLES,
            account_id=account_id,
        )
    return {
        "team_roles": dict(scope.team_roles),
        "sees_all_teams": scope.sees_all,
        "instance_permissions": instance_permissions,
        "permissions": instance_permissions | team_permissions_union(scope.team_roles),
    }
