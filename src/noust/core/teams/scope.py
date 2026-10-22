# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Which teams a principal sees, and the SQL that says so.

A :class:`Scope` is what the data layer is asked with. It is built once per
request from who is asking (:func:`noust.web.permissions.principal.scope_of`),
never from anything the request says about itself, and it travels to the
store through :class:`~noust.core.teams.scoped.ScopedStore`: a query for
applications, databases, sites or backup schedules cannot be written without
one, so a route that forgot to ask "whose is this" cannot exist.

Who sees what:

- The master token, a central's fleet token and a token nobody owns act as the
  instance (:meth:`Scope.everything`): they see every team.
- ``admin``, ``security`` and ``auditor`` accounts see every team.
- A ``viewer`` or ``operator`` account sees the teams it belongs to, plus every
  team there is when it belongs to all of them (a server with one team behaves
  as 3.3 did until a second one exists).
- Any account may also be given a role on one project, or on one environment,
  of a team it does not belong to (I-03): it then sees that project's or
  environment's resources and nothing else of the team.

The ambient scope (:func:`bind`, :func:`current_scope`) is how code that has no
request in hand - a helper two calls below a route - still asks with the
principal's scope. Outside a request (the CLI as root, a job, the monitor) it
is the instance's, which is the point: those are instance-level by definition.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Scope:
    """
    The teams, projects and environments a principal may reach, and its role in each.

    Attributes:
        team_roles: Team id to the principal's role there. Empty for the
            instance's own principals.
        sees_all: Whether no team is filtered out: an instance principal, or
            a member of every team there is.
        account_id: The account behind the principal, when it is one.
        project_roles: Project id to the principal's role on that project alone
            (I-03). A project of a team the principal belongs to is not here
            unless it was given a role of its own.
        environment_roles: Environment id to the principal's role on that
            environment alone.
        parent_projects: Projects that hold an environment the principal was
            given but are not theirs: shown so the environment can be found,
            holding nothing else.
    """

    team_roles: Mapping[int, str] = field(default_factory=dict)
    sees_all: bool = False
    account_id: int | None = None
    project_roles: Mapping[int, str] = field(default_factory=dict)
    environment_roles: Mapping[int, str] = field(default_factory=dict)
    parent_projects: frozenset[int] = frozenset()

    @classmethod
    def everything(cls) -> Scope:
        """
        Returns:
            The scope of the instance itself: nothing is filtered out.
        """
        return cls(team_roles={}, sees_all=True)

    @classmethod
    def nothing(cls, account_id: int | None = None) -> Scope:
        """
        Returns:
            The scope of a principal that belongs to no team: it sees nothing.
        """
        return cls(team_roles={}, sees_all=False, account_id=account_id)

    @property
    def restricted(self) -> bool:
        """Whether some team's resources are hidden from this principal."""
        return not self.sees_all

    @property
    def team_ids(self) -> frozenset[int] | None:
        """
        Returns:
            The teams it may see whole, or None when it may see all of them.
        """
        return None if self.sees_all else frozenset(self.team_roles)

    @property
    def project_ids(self) -> frozenset[int]:
        """
        Returns:
            The projects it was given on their own, beyond its teams.
        """
        return frozenset(self.project_roles)

    @property
    def environment_ids(self) -> frozenset[int]:
        """
        Returns:
            The environments it was given on their own, beyond its projects.
        """
        return frozenset(self.environment_roles)

    def sees_team(self, team_id: int | None) -> bool:
        """
        Args:
            team_id: A team, or None for a row that belongs to none yet.

        Returns:
            Whether the principal belongs to the team as a whole. A project or
            an environment given alone does not make its team visible: the
            team's members and settings stay the team's.
        """
        if self.sees_all:
            return True
        return team_id is not None and team_id in self.team_roles

    def sees_project(self, project_id: int, team_id: int | None) -> bool:
        """
        Args:
            project_id: A project.
            team_id: The team it belongs to.

        Returns:
            Whether the project is visible: its team's, one given alone, or
            the parent of an environment given alone.
        """
        return (
            self.sees_team(team_id)
            or project_id in self.project_roles
            or project_id in self.parent_projects
        )

    def sees_environment(self, environment_id: int, project_id: int, team_id: int | None) -> bool:
        """
        Args:
            environment_id: An environment.
            project_id: The project it belongs to.
            team_id: That project's team.

        Returns:
            Whether the environment is visible: through its team, its project,
            or given alone.
        """
        return (
            self.sees_team(team_id)
            or project_id in self.project_roles
            or environment_id in self.environment_roles
        )

    def sees_placement(
        self, team_id: int | None, project_id: int | None, environment_id: int | None
    ) -> bool:
        """
        The test :func:`visible_clause` makes in SQL, for a row already read.

        Args:
            team_id: The team of the row's project.
            project_id: The row's project.
            environment_id: The row's environment.

        Returns:
            Whether a resource there is visible: through its team, through its
            project given alone, or through its environment given alone. A
            project that only holds an environment given alone does not make
            the rest of it visible.
        """
        if self.sees_all:
            return True
        if team_id is not None and team_id in self.team_roles:
            return True
        if project_id is not None and project_id in self.project_roles:
            return True
        return environment_id is not None and environment_id in self.environment_roles

    def role_in(self, team_id: int | None) -> str | None:
        """
        Args:
            team_id: A team.

        Returns:
            The principal's role in it, or None when it has none.
        """
        return None if team_id is None else self.team_roles.get(team_id)

    @property
    def has_narrow_grants(self) -> bool:
        """Whether some access is held on a project or an environment alone."""
        return bool(self.project_roles or self.environment_roles)


