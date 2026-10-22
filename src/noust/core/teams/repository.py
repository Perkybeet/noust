# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Teams, their members, projects and environments, and what belongs to them.

:class:`TeamStore` is the one reader and writer of the five tables the 3.4
schema adds for this (``teams``, ``team_members``, ``projects``,
``environments``, ``account_active_team``) and of the ownership columns of
applications, sites, databases and backup schedules. It works on the store's
own connection, the way :class:`~noust.core.accounts.manager.AccountManager`
does, so a ``--dry-run`` rehearses it and a failure rolls back with the rest
of the store.

It decides nothing about who may call it: the API holds a principal to the
permission each route declares and to its scope, and the CLI runs as root.
What it does guard is what the data itself needs: slugs, names that are
taken, an instance-only account joining a team, the last owner leaving, and
deleting anything that still holds resources.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from noust.core.access.model import SCOPE_ENVIRONMENT, SCOPE_PROJECT, SCOPE_TEAM
from noust.core.access.store import AccessStore
from noust.core.exceptions import ValidationError
from noust.core.teams import ownership
from noust.core.teams.model import (
    DEFAULT_SLUG,
    INSTANCE_WIDE_ROLES,
    OWNED_TABLES,
    PRODUCTION_NAME,
    PRODUCTION_SLUG,
    TEAM_ROLE_OWNER,
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
    clean_name,
    now,
    refuse_instance_only_member,
    slugify,
    validate_slug,
    validate_team_role,
)
from noust.core.teams.scope import Scope

if TYPE_CHECKING:
    from noust.core.store import NoustStore


