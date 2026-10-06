# Copyright (c) 2024-2026 Yago Lopez Prado
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
    The teams a principal may reach, and its role in each.

    Attributes:
        team_roles: Team id to the principal's role there. Empty for the
            instance's own principals.
        sees_all: Whether no team is filtered out: an instance principal, or
            a member of every team there is.
        account_id: The account behind the principal, when it is one.
    """

    team_roles: Mapping[int, str] = field(default_factory=dict)
    sees_all: bool = False
    account_id: int | None = None

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
            The teams it may see, or None when it may see all of them.
        """
        return None if self.sees_all else frozenset(self.team_roles)

    def sees_team(self, team_id: int | None) -> bool:
        """
        Args:
            team_id: A team, or None for a row that belongs to none yet.

        Returns:
            Whether resources of that team are visible. A row with no team is
            visible only to a principal that sees everything.
        """
        if self.sees_all:
            return True
        return team_id is not None and team_id in self.team_roles

    def role_in(self, team_id: int | None) -> str | None:
        """
        Args:
            team_id: A team.

        Returns:
            The principal's role in it, or None when it has none.
        """
        return None if team_id is None else self.team_roles.get(team_id)


def visible_clause(scope: Scope, column: str = "project_id") -> tuple[str, list[int]]:
    """
    The SQL condition that keeps only the rows a scope sees.

    The one place the filter is spelled: every scoped query of the store uses
    it, so a change to what "visible" means is a change here.

    Args:
        scope: Whom the query is for.
        column: The column holding the row's project id.

    Returns:
        ``(condition, parameters)``, to be joined with ``AND``. ``1 = 1`` when
        nothing is filtered out, and ``0 = 1`` for a principal in no team (and
        so a row with no project is never visible to it).
    """
    ids = scope.team_ids
    if ids is None:
        return "1 = 1", []
    if not ids:
        return "0 = 1", []
    ordered = sorted(ids)
    marks = ", ".join("?" for _ in ordered)
    # The column is code and the marks are question marks: nothing typed is spliced in.
    return f"{column} IN (SELECT id FROM projects WHERE team_id IN ({marks}))", ordered  # noqa: S608


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
