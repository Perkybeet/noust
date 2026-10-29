# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Custom roles, role bindings, and what a principal holds where.

:mod:`noust.core.access` keeps the two tables. This module decides, because it
is the one that knows what a permission is:

- **What a role may hold** (I-07): permissions that exist, and not both
  halves of the separation of duties - governing who may run the server
  (``security.manage``, ``accounts.manage``, ``audit.manage``) and running it
  (deploying, operating, managing), where a framework asks for it. The roles
  are one list now (:class:`~noust.core.access.role_manager.RoleManager` keeps
  it); this manager is the front the access commands and routes were written
  against, and gives a role at a scope.
- **Who may be given it, and where** (I-03): a built-in team role or a custom
  one on a team, a project or an environment, and a custom one on the server.
  A scope narrower than the server can only carry what a team can own
  (:data:`~noust.web.permissions.team_roles.TENANT_PERMISSIONS`); a role that
  reaches the machine is never inert-but-present at a project.
- **What a principal ends up holding where**: :func:`resolve_grants` turns an
  account's memberships and bindings into one set of permissions per team,
  project and environment, which :func:`noust.web.permissions.team_roles.permissions_at`
  then unions for the resource in front of it.

Everything is checked here, at the one writer, not by the routes or the CLI
that call it (rule 4).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from noust.core.access import (
    SCOPE_ENVIRONMENT,
    SCOPE_INSTANCE,
    SCOPE_PROJECT,
    SCOPE_TEAM,
    AccessNotFoundError,
    AccessStore,
    Binding,
    CustomRole,
)
from noust.core.access.builtin import GOVERNANCE, RUNNING, separation_conflict
from noust.core.access.model import validate_scope_kind
from noust.core.access.role_manager import RoleManager
from noust.core.access.roles import KIND_CUSTOM, RoleStore
from noust.core.access.store import custom_role_of
from noust.core.accounts.model import Account
from noust.core.exceptions import ValidationError
from noust.core.teams import TeamStore
from noust.core.teams.model import (
    TEAM_ROLE_ADMIN,
    TEAM_ROLE_OPERATOR,
    TEAM_ROLE_VIEWER,
    TEAM_ROLES,
    now,
    refuse_instance_only_member,
)
from noust.web.permissions import ALL_PERMISSIONS, DESCRIPTIONS, Permission

P = Permission

#: Built-in team roles that a project or an environment may be given. ``owner``
#: governs a team's members, which a project does not have.
NARROW_BUILT_INS: tuple[str, ...] = (TEAM_ROLE_VIEWER, TEAM_ROLE_OPERATOR, TEAM_ROLE_ADMIN)


def _tenant_permissions() -> frozenset[str]:
    """
    Returns:
        What a team can own. Imported late: the team roles import this module's
        neighbours at load.
    """
    from noust.web.permissions.team_roles import TENANT_PERMISSIONS

    return TENANT_PERMISSIONS


def ceiling_for(scope_kind: str) -> frozenset[str]:
    """
    Args:
        scope_kind: Where a role is held.

    Returns:
        The most a role can mean there. A team may also govern its own members;
        a project or an environment holds none of that; the server holds what
        the role says.
    """
    if scope_kind == SCOPE_INSTANCE:
        return ALL_PERMISSIONS
    tenant = _tenant_permissions()
    return tenant | {P.TEAMS_MANAGE} if scope_kind == SCOPE_TEAM else tenant


def built_in_team_permissions(role: str) -> frozenset[str]:
    """
    Args:
        role: A built-in team role.

    Returns:
        What it holds in its team (empty for a name this version does not know).
    """
    from noust.web.permissions.team_roles import TEAM_ROLE_PERMISSIONS

    return TEAM_ROLE_PERMISSIONS.get(role, frozenset())


def role_permissions(
    role: str, scope_kind: str, custom: Mapping[str, frozenset[str]]
) -> frozenset[str]:
    """
    What a role means at a scope.

    Args:
        role: A built-in team role or a custom role's name.
        scope_kind: Where it is held.
        custom: Custom roles' permissions by name.

    Returns:
        The role's permissions cut to what the scope can carry. A role the
        server does not know (deleted by hand) holds nothing: it fails closed.
    """
    if role in TEAM_ROLES:
        return built_in_team_permissions(role) & ceiling_for(scope_kind)
    return custom.get(role, frozenset()) & ceiling_for(scope_kind)


