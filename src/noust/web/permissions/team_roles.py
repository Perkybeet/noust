# Copyright (c) 2024-2026 Yago López Prado
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

A role can also be held on one project or on one environment (I-03), built in
or defined by an administrator (I-07, :mod:`noust.web.permissions.access`). A
payload then carries what the account holds at each scope as permissions
(``scope_permissions``), and :func:`permissions_at` unions what applies to the
resource in front of it: the team's, its project's and its environment's.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Mapping
from typing import Any

from noust.core.access import (
    SCOPE_ENVIRONMENT,
    SCOPE_PROJECT,
    SCOPE_TEAM,
    AccessStore,
)
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
from noust.web.permissions.access import GrantSet, custom_permissions, resolve_grants
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

#: What :func:`team_fields` adds to a payload besides ``permissions``, which a credential
#: that is narrower than its owner (an API token) takes whole and narrows itself.
TEAM_PAYLOAD_KEYS: tuple[str, ...] = (
    "team_roles",
    "project_roles",
    "environment_roles",
    "parent_projects",
    "sees_all_teams",
    "instance_permissions",
    "scope_permissions",
)

#: The scopes an API token can be bound to (I-34).
BOUND_KINDS: tuple[str, ...] = (SCOPE_TEAM, SCOPE_PROJECT, SCOPE_ENVIRONMENT)

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


def _held_at(mapping: Mapping[Any, Any] | None, key: int | None) -> frozenset[str]:
    """
    Args:
        mapping: Scope id to the permissions held there; keys may have been
            turned into text on their way through JSON.
        key: The scope's id, or None for a resource that has none.

    Returns:
        What is held there; nothing when the scope is not in the mapping.
    """
    if not mapping or key is None:
        return _NOTHING
    found = mapping.get(key)
    if found is None:
        found = mapping.get(str(key))
    return frozenset(found) if found is not None else _NOTHING


def permissions_at(
    payload: Mapping[str, Any],
    team_id: int | None,
    project_id: int | None = None,
    environment_id: int | None = None,
) -> frozenset[str]:
    """
    What a principal may do to a resource, by where it lives.

    Args:
        payload: An authenticated payload.
        team_id: The team the resource belongs to, or None for a resource of
            none (a row not yet given an owner).
        project_id: Its project, when known.
        environment_id: Its environment, when known.

    Returns:
        Its instance permissions plus what it holds in the resource's team,
        project and environment, never more than the credential holds (an API
        token narrowed below its owner stays narrow).
    """
    held = permissions_of(payload)
    if "team_roles" not in payload:
        return held
    granted = payload.get("scope_permissions")
    if granted is None:
        # A payload from before roles had scopes: the team role names the team's permissions.
        roles: Mapping[Any, str] = payload["team_roles"]
        extra = permissions_for_team_role(roles.get(team_id)) if team_id is not None else _NOTHING
    else:
        extra = (
            _held_at(granted.get("team"), team_id)
            | _held_at(granted.get("project"), project_id)
            | _held_at(granted.get("environment"), environment_id)
        )
    return held & (instance_permissions_of(payload) | extra)


def permissions_in_team(payload: Mapping[str, Any], team_id: int | None) -> frozenset[str]:
    """
    What a principal may do to a resource of one team, whatever its project.

    Args:
        payload: An authenticated payload.
        team_id: The team the resource belongs to, or None.

    Returns:
        What :func:`permissions_at` says for the team alone.
    """
    return permissions_at(payload, team_id)


def team_fields(
    account_id: int,
    instance_role: str,
    instance_permissions: frozenset[str],
    store: Any = None,
) -> dict[str, Any]:
    """
    What a payload says about the teams of the account behind it.

    The one place a payload learns its teams, projects and environments, called
    wherever an account's credential is turned into one (a session, a token an
    account owns), so the two cannot disagree. Read on every request, so
    removing someone from a team, or taking a role from them, takes effect on
    their next call.

    Args:
        account_id: The account.
        instance_role: Its instance role.
        instance_permissions: What that role holds outside any team.
        store: The store; the process-wide one by default.

    Returns:
        ``team_roles``, ``project_roles``, ``environment_roles``,
        ``parent_projects`` and ``sees_all_teams`` (read back by
        :func:`noust.web.permissions.principal.scope_of`),
        ``instance_permissions`` (the role's, plus roles bound to the whole
        server), ``scope_permissions`` (what it holds at each team, project and
        environment), and ``permissions``: everything it holds anywhere, the
        ceiling a route is held to before the resource's own place narrows it.
        If the teams cannot be read the account is given none: it sees nothing
        rather than everything.
    """
    try:
        teams = TeamStore(store)
        scope = teams.scope_for(account_id, instance_role)
        access = AccessStore(store)
        grants = resolve_grants(
            teams.memberships_of(account_id),
            access.grants_of(account_id),
            custom_permissions(access),
        )
    except (StoreError, sqlite3.Error) as exc:
        logger.error("Cannot read the teams of account %s: %s", account_id, exc)
        scope = Scope(
            team_roles={},
            sees_all=instance_role in INSTANCE_WIDE_ROLES,
            account_id=account_id,
        )
        grants = GrantSet()
    held = instance_permissions | grants.instance
    return {
        "team_roles": dict(scope.team_roles),
        "project_roles": dict(scope.project_roles),
        "environment_roles": dict(scope.environment_roles),
        "parent_projects": sorted(scope.parent_projects),
        "sees_all_teams": scope.sees_all,
        "instance_permissions": held,
        "scope_permissions": {
            "team": dict(grants.team),
            "project": dict(grants.project),
            "environment": dict(grants.environment),
        },
        "permissions": held | grants.union(),
    }