def _team(row: sqlite3.Row) -> Team:
    return Team(
        id=int(row["id"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
    )


def _project(row: sqlite3.Row) -> Project:
    return Project(
        id=int(row["id"]),
        team_id=int(row["team_id"]),
        team=str(row["team_slug"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
    )


def _environment(row: sqlite3.Row) -> ProjectEnvironment:
    return ProjectEnvironment(
        id=int(row["id"]),
        project_id=int(row["project_id"]),
        project=str(row["project_slug"]),
        team=str(row["team_slug"]),
        slug=str(row["slug"]),
        name=str(row["name"]),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
    )


def _membership(row: sqlite3.Row) -> Membership:
    return Membership(
        team_id=int(row["team_id"]),
        team=str(row["team_slug"]),
        account_id=int(row["account_id"]),
        username=str(row["username"]),
        role=str(row["role"]),
        instance_role=str(row["instance_role"]),
        created_at=float(row["created_at"]),
        created_by=row["created_by"],
    )


_PROJECT_SELECT = (
    "SELECT p.id, p.team_id, t.slug AS team_slug, p.slug, p.name, p.created_at, p.created_by "
    "FROM projects p JOIN teams t ON t.id = p.team_id"
)
_ENVIRONMENT_SELECT = (
    "SELECT e.id, e.project_id, p.slug AS project_slug, t.slug AS team_slug, e.slug, e.name, "
    "e.created_at, e.created_by FROM environments e "
    "JOIN projects p ON p.id = e.project_id JOIN teams t ON t.id = p.team_id"
)
_MEMBER_SELECT = (
    "SELECT m.team_id, t.slug AS team_slug, m.account_id, a.username, m.role, "
    "a.role AS instance_role, m.created_at, m.created_by FROM team_members m "
    "JOIN teams t ON t.id = m.team_id JOIN accounts a ON a.id = m.account_id"
)


@dataclass(frozen=True)
class Move:
    """
    What a move did.

    Attributes:
        domain: The application.
        team: The slug of the team it is in now.
        project: The slug of its project.
        environment: The slug of its environment.
        moved: Rows moved, by kind: the application, its sites, its databases
            and its backup schedule move together.
    """

    domain: str
    team: str
    project: str
    environment: str
    moved: dict[str, int]


class OwnerLabels:
    """
    The slugs a list of resources shows for where each one lives.

    Read once for a whole list, so naming the project of fifty applications is
    one query and not fifty.

    Args:
        projects: Project id to ``(team slug, project slug)``.
        environments: Environment id to its slug.
    """

    def __init__(self, projects: dict[int, tuple[str, str]], environments: dict[int, str]) -> None:
        self._projects = projects
        self._environments = environments

    def of(
        self, project_id: int | None, environment_id: int | None
    ) -> tuple[str | None, str | None, str | None]:
        """
        Args:
            project_id: A row's project.
            environment_id: Its environment.

        Returns:
            ``(team, project, environment)`` slugs, None where it has none.
        """
        team, project = self._projects.get(project_id or 0, (None, None))
        return team, project, self._environments.get(environment_id or 0)


class TeamStore:
    """
    Read and write teams, projects, environments, memberships and ownership.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: NoustStore | None = None) -> None:
        self._store = store

    @property
    def store(self) -> NoustStore:
        """The store the teams live in."""
        if self._store is not None:
            return self._store
        from noust.core.store import get_store

        return get_store()

    def _rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        """
        Args:
            sql: A SELECT.
            params: Its parameters.

        Returns:
            The rows.
        """
        return list(self.store._get_connection().execute(sql, params).fetchall())

    # ------------------------------------------------------------------ teams

    def list_teams(self, scope: Scope | None = None) -> list[Team]:
        """
        Args:
            scope: Only the teams this scope sees; every team by default.

        Returns:
            The teams, by name.
        """
        teams = [_team(row) for row in self._rows("SELECT * FROM teams ORDER BY name, id")]
        return [team for team in teams if scope is None or scope.sees_team(team.id)]

    def get_team(self, slug: str) -> Team | None:
        """
        Args:
            slug: The team's slug.

        Returns:
            The team, or None.
        """
        rows = self._rows("SELECT * FROM teams WHERE slug = ?", ((slug or "").strip().lower(),))
        return _team(rows[0]) if rows else None

    def team_by_id(self, team_id: int) -> Team | None:
        """
        Args:
            team_id: The team's id.

        Returns:
            The team, or None.
        """
        rows = self._rows("SELECT * FROM teams WHERE id = ?", (team_id,))
        return _team(rows[0]) if rows else None

    def require_team(self, slug: str) -> Team:
        """
        Args:
            slug: The team's slug.

        Returns:
            The team.

        Raises:
            TeamNotFoundError: When no team has that slug.
        """
        team = self.get_team(slug)
        if team is None:
            raise TeamNotFoundError(
                f"No team named {slug!r}",
                details="List the teams with 'noust team list'.",
                field="team",
            )
        return team

    def create_team(
        self, name: str, *, slug: str | None = None, created_by: str | None = None
    ) -> Team:
        """
        Create a team, with a default project and its production environment.

        Args:
            name: Display name.
            slug: Its slug; made from the name when omitted.
            created_by: Who is creating it, for the record.

        Returns:
            The team.

        Raises:
            ValidationError: When the name or the slug is refused.
            TeamConflictError: When the slug is taken.
        """
        display = clean_name(name, fallback=slug or "")
        chosen = validate_slug(slug if slug else slugify(display), what="team")
        stamp = now()
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO teams (slug, name, created_at, created_by) VALUES (?, ?, ?, ?)",
                    (chosen, display, stamp, created_by),
                )
                team_id = int(cursor.lastrowid or 0)
                self._insert_project(cursor, team_id, DEFAULT_SLUG, "Default", created_by)
        except sqlite3.IntegrityError as exc:
            raise TeamConflictError(
                f"A team named {chosen!r} already exists",
                details="Pick another slug with --slug, or list the teams with 'noust team list'.",
                field="slug",
            ) from exc
        return self.require_team(chosen)

    def rename_team(self, slug: str, name: str) -> Team:
        """
        Change a team's display name.

        Args:
            slug: The team.
            name: The new name.

        Returns:
            The team.

        Raises:
            TeamNotFoundError: When there is no such team.
        """
        team = self.require_team(slug)
        display = clean_name(name, fallback=team.name)
        with self.store._transaction() as cursor:
            cursor.execute("UPDATE teams SET name = ? WHERE id = ?", (display, team.id))
        return self.require_team(team.slug)

    def delete_team(self, slug: str) -> Team:
        """
        Delete a team that holds nothing.

        Its projects, environments and memberships go with it.

        Args:
            slug: The team.

        Returns:
            The team as it was.

        Raises:
            TeamNotFoundError: When there is no such team.
            TeamConflictError: When it is the default team, or still holds
                applications, sites, databases or backup schedules.
        """
        team = self.require_team(slug)
        if team.slug == DEFAULT_SLUG:
            raise TeamConflictError(
                "The default team cannot be deleted",
                details="It receives what nobody assigned. Rename it, or leave it empty.",
            )
        held = self.resource_counts(team_id=team.id)
        if any(held.values()):
            raise TeamConflictError(
                f"The team {team.slug!r} still holds {_describe(held)}",
                details=(
                    "Move them to another project first ('noust app move APP --project P --env E'), "
                    "or delete them."
                ),
            )
        servers = int(
            self._rows("SELECT COUNT(*) AS n FROM node_teams WHERE team_id = ?", (team.id,))[0]["n"]
        )
        if servers:
            raise TeamConflictError(
                f"The team {team.slug!r} still has {servers} server{'' if servers == 1 else 's'}",
                details="Give them back to the instance first ('noust node unassign NODE').",
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM access_bindings WHERE (scope_kind = 'team' AND scope_id = ?) "
                "OR (scope_kind = 'project' AND scope_id IN "
                "(SELECT id FROM projects WHERE team_id = ?)) "
                "OR (scope_kind = 'environment' AND scope_id IN "
                "(SELECT e.id FROM environments e JOIN projects p ON p.id = e.project_id "
                "WHERE p.team_id = ?))",
                (team.id, team.id, team.id),
            )
            cursor.execute("DELETE FROM teams WHERE id = ?", (team.id,))
        return team

    # --------------------------------------------------------------- projects

    def _insert_project(
        self,
        cursor: sqlite3.Cursor,
        team_id: int,
        slug: str,
        name: str,
        created_by: str | None,
    ) -> int:
        """
        Insert a project and its production environment.

        Args:
            cursor: A cursor inside the caller's transaction.
            team_id: The team.
            slug: The project's slug.
            name: Its display name.
            created_by: Who is creating it.

        Returns:
            The project's id.
        """
        stamp = now()
        cursor.execute(
            "INSERT INTO projects (team_id, slug, name, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (team_id, slug, name, stamp, created_by),
        )
        project_id = int(cursor.lastrowid or 0)
        cursor.execute(
            "INSERT INTO environments (project_id, slug, name, created_at, created_by) "
            "VALUES (?, ?, ?, ?, ?)",
            (project_id, PRODUCTION_SLUG, PRODUCTION_NAME, stamp, created_by),
        )
        return project_id

    def list_projects(self, scope: Scope | None = None, team: str | None = None) -> list[Project]:
        """
        Args:
            scope: Only the projects of the teams this scope sees.
            team: Only this team's projects.

        Returns:
            The projects, by team and name.
        """
        sql = _PROJECT_SELECT
        params: tuple[Any, ...] = ()
        if team is not None:
            sql += " WHERE t.slug = ?"
            params = (team.strip().lower(),)
        projects = [_project(row) for row in self._rows(sql + " ORDER BY t.name, p.name", params)]
        return [p for p in projects if scope is None or scope.sees_project(p.id, p.team_id)]

    def get_project(self, team: str, slug: str) -> Project | None:
        """
        Args:
            team: The team's slug.
            slug: The project's slug.

        Returns:
            The project, or None.
        """
        rows = self._rows(
            _PROJECT_SELECT + " WHERE t.slug = ? AND p.slug = ?",
            ((team or "").strip().lower(), (slug or "").strip().lower()),
        )
        return _project(rows[0]) if rows else None

    def project_by_id(self, project_id: int) -> Project | None:
        """
        Args:
            project_id: The project's id.

        Returns:
            The project, or None.
        """
        rows = self._rows(_PROJECT_SELECT + " WHERE p.id = ?", (project_id,))
        return _project(rows[0]) if rows else None

    def require_project(self, team: str, slug: str) -> Project:
        """
        Args:
            team: The team's slug.
            slug: The project's slug.

        Returns:
            The project.

        Raises:
            TeamNotFoundError: When the team has no such project.
        """
        project = self.get_project(team, slug)
        if project is None:
            raise TeamNotFoundError(
                f"The team {team!r} has no project named {slug!r}",
                details=f"List its projects with 'noust project list --team {team}'.",
                field="project",
            )
        return project

    def create_project(
        self, team: str, name: str, *, slug: str | None = None, created_by: str | None = None
    ) -> Project:
        """
        Create a project in a team, with its production environment.

        Args:
            team: The team's slug.
            name: Display name.
            slug: Its slug; made from the name when omitted.
            created_by: Who is creating it.

        Returns:
            The project.

        Raises:
            TeamNotFoundError: When there is no such team.
            TeamConflictError: When the team already has a project with that slug.
        """
        owner = self.require_team(team)
        display = clean_name(name, fallback=slug or "")
        chosen = validate_slug(slug if slug else slugify(display), what="project")
        try:
            with self.store._transaction() as cursor:
                self._insert_project(cursor, owner.id, chosen, display, created_by)
        except sqlite3.IntegrityError as exc:
            raise TeamConflictError(
                f"The team {owner.slug!r} already has a project named {chosen!r}",
                details="Pick another slug with --slug.",
                field="slug",
            ) from exc
        return self.require_project(owner.slug, chosen)

    def rename_project(self, team: str, slug: str, name: str) -> Project:
        """
        Change a project's display name; its slug, which commands and URLs
        name it by, stays.

        Args:
            team: The team's slug.
            slug: The project's slug.
            name: The new name; the old one stays when it is empty.

        Returns:
            The project.

        Raises:
            TeamNotFoundError: When there is no such project.
            ValidationError: When the name is too long or unprintable.
        """
        project = self.require_project(team, slug)
        display = clean_name(name, fallback=project.name)
        with self.store._transaction() as cursor:
            cursor.execute("UPDATE projects SET name = ? WHERE id = ?", (display, project.id))
        return self.require_project(project.team, project.slug)

    def delete_project(self, team: str, slug: str) -> Project:
        """
        Delete a project that holds nothing, with its environments.

        Args:
            team: The team's slug.
            slug: The project's slug.

        Returns:
            The project as it was.

        Raises:
            TeamNotFoundError: When there is no such project.
            TeamConflictError: When it is the default project of the default
                team, or still holds resources.
        """
        project = self.require_project(team, slug)
        if project.team == DEFAULT_SLUG and project.slug == DEFAULT_SLUG:
            raise TeamConflictError(
                "The default project cannot be deleted",
                details="It receives what nobody assigned. Leave it empty.",
            )
        held = self.resource_counts(project_id=project.id)
        if any(held.values()):
            raise TeamConflictError(
                f"The project {project.team}/{project.slug} still holds {_describe(held)}",
                details="Move them to another project first, or delete them.",
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM access_bindings WHERE (scope_kind = 'project' AND scope_id = ?) "
                "OR (scope_kind = 'environment' AND scope_id IN "
                "(SELECT id FROM environments WHERE project_id = ?))",
                (project.id, project.id),
            )
            cursor.execute("DELETE FROM projects WHERE id = ?", (project.id,))
        return project

    # ----------------------------------------------------------- environments

    def list_environments(
        self, scope: Scope | None = None, *, team: str | None = None, project: str | None = None
    ) -> list[ProjectEnvironment]:
        """
        Args:
            scope: Only the environments of the teams this scope sees.
            team: Only this team's.
            project: Only this project's (needs ``team``).

        Returns:
            The environments, by team, project and name.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if team is not None:
            clauses.append("t.slug = ?")
            params.append(team.strip().lower())
        if project is not None:
            clauses.append("p.slug = ?")
            params.append(project.strip().lower())
        sql = _ENVIRONMENT_SELECT
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        rows = self._rows(sql + " ORDER BY t.name, p.name, e.name", tuple(params))
        environments = [_environment(row) for row in rows]
        if scope is None:
            return environments
        owners = {p.id: p.team_id for p in self.list_projects()}
        return [
            e
            for e in environments
            if scope.sees_environment(e.id, e.project_id, owners.get(e.project_id))
        ]

    def get_environment(self, team: str, project: str, slug: str) -> ProjectEnvironment | None:
        """
        Args:
            team: The team's slug.
            project: The project's slug.
            slug: The environment's slug.

        Returns:
            The environment, or None.
        """
        rows = self._rows(
            _ENVIRONMENT_SELECT + " WHERE t.slug = ? AND p.slug = ? AND e.slug = ?",
            (
                (team or "").strip().lower(),
                (project or "").strip().lower(),
                (slug or "").strip().lower(),
            ),
        )
        return _environment(rows[0]) if rows else None

    def environment_by_id(self, environment_id: int) -> ProjectEnvironment | None:
        """
        Args:
            environment_id: The environment's id.

        Returns:
            The environment, or None.
        """
        rows = self._rows(_ENVIRONMENT_SELECT + " WHERE e.id = ?", (environment_id,))
        return _environment(rows[0]) if rows else None

    def require_environment(self, team: str, project: str, slug: str) -> ProjectEnvironment:
        """
        Args:
            team: The team's slug.
            project: The project's slug.
            slug: The environment's slug.

        Returns:
            The environment.

        Raises:
            TeamNotFoundError: When the project has no such environment.
        """
        environment = self.get_environment(team, project, slug)
        if environment is None:
            raise TeamNotFoundError(
                f"The project {team}/{project} has no environment named {slug!r}",
                details=(
                    f"List its environments with 'noust environment list --team {team} "
                    f"--project {project}'."
                ),
                field="environment",
            )
        return environment

    def create_environment(
        self,
        team: str,
        project: str,
        name: str,
        *,
        slug: str | None = None,
        created_by: str | None = None,
    ) -> ProjectEnvironment:
        """
        Create an environment in a project.

        Args:
            team: The team's slug.
            project: The project's slug.
            name: Display name.
            slug: Its slug; made from the name when omitted.
            created_by: Who is creating it.

        Returns:
            The environment.

        Raises:
            TeamNotFoundError: When there is no such project.
            TeamConflictError: When the project already has that environment.
        """
        owner = self.require_project(team, project)
        display = clean_name(name, fallback=slug or "")
        chosen = validate_slug(slug if slug else slugify(display), what="environment")
        try:
            with self.store._transaction() as cursor:
                cursor.execute(
                    "INSERT INTO environments (project_id, slug, name, created_at, created_by) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (owner.id, chosen, display, now(), created_by),
                )
        except sqlite3.IntegrityError as exc:
            raise TeamConflictError(
                f"The project {owner.team}/{owner.slug} already has an environment "
                f"named {chosen!r}",
                details="Pick another slug with --slug.",
                field="slug",
            ) from exc
        return self.require_environment(owner.team, owner.slug, chosen)

    def rename_environment(
        self, team: str, project: str, slug: str, name: str
    ) -> ProjectEnvironment:
        """
        Change an environment's display name; its slug stays.

        Args:
            team: The team's slug.
            project: The project's slug.
            slug: The environment's slug.
            name: The new name; the old one stays when it is empty.

        Returns:
            The environment.

        Raises:
            TeamNotFoundError: When there is no such environment.
            ValidationError: When the name is too long or unprintable.
        """
        environment = self.require_environment(team, project, slug)
        display = clean_name(name, fallback=environment.name)
        with self.store._transaction() as cursor:
            cursor.execute(
                "UPDATE environments SET name = ? WHERE id = ?", (display, environment.id)
            )
        return self.require_environment(environment.team, environment.project, environment.slug)

    def delete_environment(self, team: str, project: str, slug: str) -> ProjectEnvironment:
        """
        Delete an environment that holds nothing.

        Args:
            team: The team's slug.
            project: The project's slug.
            slug: The environment's slug.

        Returns:
            The environment as it was.

        Raises:
            TeamNotFoundError: When there is no such environment.
            TeamConflictError: When it is the last of its project, the default
                project's production, or still holds resources.
        """
        environment = self.require_environment(team, project, slug)
        siblings = self.list_environments(team=team, project=project)
        if len(siblings) <= 1:
            raise TeamConflictError(
                f"{team}/{project}/{environment.slug} is the only environment of its project",
                details="A project needs one to hold anything. Create another first.",
            )
        if environment.team == DEFAULT_SLUG and (project, slug) == (DEFAULT_SLUG, PRODUCTION_SLUG):
            raise TeamConflictError(
                "The default environment cannot be deleted",
                details="It receives what nobody assigned. Leave it empty.",
            )
        held = self.resource_counts(environment_id=environment.id)
        if any(held.values()):
            raise TeamConflictError(
                f"The environment {team}/{project}/{environment.slug} still holds {_describe(held)}",
                details="Move them to another environment first, or delete them.",
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM access_bindings WHERE scope_kind = 'environment' AND scope_id = ?",
                (environment.id,),
            )
            cursor.execute("DELETE FROM environments WHERE id = ?", (environment.id,))
        return environment

    def owner_labels(self) -> OwnerLabels:
        """
        Returns:
            The labels of every project and environment, to name where rows live.
        """
        projects = {
            int(row["id"]): (str(row["team_slug"]), str(row["slug"]))
            for row in self._rows(_PROJECT_SELECT)
        }
        environments = {
            int(row["id"]): str(row["slug"])
            for row in self._rows("SELECT id, slug FROM environments")
        }
        return OwnerLabels(projects, environments)

    # ---------------------------------------------------------------- members

    def memberships_of(self, account_id: int) -> dict[int, str]:
        """
        Args:
            account_id: An account.

        Returns:
            Team id to the account's role there, for every team it belongs to.
        """
        rows = self._rows(
            "SELECT team_id, role FROM team_members WHERE account_id = ?", (account_id,)
        )
        return {int(row["team_id"]): str(row["role"]) for row in rows}

    def teams_of(self, account_id: int) -> list[tuple[Team, str]]:
        """
        Args:
            account_id: An account.

        Returns:
            Each team it belongs to, with its role there, by team name.
        """
        roles = self.memberships_of(account_id)
        return [(team, roles[team.id]) for team in self.list_teams() if team.id in roles]

    def list_members(self, team: str) -> list[Membership]:
        """
        Args:
            team: The team's slug.

        Returns:
            Its members, strongest role first and then by name.

        Raises:
            TeamNotFoundError: When there is no such team.
        """
        owner = self.require_team(team)
        rows = self._rows(_MEMBER_SELECT + " WHERE m.team_id = ?", (owner.id,))
        members = [_membership(row) for row in rows]
        return sorted(members, key=lambda m: (-TEAM_ROLES.index(m.role), m.username))

    def _account(self, username: str) -> sqlite3.Row:
        """
        Args:
            username: An account's name.

        Returns:
            Its row (id, username, role, status).

        Raises:
            TeamNotFoundError: When no account has that name.
        """
        rows = self._rows(
            "SELECT id, username, role, status FROM accounts WHERE username = ?",
            ((username or "").strip().lower(),),
        )
        if not rows:
            raise TeamNotFoundError(
                f"No account named {username!r}",
                details="List the accounts with 'noust user list', or invite the person.",
                field="username",
            )
        return rows[0]

    def add_member(
        self, team: str, username: str, role: str, *, created_by: str | None = None
    ) -> Membership:
        """
        Make an account a member of a team, or change its role there.

        Args:
            team: The team's slug.
            username: The account's name.
            role: One of :data:`~noust.core.teams.model.TEAM_ROLES`.
            created_by: Who is adding it.

        Returns:
            The membership.

        Raises:
            TeamNotFoundError: When the team or the account does not exist.
            ValidationError: When the role is unknown, or the account is a
                security or auditor account, which never joins a team.
            TeamConflictError: When it would demote the team's last owner.
        """
        owner = self.require_team(team)
        account = self._account(username)
        chosen = validate_team_role(role)
        refuse_instance_only_member(str(account["username"]), str(account["role"]))
        current = self.memberships_of(int(account["id"])).get(owner.id)
        if current == TEAM_ROLE_OWNER and chosen != TEAM_ROLE_OWNER:
            self._refuse_last_owner(owner, int(account["id"]), "demote")
        with self.store._transaction() as cursor:
            cursor.execute(
                "INSERT INTO team_members (team_id, account_id, role, created_at, created_by) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(team_id, account_id) "
                "DO UPDATE SET role = excluded.role",
                (owner.id, int(account["id"]), chosen, now(), created_by),
            )
        return self.member(owner.slug, str(account["username"]))

    def member(self, team: str, username: str) -> Membership:
        """
        Args:
            team: The team's slug.
            username: The account's name.

        Returns:
            Its membership.

        Raises:
            TeamNotFoundError: When the account is not a member.
        """
        for membership in self.list_members(team):
            if membership.username == (username or "").strip().lower():
                return membership
        raise TeamNotFoundError(
            f"{username!r} is not a member of the team {team!r}",
            details=f"List its members with 'noust team members {team}'.",
            field="username",
        )

    def remove_member(self, team: str, username: str) -> Membership:
        """
        Take an account out of a team.

        Args:
            team: The team's slug.
            username: The account's name.

        Returns:
            The membership as it was.

        Raises:
            TeamNotFoundError: When the account is not a member.
            TeamConflictError: When it is the team's last owner.
        """
        membership = self.member(team, username)
        if membership.role == TEAM_ROLE_OWNER:
            self._refuse_last_owner(self.require_team(team), membership.account_id, "remove")
        with self.store._transaction() as cursor:
            cursor.execute(
                "DELETE FROM team_members WHERE team_id = ? AND account_id = ?",
                (membership.team_id, membership.account_id),
            )
            cursor.execute(
                "DELETE FROM account_active_team WHERE account_id = ? AND team_id = ?",
                (membership.account_id, membership.team_id),
            )
        return membership

    def _refuse_last_owner(self, team: Team, account_id: int, verb: str) -> None:
        """
        Args:
            team: The team.
            account_id: The owner about to leave or be demoted.
            verb: ``remove`` or ``demote``, for the message.

        Raises:
            TeamConflictError: When nobody else owns the team.
        """
        others = self._rows(
            "SELECT 1 FROM team_members WHERE team_id = ? AND role = ? AND account_id != ?",
            (team.id, TEAM_ROLE_OWNER, account_id),
        )
        if not others:
            raise TeamConflictError(
                f"Cannot {verb} the last owner of the team {team.slug!r}",
                details="Make another member an owner first.",
            )

    # ------------------------------------------------------------ active team

    def active_team(self, account_id: int | None) -> Team | None:
        """
        Args:
            account_id: An account, or None.

        Returns:
            The team it chose to work in, when it chose one.
        """
        if account_id is None:
            return None
        rows = self._rows(
            "SELECT team_id FROM account_active_team WHERE account_id = ?", (account_id,)
        )
        return self.team_by_id(int(rows[0]["team_id"])) if rows else None

    def set_active_team(self, account_id: int, team_id: int | None) -> None:
        """
        Remember the team an account works in.

        The caller has checked the account may see the team.

        Args:
            account_id: The account.
            team_id: The team, or None to forget the choice.
        """
        with self.store._transaction() as cursor:
            if team_id is None:
                cursor.execute(
                    "DELETE FROM account_active_team WHERE account_id = ?", (account_id,)
                )
            else:
                cursor.execute(
                    "INSERT INTO account_active_team (account_id, team_id) VALUES (?, ?) "
                    "ON CONFLICT(account_id) DO UPDATE SET team_id = excluded.team_id",
                    (account_id, team_id),
                )

    # ------------------------------------------------------------------ scope

    def scope_for(self, account_id: int, instance_role: str) -> Scope:
        """
        Work out which teams an account sees.

        Args:
            account_id: The account.
            instance_role: Its instance role.

        Returns:
            Every team for ``admin``, ``security`` and ``auditor``; for the
            other roles the teams it belongs to, which is every team when it
            belongs to all of them.
        """
        roles = self.memberships_of(account_id)
        if instance_role in INSTANCE_WIDE_ROLES:
            return Scope(team_roles=roles, sees_all=True, account_id=account_id)
        team_roles = dict(roles)
        project_roles: dict[int, str] = {}
        environment_roles: dict[int, str] = {}
        access = AccessStore(self.store)
        for kind, scope_id, role in access.grants_of(account_id):
            if kind == SCOPE_TEAM:
                # A custom role at team scope is a membership of its own; a built-in
                # membership already named the team, and keeps its label.
                team_roles.setdefault(scope_id, role)
            elif kind == SCOPE_PROJECT:
                project_roles[scope_id] = role
            elif kind == SCOPE_ENVIRONMENT:
                environment_roles[scope_id] = role
        parents = {
            parent
            for environment_id in environment_roles
            if (parent := access.project_of_environment(environment_id)) is not None
        } - set(project_roles)
        every = {team.id for team in self.list_teams()}
        return Scope(
            team_roles=team_roles,
            sees_all=every <= set(team_roles),
            account_id=account_id,
            project_roles=project_roles,
            environment_roles=environment_roles,
            parent_projects=frozenset(parents),
        )

    def reachable_teams(self, scope: Scope) -> list[Team]:
        """
        Args:
            scope: Whose.

        Returns:
            The teams the scope reaches at all: as a member, or through a
            project or an environment it was given alone. A team reached only
            through a project is not one the principal belongs to, and none of
            its members or settings are shown (:meth:`list_teams`).
        """
        teams = self.list_teams()
        if scope.sees_all:
            return teams
        reached = set(scope.team_roles)
        for project in self.list_projects():
            if project.id in scope.project_roles or project.id in scope.parent_projects:
                reached.add(project.team_id)
        return [team for team in teams if team.id in reached]

    # ----------------------------------------------------------------- target

    def default_target(self) -> Target:
        """
        Returns:
            Where a row goes when nobody says: the default team's default
            project and its production environment, created if missing.
        """
        with self.store._transaction() as cursor:
            target, _ = ownership.ensure_default(cursor)
        return target

    def resolve_target(
        self,
        scope: Scope,
        *,
        team: str | None = None,
        project: str | None = None,
        environment: str | None = None,
    ) -> Target:
        """
        Find the project and environment a new resource is meant for.

        The principal's own choice wins: a team, project and environment named
        in the request. What is left unsaid is filled in, in this order: the
        team the account works in (its active team, or its only team), then
        the default team for a principal that sees every team; a project is
        the team's ``default`` one or its only one; an environment is
        ``production`` or the project's only one. A team the scope does not
        see is answered as a team that does not exist.

        Args:
            scope: Who is asking.
            team: A team slug.
            project: A project slug.
            environment: An environment slug.

        Returns:
            The target.

        Raises:
            TeamNotFoundError: When a named team, project or environment does
                not exist, or the team is not one the principal sees.
            TeamError: When something is ambiguous and the request did not say
                which: several projects, several environments, several teams.
        """
        if team is not None:
            # A team the principal does not reach is answered like one that is not there.
            found = self.get_team(team)
            if found is None or found.id not in {t.id for t in self.reachable_teams(scope)}:
                raise TeamNotFoundError(
                    f"No team named {team!r}",
                    details="List the teams with 'noust team list'.",
                    field="team",
                )
            owner = found
        else:
            owner = self._home_team(scope)
        if project is not None:
            chosen = self.require_project(owner.slug, project)
            if not scope.sees_project(chosen.id, chosen.team_id):
                raise TeamNotFoundError(
                    f"No project {owner.slug}/{project} in this team",
                    details="List them with 'noust project list'.",
                    field="project",
                )
        else:
            projects = self.list_projects(scope, team=owner.slug)
            by_slug = {p.slug: p for p in projects}
            if DEFAULT_SLUG in by_slug:
                chosen = by_slug[DEFAULT_SLUG]
            elif len(projects) == 1:
                chosen = projects[0]
            else:
                raise TeamError(
                    f"The team {owner.slug!r} has several projects and none is named 'default'",
                    details="Say which one: --project NAME (see 'noust project list').",
                    field="project",
                )
        if environment is not None:
            env = self.require_environment(owner.slug, chosen.slug, environment)
            if not scope.sees_environment(env.id, chosen.id, chosen.team_id):
                raise TeamNotFoundError(
                    f"No environment {owner.slug}/{chosen.slug}/{environment}",
                    details="List them with 'noust environment list'.",
                    field="environment",
                )
        else:
            envs = self.list_environments(scope, team=owner.slug, project=chosen.slug)
            by_env = {e.slug: e for e in envs}
            if PRODUCTION_SLUG in by_env:
                env = by_env[PRODUCTION_SLUG]
            elif len(envs) == 1:
                env = envs[0]
            else:
                raise TeamError(
                    f"The project {owner.slug}/{chosen.slug} has several environments and none "
                    "is named 'production'",
                    details="Say which one: --env NAME (see 'noust environment list').",
                    field="environment",
                )
        return Target(owner.id, chosen.id, env.id)

    def resolve_move_target(
        self,
        scope: Scope,
        current_project_id: int | None,
        current_environment_id: int | None = None,
        *,
        team: str | None = None,
        project: str | None = None,
        environment: str | None = None,
    ) -> Target:
        """
        Find where an application moves to, keeping what the request leaves out.

        Moving to another environment names the environment only and stays in
        the application's project; moving to another project of the same team
        names the project; moving to another team names the team (and its
        project, or takes the team's default one). Whatever is left out is
        what the application has now, until the move changes the project: then
        the environment is the new project's ``production``.

        Args:
            scope: Who is asking.
            current_project_id: The project the application is in now.
            current_environment_id: The environment it is in now.
            team: A team slug.
            project: A project slug.
            environment: An environment slug.

        Returns:
            The target.

        Raises:
            TeamNotFoundError: When something named does not exist or is not
                visible to the principal.
            TeamError: When the destination is ambiguous.
        """
        current = self.project_by_id(current_project_id) if current_project_id is not None else None
        if current is None:
            return self.resolve_target(scope, team=team, project=project, environment=environment)
        same_team = team is None or team.strip().lower() == current.team
        chosen_team = current.team if same_team else team
        chosen_project = project
        if chosen_project is None and same_team:
            chosen_project = current.slug
        if environment is None and same_team and chosen_project == current.slug:
            # Staying in the project: staying in the environment too.
            held = (
                self.environment_by_id(current_environment_id)
                if current_environment_id is not None
                else None
            )
            environment = held.slug if held is not None else None
        return self.resolve_target(
            scope, team=chosen_team, project=chosen_project, environment=environment
        )

    def move_app_to(
        self,
        scope: Scope,
        domain: str,
        *,
        team: str | None = None,
        project: str | None = None,
        environment: str | None = None,
        authorize: Callable[[Target], None] | None = None,
    ) -> Move:
        """
        Move an application, named by where it goes, with what follows it.

        The one implementation of "move": the CLI and the API both come here.
        The application must be one ``scope`` sees (anything else is as absent
        as an application that does not exist).

        Args:
            scope: Who is asking.
            domain: The application's domain.
            team: The team it moves to; omitted: the one it is in.
            project: The project it moves to; omitted: the one it is in.
            environment: The environment; omitted: the one it is in when the
                project stays, else the new project's ``production``.
            authorize: Called with the target before anything is written, to
                refuse a destination the caller may not manage.

        Returns:
            Where it is now and what moved.

        Raises:
            ValidationError: When the request names no destination.
            ResourceNotFoundError: When the application is not visible.
            TeamNotFoundError: When the destination does not exist or is not
                visible.
        """
        if team is None and project is None and environment is None:
            raise ValidationError(
                "Say where the application moves to",
                details="Give --project, --env, or both (and --team for another team's).",
                field="project",
            )
        with self.store._transaction() as cursor:
            cursor.execute(
                "SELECT project_id, environment_id FROM apps WHERE domain = ?", (domain,)
            )
            row = cursor.fetchone()
        visible = row is not None and (
            scope.sees_all
            or (
                row[0] is not None
                and scope.sees_placement(self._team_of_project(int(row[0])), int(row[0]), row[1])
            )
        )
        if not visible:
            raise ResourceNotFoundError(f"Application not found: {domain}")
        target = self.resolve_move_target(
            scope,
            row[0],
            row[1],
            team=team,
            project=project,
            environment=environment,
        )
        if authorize is not None:
            authorize(target)
        moved = self.move_app(domain, target.project_id, target.environment_id)
        destination = self.project_by_id(target.project_id)
        stage = self.environment_by_id(target.environment_id)
        if destination is None or stage is None:
            raise TeamNotFoundError("The destination vanished while the application moved")
        return Move(domain, destination.team, destination.slug, stage.slug, moved)

    def _team_of_project(self, project_id: int) -> int | None:
        """
        Args:
            project_id: A project's id.

        Returns:
            Its team's id, or None when there is no such project.
        """
        project = self.project_by_id(project_id)
        return project.team_id if project is not None else None

    def _home_team(self, scope: Scope) -> Team:
        """
        Find the team a request that named none is about.

        Args:
            scope: Who is asking.

        Returns:
            The team the account works in, its only team, or the default.

        Raises:
            TeamError: When the principal is in several teams and has not
                chosen one, or in none.
        """
        active = self.active_team(scope.account_id)
        reachable = self.reachable_teams(scope)
        if active is not None and active.id in {team.id for team in reachable}:
            return active
        if scope.sees_all:
            default = self.get_team(DEFAULT_SLUG)
            if default is not None:
                return default
        visible = reachable
        if len(visible) == 1:
            return visible[0]
        if not visible:
            raise TeamError(
                "This account belongs to no team",
                details="Ask a team's owner to add it, or an administrator ('noust team add-member').",
                field="team",
            )
        raise TeamError(
            "This account belongs to several teams and has not chosen one",
            details="Name the team (team=...) or choose the one to work in (PUT /api/teams/active).",
            field="team",
        )

    # --------------------------------------------------------------- ownership

    def resource_counts(
        self,
        *,
        team_id: int | None = None,
        project_id: int | None = None,
        environment_id: int | None = None,
    ) -> dict[str, int]:
        """
        Count what a team, a project or an environment holds.

        Args:
            team_id: Count what the team's projects hold.
            project_id: Count what the project holds.
            environment_id: Count what the environment holds.

        Returns:
            ``apps``, ``sites``, ``databases`` and ``backup_schedules`` counts.
        """
        if team_id is not None:
            clause, params = (
                "project_id IN (SELECT id FROM projects WHERE team_id = ?)",
                (team_id,),
            )
        elif project_id is not None:
            clause, params = "project_id = ?", (project_id,)
        elif environment_id is not None:
            clause, params = "environment_id = ?", (environment_id,)
        else:
            return dict.fromkeys(OWNED_TABLES, 0)
        return {
            table: int(
                self._rows(f"SELECT COUNT(*) AS n FROM {table} WHERE {clause}", params)[0]["n"]  # noqa: S608 - fixed names
            )
            for table in OWNED_TABLES
        }

    def move_app(self, domain: str, project_id: int, environment_id: int) -> dict[str, int]:
        """
        Move an application, with everything that belongs to it.

        Its sites, the databases linked to it, its backup schedule and the
        previews of it go with it in one transaction, so the application and
        what follows it are never in two places. The database refuses an
        environment that is not the project's.

        Args:
            domain: The application's domain.
            project_id: The project it moves to.
            environment_id: The environment of that project.

        Returns:
            How many rows moved, by table.

        Raises:
            TeamNotFoundError: When the application, the project or the
                environment does not exist.
            TeamConflictError: When the environment is not the project's.
        """
        project = self.project_by_id(project_id)
        if project is None:
            raise TeamNotFoundError(f"No project with id {project_id}", field="project")
        env_rows = self._rows("SELECT project_id FROM environments WHERE id = ?", (environment_id,))
        if not env_rows:
            raise TeamNotFoundError(f"No environment with id {environment_id}", field="environment")
        if int(env_rows[0]["project_id"]) != project.id:
            raise TeamConflictError(
                "That environment does not belong to that project",
                details="Pick an environment of the project ('noust environment list').",
                field="environment",
            )
        moved: dict[str, int] = {}
        with self.store._transaction() as cursor:
            cursor.execute("SELECT id FROM apps WHERE domain = ?", (domain,))
            row = cursor.fetchone()
            if row is None:
                raise TeamNotFoundError(f"Application not found: {domain}", field="domain")
            app_id = int(row[0])
            pair = (project_id, environment_id)
            # The application and the previews that point at it...
            cursor.execute(
                "UPDATE apps SET project_id = ?, environment_id = ? "
                "WHERE id = ? OR preview_parent = ?",
                (*pair, app_id, domain),
            )
            moved["apps"] = cursor.rowcount
            # ...then what belongs to either of them.
            cursor.execute(
                "UPDATE sites SET project_id = ?, environment_id = ? WHERE app_id IN "
                "(SELECT id FROM apps WHERE id = ? OR preview_parent = ?)",
                (*pair, app_id, domain),
            )
            moved["sites"] = cursor.rowcount
            cursor.execute(
                "UPDATE databases SET project_id = ?, environment_id = ? WHERE app_id IN "
                "(SELECT id FROM apps WHERE id = ? OR preview_parent = ?)",
                (*pair, app_id, domain),
            )
            moved["databases"] = cursor.rowcount
            cursor.execute(
                "UPDATE backup_schedules SET project_id = ?, environment_id = ? WHERE app_domain IN "
                "(SELECT domain FROM apps WHERE id = ? OR preview_parent = ?)",
                (*pair, app_id, domain),
            )
            moved["backup_schedules"] = cursor.rowcount
        return moved


def _describe(held: dict[str, int]) -> str:
    """
    Args:
        held: Counts by table, from :meth:`TeamStore.resource_counts`.

    Returns:
        A sentence fragment such as ``2 applications and 1 database``.
    """
    names = {
        "apps": "application",
        "sites": "site",
        "databases": "database",
        "backup_schedules": "backup schedule",
    }
    parts = [
        f"{count} {names[table]}{'' if count == 1 else 's'}"
        for table, count in held.items()
        if count
    ]
    if len(parts) > 1:
        return ", ".join(parts[:-1]) + " and " + parts[-1]
    return parts[0] if parts else "nothing"