@dataclass(frozen=True)
class GrantSet:
    """
    What an account holds at each scope, in permissions.

    Attributes:
        team: Team id to what the account holds in that team (its membership
            role's, and a custom role bound to the team).
        project: Project id to what a role bound to it adds.
        environment: Environment id to what a role bound to it adds.
        instance: What roles bound to the server add to the account's own role.
    """

    team: dict[int, frozenset[str]] = field(default_factory=dict)
    project: dict[int, frozenset[str]] = field(default_factory=dict)
    environment: dict[int, frozenset[str]] = field(default_factory=dict)
    instance: frozenset[str] = frozenset()

    def union(self) -> frozenset[str]:
        """
        Returns:
            Everything held anywhere: the ceiling a route is first held to,
            before the resource's own place narrows it.
        """
        held: set[str] = set(self.instance)
        for part in (self.team, self.project, self.environment):
            for permissions in part.values():
                held |= permissions
        return frozenset(held)


def resolve_grants(
    memberships: Mapping[int, str],
    bindings: Iterable[tuple[str, int, str]],
    custom: Mapping[str, frozenset[str]],
) -> GrantSet:
    """
    Turn an account's memberships and bindings into permissions per scope.

    The one place a role name becomes a set of permissions, so the session, a
    token and the API agree.

    Args:
        memberships: Team id to the built-in role the account has there.
        bindings: ``(scope_kind, scope_id, role)`` of its bindings.
        custom: Custom roles' permissions by name.

    Returns:
        The grants.
    """
    team: dict[int, frozenset[str]] = {
        team_id: role_permissions(role, SCOPE_TEAM, custom) for team_id, role in memberships.items()
    }
    project: dict[int, frozenset[str]] = {}
    environment: dict[int, frozenset[str]] = {}
    instance: set[str] = set()
    for kind, scope_id, role in bindings:
        granted = role_permissions(role, kind, custom)
        if kind == SCOPE_TEAM:
            team[scope_id] = team.get(scope_id, frozenset()) | granted
        elif kind == SCOPE_PROJECT:
            project[scope_id] = project.get(scope_id, frozenset()) | granted
        elif kind == SCOPE_ENVIRONMENT:
            environment[scope_id] = environment.get(scope_id, frozenset()) | granted
        elif kind == SCOPE_INSTANCE:
            instance |= granted
    return GrantSet(team, project, environment, frozenset(instance))


def custom_permissions(store: AccessStore) -> dict[str, frozenset[str]]:
    """
    Args:
        store: The access store.

    Returns:
        Each role of the operator's own, by name, with what it holds now: the ones that can be
        given at a team, a project or an environment.
    """
    return {
        role.name: role.held
        for role in RoleStore(store.store).list_roles()
        if role.kind == KIND_CUSTOM
    }