def _env_column(column: str) -> str:
    """
    Args:
        column: A project column, such as ``project_id`` or ``a.project_id``.

    Returns:
        The environment column of the same table.
    """
    if column.endswith("project_id"):
        return column[: -len("project_id")] + "environment_id"
    raise ValueError(f"{column!r} is not a project column")  # a typo in the code


def visible_clause(scope: Scope, column: str = "project_id") -> tuple[str, list[int]]:
    """
    The SQL condition that keeps only the rows a scope sees.

    The one place the filter is spelled: every scoped query of the store uses
    it, so a change to what "visible" means is a change here. A row is visible
    through its team, through its project given alone, or through its
    environment given alone.

    Args:
        scope: Whom the query is for.
        column: The column holding the row's project id; the environment column
            of the same table is derived from it.

    Returns:
        ``(condition, parameters)``, to be joined with ``AND``. ``1 = 1`` when
        nothing is filtered out, and ``0 = 1`` for a principal that reaches
        nothing (and so a row with no project is never visible to it).
    """
    ids = scope.team_ids
    if ids is None:
        return "1 = 1", []
    parts: list[str] = []
    params: list[int] = []
    # The column is code and the marks are question marks: nothing typed is spliced in.
    if ids:
        ordered = sorted(ids)
        marks = ", ".join("?" for _ in ordered)
        parts.append(f"{column} IN (SELECT id FROM projects WHERE team_id IN ({marks}))")  # noqa: S608
        params.extend(ordered)
    if scope.project_roles:
        ordered = sorted(scope.project_roles)
        marks = ", ".join("?" for _ in ordered)
        parts.append(f"{column} IN ({marks})")
        params.extend(ordered)
    if scope.environment_roles:
        ordered = sorted(scope.environment_roles)
        marks = ", ".join("?" for _ in ordered)
        parts.append(f"{_env_column(column)} IN ({marks})")
        params.extend(ordered)
    if not parts:
        return "0 = 1", []
    return (parts[0] if len(parts) == 1 else "(" + " OR ".join(parts) + ")"), params


_current: ContextVar[Scope | None] = ContextVar("noust_scope", default=None)


def current_scope() -> Scope:
    """
    Returns:
        The scope of the request being served, or the instance's outside one.
    """
    return _current.get() or Scope.everything()


@contextmanager
def bind(scope: Scope) -> Iterator[Scope]:
    """
    Make a scope the ambient one for a block.

    Args:
        scope: The scope of whoever is being served.

    Yields:
        The scope.
    """
    token = _current.set(scope)
    try:
        yield scope
    finally:
        _current.reset(token)


def bind_for_request(scope: Scope) -> None:
    """
    Make a scope the ambient one for the rest of the request.

    A dependency cannot wrap the handler in a ``with``; it sets the scope and
    the request's task, which owns the context, drops it when the response is
    done.

    Args:
        scope: The scope of whoever is being served.
    """
    _current.set(scope)