def bound_reach(fields: Mapping[str, Any], kind: str, bound_id: int, store: Any = None) -> bool:
    """
    Whether an account reaches the scope a token is bound to.

    Args:
        fields: What :func:`team_fields` says about the account.
        kind: ``team``, ``project`` or ``environment``.
        bound_id: That scope's row.
        store: The store; the process-wide one by default.

    Returns:
        True when the account sees the team whole, or holds a role on the
        project, the environment, or a team or project above it. A token
        never reaches more than its owner does, so one bound to a scope its
        owner left reaches nothing.
    """
    if fields.get("sees_all_teams"):
        return True
    access = AccessStore(store)
    team_id: int | None
    project_id: int | None = None
    environment_id: int | None = None
    if kind == SCOPE_TEAM:
        team_id = bound_id
    elif kind == SCOPE_PROJECT:
        project_id = bound_id
        team_id = access.team_of_project(bound_id)
    elif kind == SCOPE_ENVIRONMENT:
        environment_id = bound_id
        project_id = access.project_of_environment(bound_id)
        team_id = access.team_of_project(project_id) if project_id is not None else None
    else:
        return False
    if team_id is None:
        return False
    if _held_key(fields.get("team_roles"), team_id):
        return True
    if kind == SCOPE_TEAM:
        return False
    if project_id is not None and _held_key(fields.get("project_roles"), project_id):
        return True
    return environment_id is not None and _held_key(fields.get("environment_roles"), environment_id)


def _held_key(mapping: Mapping[Any, Any] | None, key: int) -> bool:
    """
    Args:
        mapping: Scope id to something.
        key: A scope id.

    Returns:
        Whether the mapping has it, as a number or as text.
    """
    return bool(mapping) and (key in mapping or str(key) in mapping)  # type: ignore[operator]


def bound_fields(
    fields: Mapping[str, Any], kind: str, bound_id: int, store: Any = None
) -> dict[str, Any]:
    """
    Narrow what an account holds to the one scope a token is bound to (I-34).

    A token bound to a client's project sees that project and nothing else of
    the server, even when its owner is an administrator: outside the scope it
    holds a viewer's floor, and inside it what its owner holds there, cut to
    what a team can own (a bound token never reaches the machine).

    Args:
        fields: What :func:`team_fields` says about the owner.
        kind: ``team``, ``project`` or ``environment``.
        bound_id: That scope's row.
        store: The store; the process-wide one by default.

    Returns:
        The payload fields the token is given instead of the owner's: the
        same keys as :func:`team_fields`. A scope the owner does not reach, or
        that no longer exists, yields a token that sees nothing.
    """
    access = AccessStore(store)
    floor = frozenset(instance_permissions_of(fields)) & VIEWER
    empty: dict[str, Any] = {
        "team_roles": {},
        "project_roles": {},
        "environment_roles": {},
        "parent_projects": [],
        "sees_all_teams": False,
        "instance_permissions": floor,
        "scope_permissions": {"team": {}, "project": {}, "environment": {}},
        "permissions": floor,
    }
    if not bound_reach(fields, kind, bound_id, store):
        return empty
    granted: Mapping[str, Mapping[Any, frozenset[str]]] = fields.get("scope_permissions") or {}
    inst = frozenset(instance_permissions_of(fields))
    tenant = TENANT_PERMISSIONS
    label = "token"
    teams: dict[int, str] = {}
    projects: dict[int, str] = {}
    environments: dict[int, str] = {}
    parents: list[int] = []
    scope_permissions: dict[str, dict[int, frozenset[str]]] = {
        "team": {},
        "project": {},
        "environment": {},
    }
    if kind == SCOPE_TEAM:
        team_id = bound_id
        scope_permissions["team"][team_id] = (
            inst & (tenant | {Permission.TEAMS_MANAGE})
        ) | _held_at(granted.get("team"), team_id)
        teams[team_id] = label
        for project_id in access.projects_of_team(team_id):
            held = _held_at(granted.get("project"), project_id)
            if held:
                scope_permissions["project"][project_id] = held
        for environment_id in access.environments_of_team(team_id):
            held = _held_at(granted.get("environment"), environment_id)
            if held:
                scope_permissions["environment"][environment_id] = held
    else:
        owner_project = (
            bound_id if kind == SCOPE_PROJECT else access.project_of_environment(bound_id)
        )
        team_id_up = access.team_of_project(owner_project) if owner_project is not None else None
        if owner_project is None or team_id_up is None:
            return empty
        project_id = owner_project
        above = (
            inst
            | (_held_at(granted.get("team"), team_id_up) - {Permission.TEAMS_MANAGE})
            | _held_at(granted.get("project"), project_id)
        ) & tenant
        if kind == SCOPE_PROJECT:
            scope_permissions["project"][project_id] = above
            projects[project_id] = label
            for environment_id in access.environments_of_project(project_id):
                held = _held_at(granted.get("environment"), environment_id)
                if held:
                    scope_permissions["environment"][environment_id] = held
        else:
            scope_permissions["environment"][bound_id] = (
                above | _held_at(granted.get("environment"), bound_id)
            ) & tenant
            environments[bound_id] = label
            parents.append(project_id)
    union: set[str] = set(floor)
    for part in scope_permissions.values():
        for held in part.values():
            union |= held
    return {
        "team_roles": teams,
        "project_roles": projects,
        "environment_roles": environments,
        "parent_projects": parents,
        "sees_all_teams": False,
        "instance_permissions": floor,
        "scope_permissions": scope_permissions,
        "permissions": frozenset(union),
    }