class AccessManager:
    """
    Define custom roles and give roles at a scope.

    Args:
        store: The store; the process-wide one by default.
    """

    def __init__(self, store: object | None = None) -> None:
        self._store = store
        self.data = AccessStore(store)  # type: ignore[arg-type]
        self.teams = TeamStore(store)  # type: ignore[arg-type]

    # -------------------------------------------------------------------- roles

    def catalog(self) -> dict[str, object]:
        """
        Returns:
            Everything a screen needs to offer roles at a scope: the built-in team roles, the
            roles of the operator's own, the permission names with their meaning, and which
            permissions a team, a project and an environment can carry. ``server_roles`` is the
            whole list of the server's roles (:class:`~noust.core.access.role_manager.RoleManager`).
        """
        tenant = _tenant_permissions()
        listed = RoleStore(self.data.store).list_roles()
        return {
            "built_in": [
                {
                    "name": role,
                    "kind": "team",
                    "permissions": sorted(built_in_team_permissions(role)),
                    "scopes": [SCOPE_TEAM, SCOPE_PROJECT, SCOPE_ENVIRONMENT]
                    if role in NARROW_BUILT_INS
                    else [SCOPE_TEAM],
                }
                for role in TEAM_ROLES
            ],
            "custom": [
                custom_role_of(role).to_dict() for role in listed if role.kind == KIND_CUSTOM
            ],
            "server_roles": [role.to_dict() for role in listed],
            "permissions": {name: DESCRIPTIONS.get(name, "") for name in sorted(ALL_PERMISSIONS)},
            "team_permissions": sorted(tenant | {P.TEAMS_MANAGE}),
            "narrow_permissions": sorted(tenant),
        }

    def create_role(
        self,
        name: str,
        permissions: Iterable[str],
        *,
        label: str | None = None,
        description: str | None = None,
        actor: str | None = None,
    ) -> CustomRole:
        """
        Define a role of one's own.

        Args:
            name: Its slug.
            permissions: What it holds.
            label: What the console shows; the name by default.
            description: What it is for.
            actor: Who defines it.

        Returns:
            The role.

        Raises:
            ValidationError: For a name or a permission that is not valid.
            AccessError: When the set mixes governing and running.
            AccessConflictError: When the name is taken or there are too many roles.
        """
        created = RoleManager(self._store).create(  # type: ignore[arg-type]
            name, permissions, label=label, description=description, actor=actor
        )
        return custom_role_of(created)

    def update_role(
        self,
        name: str,
        *,
        permissions: Iterable[str] | None = None,
        label: str | None = None,
        description: str | None = None,
        actor: str | None = None,
    ) -> CustomRole:
        """
        Change a role; every account that holds it holds the change.

        Args:
            name: The role: one of the operator's own or one Noust ships.
            permissions: Its new permissions, when they change.
            label: Its new label.
            description: Its new description.
            actor: Who changes it.

        Returns:
            The role as it is now.

        Raises:
            AccessNotFoundError: When there is no such role.
            AccessError: When it is the owner, or a change would break separation of duties for
                the role or for an account that holds it.
        """
        updated = RoleManager(self._store).update(  # type: ignore[arg-type]
            name, permissions=permissions, label=label, description=description, actor=actor
        )
        return custom_role_of(updated)

    def delete_role(self, name: str) -> CustomRole:
        """
        Remove a role of one's own that nobody holds.

        Args:
            name: The role.

        Returns:
            The role as it was.

        Raises:
            AccessNotFoundError: When there is none.
            AccessConflictError: When an account still holds it.
        """
        return custom_role_of(RoleManager(self._store).delete(name))  # type: ignore[arg-type]

    # ----------------------------------------------------------------- bindings

    def account(self, username: str) -> Account:
        """
        Args:
            username: An account's name.

        Returns:
            The account.

        Raises:
            AccessNotFoundError: When there is none.
        """
        from noust.core.accounts.manager import AccountManager

        found = AccountManager(self._store).find(username)  # type: ignore[arg-type]
        if found is None:
            raise AccessNotFoundError(
                f"No account named {username!r}",
                details="List the accounts with 'noust user list', or invite the person.",
                field="username",
            )
        return found

    def _resolve_scope(
        self,
        scope_kind: str,
        *,
        team: str | None,
        project: str | None,
        environment: str | None,
    ) -> tuple[str, int]:
        """
        Find the row a scope names.

        Args:
            scope_kind: Where the role is held.
            team: A team slug.
            project: A project slug.
            environment: An environment slug.

        Returns:
            ``(scope_kind, scope_id)``.

        Raises:
            ValidationError: When the scope leaves out what it needs.
            TeamNotFoundError: When something named does not exist.
        """
        if scope_kind == SCOPE_INSTANCE:
            return SCOPE_INSTANCE, 0
        if not team:
            raise ValidationError(
                "Name the team", details="Give the team's slug (team=...).", field="team"
            )
        owner = self.teams.require_team(team)
        if scope_kind == SCOPE_TEAM:
            return SCOPE_TEAM, owner.id
        if not project:
            raise ValidationError(
                "Name the project",
                details="Give the project's slug (project=...).",
                field="project",
            )
        chosen = self.teams.require_project(owner.slug, project)
        if scope_kind == SCOPE_PROJECT:
            return SCOPE_PROJECT, chosen.id
        if not environment:
            raise ValidationError(
                "Name the environment",
                details="Give the environment's slug (environment=...).",
                field="environment",
            )
        stage = self.teams.require_environment(owner.slug, chosen.slug, environment)
        return SCOPE_ENVIRONMENT, stage.id

    def bound_scope(
        self,
        *,
        team: str | None = None,
        project: str | None = None,
        environment: str | None = None,
    ) -> tuple[str, int] | None:
        """
        Find the scope an API token is to be bound to (I-34).

        Args:
            team: A team slug.
            project: A project slug.
            environment: An environment slug.

        Returns:
            ``(kind, id)`` of the narrowest scope the names add up to, or None
            when none was named (a token that reaches whatever its owner does).

        Raises:
            ValidationError: When a project or an environment is named without
                what it lives in.
            TeamNotFoundError: When something named does not exist.
        """
        if not (team or project or environment):
            return None
        kind = self.scope_kind_of(project=project, environment=environment, team=team)
        return self._resolve_scope(kind, team=team, project=project, environment=environment)

    def scope_kind_of(
        self, *, project: str | None = None, environment: str | None = None, team: str | None = None
    ) -> str:
        """
        Args:
            project: A project slug, when the request names one.
            environment: An environment slug.
            team: A team slug.

        Returns:
            The narrowest scope the names add up to: the environment if given,
            else the project, else the team, else the server.
        """
        if environment:
            return SCOPE_ENVIRONMENT
        if project:
            return SCOPE_PROJECT
        return SCOPE_TEAM if team else SCOPE_INSTANCE

    def bind(
        self,
        username: str,
        role: str,
        *,
        scope: str | None = None,
        team: str | None = None,
        project: str | None = None,
        environment: str | None = None,
        actor: str | None = None,
        by: Account | None = None,
    ) -> Binding:
        """
        Give an account a role at a scope.

        A built-in team role on a whole team is a membership
        (:meth:`~noust.core.teams.TeamStore.add_member`), kept where it always
        was; every other combination is a binding. Giving the account a role
        where it already has one replaces it.

        Args:
            username: The account.
            role: A built-in team role or a custom role's name.
            scope: ``instance``, ``team``, ``project`` or ``environment``; the
                narrowest the other arguments name by default.
            team: A team slug.
            project: A project slug.
            environment: An environment slug.
            actor: Who gives it.
            by: The account asking (a token's owner); None for root and the master token.

        Returns:
            The binding. A role on the whole server is one more role of the account: it comes
            back as a binding with ``id`` 0, like a built-in role on a whole team. A built-in role on a whole team returns the membership
            as a binding with ``id`` 0.

        Raises:
            ValidationError: For a scope or role that does not fit.
            AccessNotFoundError: When the account or a custom role does not exist.
            AccessError: When separation of duties, the ENS profile, or an
                instance-only account refuses it.
        """
        kind = validate_scope_kind(
            scope or self.scope_kind_of(project=project, environment=environment, team=team)
        )
        account = self.account(username)
        role_name = (role or "").strip().lower()
        if kind == SCOPE_INSTANCE:
            return self._add_role(account, role_name, actor=actor, by=by)
        custom = custom_permissions(self.data)
        built_in = role_name in TEAM_ROLES
        if not built_in and role_name not in custom:
            raise AccessNotFoundError(
                f"No role named {role!r}",
                details="List them with 'noust access role list'.",
                field="role",
            )
        if built_in and kind != SCOPE_TEAM and role_name not in NARROW_BUILT_INS:
            raise ValidationError(
                f"{role_name!r} governs a team's members, which a {kind} does not have",
                details=f"Give 'admin' on the {kind}, or {role_name!r} on the team.",
                field="role",
            )
        if kind != SCOPE_INSTANCE:
            for held in account.roles:
                refuse_instance_only_member(account.username, held)
            granted = role_permissions(role_name, kind, custom)
            if not granted - {P.SELF}:
                raise ValidationError(
                    f"The role {role_name!r} holds nothing a {kind} can own",
                    details=(
                        "A team, a project or an environment can hold permissions about its "
                        "applications, databases and backups; this role has none of those."
                    ),
                    field="role",
                )
        scope_kind, scope_id = self._resolve_scope(
            kind, team=team, project=project, environment=environment
        )
        if built_in and scope_kind == SCOPE_TEAM:
            member = self.teams.add_member(
                team or "", account.username, role_name, created_by=actor
            )
            return Binding(
                id=0,
                account_id=member.account_id,
                username=member.username,
                scope_kind=SCOPE_TEAM,
                scope_id=member.team_id,
                scope=member.team,
                role=member.role,
                created_at=member.created_at,
                created_by=member.created_by,
            )
        return self.data.put_binding(
            account.id, scope_kind, scope_id, role_name, now=now(), actor=actor
        )

    def _add_role(
        self, account: Account, role: str, *, actor: str | None, by: Account | None
    ) -> Binding:
        """
        Give an account one more role on the whole server.

        Args:
            account: The account.
            role: A role of the list.
            actor: Who gives it.
            by: The account asking; None for root and the master token.

        Returns:
            The role as a binding with ``id`` 0 (the roles of an account are not rows of their own).

        Raises:
            ValidationError: When the list has no such role.
            AccessError: When a rule of the roles an account holds refuses it.
        """
        from noust.core.accounts.manager import AccountManager

        manager = AccountManager(self._store)  # type: ignore[arg-type]
        changed = manager.set_roles(account.username, [*account.roles, role], by=by, actor=actor)
        return Binding(
            id=0,
            account_id=changed.id,
            username=changed.username,
            scope_kind=SCOPE_INSTANCE,
            scope_id=0,
            scope="",
            role=role,
            created_at=now(),
            created_by=actor,
        )

    def unbind(self, binding_id: int) -> Binding:
        """
        Take a role away.

        Args:
            binding_id: The binding's id.

        Returns:
            The binding as it was.

        Raises:
            AccessNotFoundError: When there is none.
        """
        return self.data.delete_binding(binding_id)

    def list_bindings(self, username: str | None = None) -> list[Binding]:
        """
        Args:
            username: Only this account's.

        Returns:
            The bindings on the server, by account.

        Raises:
            AccessNotFoundError: When the account named does not exist.
        """
        account_id = self.account(username).id if username else None
        return self.data.list_bindings(account_id=account_id)

    def bindings_in_team(self, team: str) -> list[Binding]:
        """
        Args:
            team: A team's slug.

        Returns:
            The bindings at the team, at its projects and at their environments.

        Raises:
            TeamNotFoundError: When there is no such team.
        """
        owner = self.teams.require_team(team)
        projects = {p.id for p in self.teams.list_projects(team=owner.slug)}
        environments = {e.id for e in self.teams.list_environments(team=owner.slug)}
        inside = {
            SCOPE_TEAM: {owner.id},
            SCOPE_PROJECT: projects,
            SCOPE_ENVIRONMENT: environments,
        }
        return [
            b
            for b in self.data.list_bindings()
            if b.scope_kind in inside and b.scope_id in inside[b.scope_kind]
        ]

    def team_of_binding(self, binding: Binding) -> int | None:
        """
        Args:
            binding: A binding.

        Returns:
            The team its scope is in, or None for one on the server.
        """
        if binding.scope_kind == SCOPE_TEAM:
            return binding.scope_id
        if binding.scope_kind == SCOPE_PROJECT:
            return self.data.team_of_project(binding.scope_id)
        if binding.scope_kind == SCOPE_ENVIRONMENT:
            project = self.data.project_of_environment(binding.scope_id)
            return self.data.team_of_project(project) if project is not None else None
        return None


__all__ = [
    "GOVERNANCE",
    "NARROW_BUILT_INS",
    "RUNNING",
    "AccessManager",
    "GrantSet",
    "built_in_team_permissions",
    "ceiling_for",
    "custom_permissions",
    "resolve_grants",
    "role_permissions",
    "separation_conflict",
]
