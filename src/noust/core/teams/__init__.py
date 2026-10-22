# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Teams, projects and environments, and the ownership filter of the data layer.

A team groups people; a project groups what they run; an environment is a
stage of a project (production, staging). Every application, site, database
and backup schedule belongs to one project and one environment, and a
principal sees only the teams it belongs to (I-01, I-02, I-04, I-10 of the
3.4 checklist). A team is a boundary of permissions and organisation, not of
execution: applications still share one kernel, and the console says so.

The pieces, each stated once:

- :mod:`~noust.core.teams.model`: the names, the rules between them, the errors.
- :mod:`~noust.core.teams.scope`: :class:`Scope`, which teams a principal sees,
  and the SQL that says so.
- :mod:`~noust.core.teams.repository`: :class:`TeamStore`, the one reader and
  writer of the tables.
- :mod:`~noust.core.teams.scoped`: :class:`ScopedStore`, the only way the API
  reads applications, databases, sites and backup schedules.
- :mod:`~noust.core.teams.ownership`: where a new row lives.
- :mod:`~noust.core.teams.backfill`: what an existing server is given.

Importing this package starts neither the store nor the accounts: both import
it.
"""

from noust.core.teams.model import (
    DEFAULT_SLUG,
    INSTANCE_ONLY_ROLES,
    INSTANCE_WIDE_ROLES,
    PRODUCTION_SLUG,
    TEAM_ROLE_ADMIN,
    TEAM_ROLE_OPERATOR,
    TEAM_ROLE_OWNER,
    TEAM_ROLE_VIEWER,
    TEAM_ROLES,
    Membership,
    Project,
    ProjectEnvironment,
    ResourceNotFoundError,
    Target,
    Team,
    TeamConflictError,
    TeamError,
    TeamNotFoundError,
)
from noust.core.teams.ownership import ownership_target
from noust.core.teams.repository import OwnerLabels, TeamStore
from noust.core.teams.scope import Scope, bind, bind_for_request, current_scope, visible_clause
from noust.core.teams.scoped import ScopedStore, scoped_store

__all__ = [
    "DEFAULT_SLUG",
    "INSTANCE_ONLY_ROLES",
    "INSTANCE_WIDE_ROLES",
    "PRODUCTION_SLUG",
    "TEAM_ROLES",
    "TEAM_ROLE_ADMIN",
    "TEAM_ROLE_OPERATOR",
    "TEAM_ROLE_OWNER",
    "TEAM_ROLE_VIEWER",
    "Membership",
    "OwnerLabels",
    "Project",
    "ProjectEnvironment",
    "ResourceNotFoundError",
    "Scope",
    "ScopedStore",
    "Target",
    "Team",
    "TeamConflictError",
    "TeamError",
    "TeamNotFoundError",
    "TeamStore",
    "bind",
    "bind_for_request",
    "current_scope",
    "ownership_target",
    "scoped_store",
    "visible_clause",
]
