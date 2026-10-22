# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The permissions of teams, projects and environments (:mod:`noust.web.api.teams`).

Reading what one sees is ``teams.read`` and held by every role; changing a
team, its members, projects or environments is ``teams.manage``, which an
``admin`` holds on the whole server and a team's ``owner`` holds in that team
(``noust.web.permissions.team_roles``). Choosing the team one works in and
reading one's own teams are one's own business: ``self``. Moving an application
needs to manage applications, in the team it leaves and in the team it joins.
"""

from __future__ import annotations

from noust.web.permissions import Permission

ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/api/teams"): Permission.TEAMS_READ,
    ("POST", "/api/teams"): Permission.TEAMS_MANAGE,
    ("GET", "/api/teams/mine"): Permission.SELF,
    ("GET", "/api/teams/roles"): Permission.SELF,
    ("PUT", "/api/teams/active"): Permission.SELF,
    ("GET", "/api/teams/{team}"): Permission.TEAMS_READ,
    ("PATCH", "/api/teams/{team}"): Permission.TEAMS_MANAGE,
    ("DELETE", "/api/teams/{team}"): Permission.TEAMS_MANAGE,
    ("GET", "/api/teams/{team}/members"): Permission.TEAMS_READ,
    ("POST", "/api/teams/{team}/members"): Permission.TEAMS_MANAGE,
    ("PATCH", "/api/teams/{team}/members/{username}"): Permission.TEAMS_MANAGE,
    ("DELETE", "/api/teams/{team}/members/{username}"): Permission.TEAMS_MANAGE,
    ("GET", "/api/teams/{team}/projects"): Permission.TEAMS_READ,
    ("POST", "/api/teams/{team}/projects"): Permission.TEAMS_MANAGE,
    ("PATCH", "/api/teams/{team}/projects/{project}"): Permission.TEAMS_MANAGE,
    ("DELETE", "/api/teams/{team}/projects/{project}"): Permission.TEAMS_MANAGE,
    ("GET", "/api/teams/{team}/projects/{project}/environments"): Permission.TEAMS_READ,
    ("POST", "/api/teams/{team}/projects/{project}/environments"): Permission.TEAMS_MANAGE,
    (
        "PATCH",
        "/api/teams/{team}/projects/{project}/environments/{environment}",
    ): Permission.TEAMS_MANAGE,
    (
        "DELETE",
        "/api/teams/{team}/projects/{project}/environments/{environment}",
    ): Permission.TEAMS_MANAGE,
    ("GET", "/api/projects"): Permission.TEAMS_READ,
    ("GET", "/api/environments"): Permission.TEAMS_READ,
    ("POST", "/api/apps/{domain}/move"): Permission.APPS_MANAGE,
}
