# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Custom roles and role bindings over HTTP (I-03, I-07).

A client of :class:`~noust.web.permissions.access.AccessManager`, like ``noust
access``: every rule (what a role may hold, separation of duties, what a
project can own, the ENS profile's one role per account) is the manager's, not
repeated here. This module only holds the caller to its scope and records what
changed.

Two families of routes:

- ``/api/access`` is about the server: the catalogue of roles, the definition
  of custom ones and every binding, including a role bound to the whole server.
  Defining roles and binding at the server are the security role's decision
  (``accounts.manage``), in sudo mode.
- ``/api/teams/{team}/access`` is about one team: who holds what on its
  projects and environments. A team's owner decides, as it decides the team's
  members, in sudo mode. A team that is not the caller's reads as absent.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.access import Binding
from noust.core.access.model import SCOPE_ENVIRONMENT, SCOPE_INSTANCE, SCOPE_PROJECT, SCOPE_TEAM
from noust.core.audit import record as audit_record
from noust.core.teams import TeamNotFoundError, TeamStore
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.permissions.access import AccessManager
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)
team_router = APIRouter(route_class=NoustErrorRoute)


class RoleOut(BaseModel):
    """
    A role, built in or defined here.

    Attributes:
        name: Its name.
        label: What the console shows (the name for a built-in role).
        description: What it is for.
        permissions: What it holds.
        built_in: Whether it ships with Noust and cannot be changed.
        kind: ``team`` for the roles of a team, ``custom`` for the others.
        scopes: Where it can be held: ``team``, ``project``, ``environment``,
            ``instance``.
        created_at: When it was defined, for a custom role.
        created_by: Who defined it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    label: str
    description: str = ""
    permissions: list[str]
    built_in: bool
    kind: str
    scopes: list[str] = Field(default_factory=list)
    created_at: float | None = None
    created_by: str | None = None
    updated_at: float | None = None
    updated_by: str | None = None


class RolesResponse(BaseModel):
    """
    Everything a screen needs to offer roles.

    Attributes:
        roles: The built-in team roles and the custom ones.
        permissions: Every permission with its meaning.
        team_permissions: What a role bound to a whole team can hold.
        narrow_permissions: What a role bound to a project or an environment can hold.
    """

    roles: list[RoleOut]
    permissions: dict[str, str]
    team_permissions: list[str]
    narrow_permissions: list[str]


class CreateRoleRequest(BaseModel):
    """Request to define a custom role."""

    name: str = Field(..., description="Its slug, such as release-manager")
    permissions: list[str] = Field(..., description="What it holds")
    label: str | None = Field(default=None, description="What the console shows")
    description: str | None = Field(default=None, description="What it is for")


class UpdateRoleRequest(BaseModel):
    """Request to change a custom role; what is left out stays."""

    permissions: list[str] | None = Field(default=None, description="Its new permissions")
    label: str | None = None
    description: str | None = None


class BindingOut(BaseModel):
    """
    A role given to an account at a scope.

    Attributes:
        id: The binding's id, 0 for a built-in role on a whole team (a membership).
        username: The account.
        role: The role.
        scope_kind: ``instance``, ``team``, ``project`` or ``environment``.
        scope: The scope as people write it, such as ``acme/web/staging``.
        team: The team's slug, for a scope inside one.
        project: The project's slug, for a project or an environment.
        environment: The environment's slug.
        created_at: When it was given, UNIX seconds.
        created_by: Who gave it.
    """

    id: int
    username: str
    role: str
    scope_kind: str
    scope: str
    team: str | None = None
    project: str | None = None
    environment: str | None = None
    created_at: float
    created_by: str | None = None


class BindingsResponse(BaseModel):
    """The bindings asked for."""

    bindings: list[BindingOut]
    total: int


class BindRequest(BaseModel):
    """Request to give an account a role at a scope."""

    username: str = Field(..., description="The account's name")
    role: str = Field(..., description="A built-in team role or a custom role")
    scope: str | None = Field(
        default=None,
        description="instance, team, project or environment; the narrowest the names add up to",
    )
    team: str | None = Field(default=None, description="The team's slug")
    project: str | None = Field(default=None, description="The project's slug")
    environment: str | None = Field(default=None, description="The environment's slug")


class TeamBindRequest(BaseModel):
    """Request to give an account a role on a team, a project or an environment of it."""

    username: str = Field(..., description="The account's name")
    role: str = Field(..., description="A built-in team role or a custom role")
    project: str | None = Field(default=None, description="The project, to bind below the team")
    environment: str | None = Field(default=None, description="The environment (needs project)")


class ActionResponse(BaseModel):
    """The answer to a change that has nothing else to say."""

    message: str


def _out(binding: Binding) -> BindingOut:
    """
    Args:
        binding: A binding.

    Returns:
        It with its scope split into the slugs a screen links to.
    """
    parts = binding.scope.split("/") if binding.scope else []
    return BindingOut(
        id=binding.id,
        username=binding.username,
        role=binding.role,
        scope_kind=binding.scope_kind,
        scope=binding.scope,
        team=parts[0] if parts else None,
        project=parts[1] if len(parts) > 1 else None,
        environment=parts[2] if len(parts) > 2 else None,
        created_at=binding.created_at,
        created_by=binding.created_by,
    )


def _record(session: dict[str, Any], event: str, target: str, **details: Any) -> None:
    """
    Put a change on the audit trail, as whoever made it.

    Args:
        session: The authenticated payload.
        event: The catalog name, such as ``access.bind``.
        target: What it was done to.
        **details: Context, never a credential.
    """
    audit_record(event, actor=actor_of(session), target=target, details=details or None)


def _role_out(raw: dict[str, Any]) -> RoleOut:
    """
    Args:
        raw: A custom role as the model shows it.

    Returns:
        The role for the API.
    """
    return RoleOut(
        name=raw["name"],
        label=raw["label"],
        description=raw["description"],
        permissions=raw["permissions"],
        built_in=False,
        kind="custom",
        scopes=[SCOPE_INSTANCE, SCOPE_TEAM, SCOPE_PROJECT, SCOPE_ENVIRONMENT],
        created_at=raw["created_at"],
        created_by=raw["created_by"],
        updated_at=raw["updated_at"],
        updated_by=raw["updated_by"],
    )


# --------------------------------------------------------------------- roles


@router.get("/roles", response_model=RolesResponse)
def list_roles(session: Annotated[dict, Depends(get_current_session)]) -> RolesResponse:
    """
    List the roles that can be given, and what each permission means.

    Args:
        session: The authenticated session.

    Returns:
        The built-in roles of a team, the custom roles, and the permission catalogue.
    """
    catalog = AccessManager().catalog()
    built_in = [
        RoleOut(
            name=item["name"],
            label=item["name"],
            permissions=item["permissions"],
            built_in=True,
            kind="team",
            scopes=item["scopes"],
        )
        for item in catalog["built_in"]  # type: ignore[attr-defined]
    ]
    custom = [_role_out(item) for item in catalog["custom"]]  # type: ignore[attr-defined]
    return RolesResponse(
        roles=[*built_in, *custom],
        permissions=catalog["permissions"],  # type: ignore[arg-type]
        team_permissions=catalog["team_permissions"],  # type: ignore[arg-type]
        narrow_permissions=catalog["narrow_permissions"],  # type: ignore[arg-type]
    )


@router.post("/roles", response_model=RoleOut, status_code=201)
def create_role(
    body: CreateRoleRequest, session: Annotated[dict, Depends(require_elevated)]
) -> RoleOut:
    """
    Define a custom role.

    Args:
        body: Its name and permissions.
        session: The authenticated, elevated session.

    Returns:
        The role.

    Raises:
        ValidationError: 400 for a name or a permission that is not valid.
        AccessError: 400 when the set governs access and runs the server at once.
        AccessConflictError: 409 when the name is taken.
    """
    role = AccessManager().create_role(
        body.name,
        body.permissions,
        label=body.label,
        description=body.description,
        actor=actor_label(session),
    )
    _record(session, "access.role.create", f"role:{role.name}", permissions=list(role.permissions))
    return _role_out(role.to_dict())


@router.get("/roles/{role}", response_model=RoleOut)
def get_role(role: str, session: Annotated[dict, Depends(get_current_session)]) -> RoleOut:
    """
    Read one custom role.

    Args:
        role: The role's name.
        session: The authenticated session.

    Returns:
        The role.

    Raises:
        AccessNotFoundError: 404 when there is none.
    """
    return _role_out(AccessManager().data.require_role(role).to_dict())


@router.patch("/roles/{role}", response_model=RoleOut)
def update_role(
    role: str, body: UpdateRoleRequest, session: Annotated[dict, Depends(require_elevated)]
) -> RoleOut:
    """
    Change a custom role; every account that holds it holds the change.

    Args:
        role: The role's name.
        body: What changes.
        session: The authenticated, elevated session.

    Returns:
        The role as it is now.

    Raises:
        AccessNotFoundError: 404 when there is none.
        AccessError: 400 when the change would break separation of duties.
    """
    updated = AccessManager().update_role(
        role,
        permissions=body.permissions,
        label=body.label,
        description=body.description,
        actor=actor_label(session),
    )
    _record(
        session, "access.role.update", f"role:{updated.name}", permissions=list(updated.permissions)
    )
    return _role_out(updated.to_dict())


@router.delete("/roles/{role}", response_model=ActionResponse)
def delete_role(role: str, session: Annotated[dict, Depends(require_elevated)]) -> ActionResponse:
    """
    Remove a custom role nobody holds.

    Args:
        role: The role's name.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        AccessNotFoundError: 404 when there is none.
        AccessConflictError: 409 while an account holds it.
    """
    removed = AccessManager().delete_role(role)
    _record(session, "access.role.delete", f"role:{removed.name}")
    return ActionResponse(message=f"Removed the role {removed.name}")


# ------------------------------------------------------------------- bindings


@router.get("/bindings", response_model=BindingsResponse)
def list_bindings(
    session: Annotated[dict, Depends(get_current_session)],
    username: str | None = Query(default=None, max_length=64),
) -> BindingsResponse:
    """
    List every binding on the server.

    Args:
        session: The authenticated session.
        username: Only this account's.

    Returns:
        The bindings.
    """
    found = [_out(b) for b in AccessManager().list_bindings(username)]
    return BindingsResponse(bindings=found, total=len(found))


@router.post("/bindings", response_model=BindingOut, status_code=201)
def bind(body: BindRequest, session: Annotated[dict, Depends(require_elevated)]) -> BindingOut:
    """
    Give an account a role at any scope, the whole server included.

    Args:
        body: The account, the role and the scope.
        session: The authenticated, elevated session.

    Returns:
        The binding.

    Raises:
        AccessNotFoundError: 404 for an account or a role that does not exist.
        AccessError: 400 when separation of duties, the ENS profile, or an
            instance-only account refuses it.
    """
    binding = AccessManager().bind(
        body.username,
        body.role,
        scope=body.scope,
        team=body.team,
        project=body.project,
        environment=body.environment,
        actor=actor_label(session),
    )
    _record(
        session,
        "access.bind",
        f"account:{binding.username}",
        role=binding.role,
        scope=binding.scope_kind,
        where=binding.scope,
    )
    return _out(binding)


@router.delete("/bindings/{binding_id}", response_model=ActionResponse)
def unbind(binding_id: int, session: Annotated[dict, Depends(require_elevated)]) -> ActionResponse:
    """
    Take a role away.

    Args:
        binding_id: The binding's id.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        AccessNotFoundError: 404 when there is none.
    """
    binding = AccessManager().unbind(binding_id)
    _record(
        session,
        "access.unbind",
        f"account:{binding.username}",
        role=binding.role,
        scope=binding.scope_kind,
        where=binding.scope,
    )
    return ActionResponse(message=f"Took {binding.role} from {binding.username}")


# ------------------------------------------------------------------ one team


@team_router.get("/{team}/access", response_model=BindingsResponse)
def list_team_access(
    team: str, session: Annotated[dict, Depends(get_current_session)]
) -> BindingsResponse:
    """
    List who holds a role on a team, on its projects and on its environments.

    Args:
        team: The team's slug.
        session: The authenticated session.

    Returns:
        The bindings, not the team's built-in memberships (those are its members).
    """
    found = [_out(b) for b in AccessManager().bindings_in_team(team)]
    return BindingsResponse(bindings=found, total=len(found))


@team_router.post("/{team}/access", response_model=BindingOut, status_code=201)
def bind_in_team(
    team: str, body: TeamBindRequest, session: Annotated[dict, Depends(require_elevated)]
) -> BindingOut:
    """
    Give an account a role on a team, one of its projects or one of its environments.

    Args:
        team: The team's slug.
        body: The account, the role and, below the team, the project and environment.
        session: The authenticated, elevated session.

    Returns:
        The binding.

    Raises:
        TeamNotFoundError: 404 when the project or the environment is not in the team.
        AccessNotFoundError: 404 for an account or a role that does not exist.
        AccessError: 400 when separation of duties or an instance-only account refuses it.
    """
    manager = AccessManager()
    binding = manager.bind(
        body.username,
        body.role,
        team=team,
        project=body.project,
        environment=body.environment,
        actor=actor_label(session),
    )
    _record(
        session,
        "access.bind",
        f"account:{binding.username}",
        role=binding.role,
        scope=binding.scope_kind,
        where=binding.scope,
    )
    return _out(binding)


@team_router.delete("/{team}/access/{binding_id}", response_model=ActionResponse)
def unbind_in_team(
    team: str, binding_id: int, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Take away a role given on a team, a project or an environment of it.

    Args:
        team: The team's slug.
        binding_id: The binding's id.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        AccessNotFoundError: 404 when there is no such binding in this team.
    """
    manager = AccessManager()
    found = manager.data.require_binding(binding_id)
    owner = TeamStore().require_team(team)
    if manager.team_of_binding(found) != owner.id:
        # Another team's binding reads as one that does not exist.
        raise TeamNotFoundError(
            f"No access binding with id {binding_id} in {owner.slug}",
            details="List them with GET /api/teams/{team}/access.",
            field="binding",
        )
    manager.unbind(binding_id)
    _record(
        session,
        "access.unbind",
        f"account:{found.username}",
        role=found.role,
        scope=found.scope_kind,
        where=found.scope,
    )
    return ActionResponse(message=f"Took {found.role} from {found.username}")
