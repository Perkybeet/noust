# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The server's roles over HTTP: one list, edited like any other (4.1, item 96).

A client of :class:`~noust.core.access.role_manager.RoleManager`, like ``noust access role``:
every rule (the owner is protected, a role Noust ships is edited and put back and never deleted,
what a role may hold where a framework keeps governing and running apart) is the manager's, not
repeated here. This module holds the caller to its permission, records what changed, and speaks
the shapes the console draws.

- ``GET /api/roles`` is the list, with who holds each role, what it joins, what a newer release
  offers it, and which of the actions it admits.
- ``POST`` defines a role, ``PATCH`` changes one (a role Noust ships too), ``DELETE`` removes one of
  the operator's own that nobody holds, ``POST .../duplicate`` copies one, ``POST .../reset`` puts
  one Noust ships back to how it comes and ``POST .../offers`` takes or leaves what a newer
  release offers a role the operator edited.
- ``GET /api/roles/review`` is what the frameworks in force make of the roles.

Reading is ``accounts.read``; changing is ``accounts.manage`` in sudo mode. Every refusal carries a
code (``coded``) the console translates.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.access.role_manager import RoleManager, RoleView
from noust.core.accounts.role_review import review as review_roles
from noust.core.audit import record as audit_record
from noust.core.permission_catalog import ALL_PERMISSIONS, DESCRIPTIONS
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)


class ServerRoleOut(BaseModel):
    """
    One role of the list.

    Attributes:
        name: Its slug; the key everything refers to it by.
        kind: ``seeded`` (one of the five Noust ships), ``protected`` (the owner) or ``custom``.
        label: What the console shows. For a role Noust ships and the operator has not
            renamed, the console shows its own translation of the name.
        description: What it is for.
        permissions: What it holds now.
        shipped_permissions: What the running release ships for it, to show how it differs; null
            for a role of the operator's own.
        edited: Whether the operator has taken a role Noust ships over.
        follows_noust: Whether a permission a newer release adds reaches it by itself.
        protected: The owner: it is neither edited nor deleted.
        offers: Permissions a newer release ships for an edited role that it does not hold yet.
        holders: The accounts that hold it.
        holder_count: How many.
        bindings: How many bindings at a team, a project or an environment name it.
        joins_duties: Whether it holds both governing the server and running it.
        governing: The permissions that govern, when it joins them.
        running: The permissions that run the server, when it joins them.
        can_edit: Whether ``PATCH`` is admitted.
        can_delete: Whether ``DELETE`` is admitted (while nobody holds it).
        can_reset: Whether ``POST .../reset`` is admitted.
        can_duplicate: Whether ``POST .../duplicate`` is admitted.
        created_at: When it was made, UNIX seconds.
        created_by: Who made it.
        updated_at: When it last changed.
        updated_by: Who changed it.
    """

    name: str
    kind: str
    label: str
    description: str = ""
    permissions: list[str]
    shipped_permissions: list[str] | None = None
    edited: bool
    follows_noust: bool
    protected: bool
    offers: list[str] = Field(default_factory=list)
    holders: list[str] = Field(default_factory=list)
    holder_count: int = 0
    bindings: int = 0
    joins_duties: bool = False
    governing: list[str] = Field(default_factory=list)
    running: list[str] = Field(default_factory=list)
    can_edit: bool
    can_delete: bool
    can_reset: bool
    can_duplicate: bool
    created_at: float
    created_by: str | None = None
    updated_at: float
    updated_by: str | None = None

    @classmethod
    def of(cls, view: RoleView) -> ServerRoleOut:
        """
        Args:
            view: A role with what goes beside it.

        Returns:
            It for the API.
        """
        data: dict[str, Any] = view.to_dict()
        shipped = view.role.shipped
        return cls(
            **{name: data[name] for name in _ROLE_FIELDS},
            shipped_permissions=sorted(shipped) if shipped is not None else None,
        )


#: The fields of :class:`ServerRoleOut` read from ``RoleView.to_dict()``, spelled out because the
#: model's own field list is named differently in pydantic 1 and 2.
_ROLE_FIELDS = (
    "name",
    "kind",
    "label",
    "description",
    "permissions",
    "edited",
    "follows_noust",
    "protected",
    "offers",
    "holders",
    "holder_count",
    "bindings",
    "joins_duties",
    "governing",
    "running",
    "can_edit",
    "can_delete",
    "can_reset",
    "can_duplicate",
    "created_at",
    "created_by",
    "updated_at",
    "updated_by",
)


class ServerRolesResponse(BaseModel):
    """
    The list of roles, and what a screen needs beside it.

    Attributes:
        roles: Every role: the ones Noust ships and the owner first, then the operator's by name.
        permissions: Every permission there is with what it allows.
        owners: How many accounts hold the owner role and can sign in.
        duties_apart: Whether a framework (or the operator's own setting) keeps governing the
            server apart from running it, which is what makes ``joins_duties`` matter.
    """

    roles: list[ServerRoleOut]
    permissions: dict[str, str]
    owners: int
    duties_apart: bool


class ServerRoleCreate(BaseModel):
    """Request to define a role of one's own."""

    name: str = Field(..., description="Its slug, such as release-manager")
    permissions: list[str] = Field(..., description="What it holds")
    label: str | None = Field(default=None, description="What the console shows")
    description: str | None = Field(default=None, description="What it is for")


class ServerRoleUpdate(BaseModel):
    """Request to change a role; what is left out stays."""

    permissions: list[str] | None = Field(default=None, description="Its new permissions")
    label: str | None = None
    description: str | None = None


class ServerRoleDuplicate(BaseModel):
    """Request to copy a role into a role of one's own."""

    name: str = Field(..., description="The copy's slug")
    label: str | None = Field(default=None, description="Its label; the original's by default")


class ServerRoleOffers(BaseModel):
    """Request to take or leave the permissions a newer release offers a role."""

    accept: list[str] = Field(default_factory=list, description="Offered permissions to add")
    dismiss: list[str] = Field(
        default_factory=list, description="Offered permissions to leave out, for good"
    )


class RolesReviewRole(BaseModel):
    """
    One role as the frameworks judge it.

    Attributes:
        name: The role.
        protected: The owner, which holds everything by design and is not counted as a defect.
        holders: How many accounts hold it.
        joins_duties: Whether it joins governing the server with running it.
        governing: What it holds that governs, when it joins them.
        running: What it holds that runs, when it joins them.
    """

    name: str
    protected: bool
    holders: int
    joins_duties: bool
    governing: list[str] = Field(default_factory=list)
    running: list[str] = Field(default_factory=list)


class RolesReviewAccount(BaseModel):
    """
    An account whose roles together join the duties, though none of them does alone.

    Attributes:
        username: The account.
        roles: Its roles.
        governing: What it holds that governs.
        running: What it holds that runs.
    """

    username: str
    roles: list[str]
    governing: list[str]
    running: list[str]


class RolesReviewResponse(BaseModel):
    """
    What the frameworks in force make of the server's roles.

    Attributes:
        duties_apart: Whether a control keeps governing and running apart.
        controls: Which controls do (``roles_split``, ``separation_of_duties``,
            ``single_role_per_account``).
        owners: Accounts that hold the owner role and can sign in.
        suggestion: A command that splits the administrator the way the ENS review decided it,
            when the administrator joins the duties; null otherwise.
        roles: Every role, judged.
        accounts: The accounts whose roles together join the duties.
    """

    duties_apart: bool
    controls: list[str]
    owners: int
    suggestion: str | None = None
    roles: list[RolesReviewRole]
    accounts: list[RolesReviewAccount]


class RoleActionResponse(BaseModel):
    """The answer to a change that has nothing else to say."""

    message: str


def _view(name: str) -> ServerRoleOut:
    """
    Args:
        name: A role.

    Returns:
        It with its holders and what it joins.
    """
    return ServerRoleOut.of(RoleManager().view(name))


def _record(session: dict[str, Any], event: str, target: str, **details: Any) -> None:
    """
    Put a change on the audit trail, as whoever made it.

    Args:
        session: The authenticated payload.
        event: The catalog name, such as ``access.role.reset``.
        target: What it was done to.
        **details: Context, never a credential.
    """
    audit_record(event, actor=actor_of(session), target=target, details=details or None)


@router.get("", response_model=ServerRolesResponse)
def list_roles(session: Annotated[dict, Depends(get_current_session)]) -> ServerRolesResponse:
    """
    List the server's roles.

    Args:
        session: The authenticated session.

    Returns:
        The roles with who holds each, what each joins and what is on offer, every permission
        with its meaning, and how many owners the server has.
    """
    manager = RoleManager()
    judged = review_roles()
    return ServerRolesResponse(
        roles=[ServerRoleOut.of(view) for view in manager.views()],
        permissions={name: DESCRIPTIONS.get(name, "") for name in sorted(ALL_PERMISSIONS)},
        owners=judged.owners,
        duties_apart=judged.duties_apart,
    )


@router.get("/review", response_model=RolesReviewResponse)
def review(session: Annotated[dict, Depends(get_current_session)]) -> RolesReviewResponse:
    """
    Say what the frameworks in force make of the roles.

    Args:
        session: The authenticated session.

    Returns:
        Which roles join governing the server with running it, which accounts do through
        several, and how many owners the server has.
    """
    return RolesReviewResponse(**review_roles().to_dict())


@router.post("", response_model=ServerRoleOut, status_code=201)
def create_role(
    body: ServerRoleCreate, session: Annotated[dict, Depends(require_elevated)]
) -> ServerRoleOut:
    """
    Define a role of one's own.

    Args:
        body: Its name and permissions.
        session: The authenticated, elevated session.

    Returns:
        The role.

    Raises:
        ValidationError: 400 (``roles.name_invalid``, ``roles.name_system``,
            ``roles.permissions_unknown``, ``roles.permissions_none``) for a name or a permission
            that is not valid.
        AccessError: 400 (``roles.separation_role``) when the set joins governing with running
            where a framework forbids it.
        AccessConflictError: 409 (``roles.name_taken``, ``roles.too_many``).
    """
    role = RoleManager().create(
        body.name,
        body.permissions,
        label=body.label,
        description=body.description,
        actor=actor_label(session),
    )
    _record(session, "access.role.create", f"role:{role.name}", permissions=list(role.permissions))
    return _view(role.name)


@router.get("/{name}", response_model=ServerRoleOut)
def get_role(name: str, session: Annotated[dict, Depends(get_current_session)]) -> ServerRoleOut:
    """
    Read one role.

    Args:
        name: The role's name.
        session: The authenticated session.

    Returns:
        The role.

    Raises:
        AccessNotFoundError: 404 (``roles.unknown``) when there is none.
    """
    return _view(name)


@router.patch("/{name}", response_model=ServerRoleOut)
def update_role(
    name: str, body: ServerRoleUpdate, session: Annotated[dict, Depends(require_elevated)]
) -> ServerRoleOut:
    """
    Change a role, one Noust ships included; every account that holds it holds the change.

    Args:
        name: The role's name.
        body: What changes.
        session: The authenticated, elevated session.

    Returns:
        The role as it is now.

    Raises:
        AccessNotFoundError: 404 (``roles.unknown``) when there is none.
        AccessError: 400 (``roles.owner_protected``) for the owner; ``roles.separation_role`` or
            ``roles.separation_account`` when the change would join duties for the role or for
            an account that holds it.
    """
    updated = RoleManager().update(
        name,
        permissions=body.permissions,
        label=body.label,
        description=body.description,
        actor=actor_label(session),
    )
    _record(
        session, "access.role.update", f"role:{updated.name}", permissions=list(updated.permissions)
    )
    return _view(updated.name)


@router.delete("/{name}", response_model=RoleActionResponse)
def delete_role(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> RoleActionResponse:
    """
    Remove a role of one's own that nobody holds.

    Args:
        name: The role's name.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        AccessNotFoundError: 404 (``roles.unknown``) when there is none.
        AccessError: 400 (``roles.owner_protected``, ``roles.seeded_kept``) for the owner and for
            a role Noust ships, which are put back, not deleted.
        AccessConflictError: 409 (``roles.in_use``) while an account holds it or a binding names it.
    """
    removed = RoleManager().delete(name)
    _record(session, "access.role.delete", f"role:{removed.name}")
    return RoleActionResponse(message=f"Removed the role {removed.name}")


@router.post("/{name}/duplicate", response_model=ServerRoleOut, status_code=201)
def duplicate_role(
    name: str, body: ServerRoleDuplicate, session: Annotated[dict, Depends(require_elevated)]
) -> ServerRoleOut:
    """
    Copy a role into a role of one's own.

    Args:
        name: The role to copy; any, the owner included.
        body: The copy's name and, optionally, its label.
        session: The authenticated, elevated session.

    Returns:
        The copy.

    Raises:
        AccessNotFoundError: 404 (``roles.unknown``) when there is no such role.
        AccessConflictError: 409 (``roles.name_taken``, ``roles.too_many``).
    """
    copy = RoleManager().duplicate(name, body.name, label=body.label, actor=actor_label(session))
    _record(session, "access.role.duplicate", f"role:{copy.name}", copied_from=name)
    return _view(copy.name)


@router.post("/{name}/reset", response_model=ServerRoleOut)
def reset_role(name: str, session: Annotated[dict, Depends(require_elevated)]) -> ServerRoleOut:
    """
    Put a role Noust ships back to how it comes: it follows Noust again.

    Args:
        name: The role's name.
        session: The authenticated, elevated session.

    Returns:
        The role as it is now.

    Raises:
        AccessNotFoundError: 404 (``roles.unknown``) when there is none.
        AccessError: 400 (``roles.owner_protected``, ``roles.reset_custom``) for the owner and
            for a role of the operator's own; ``roles.separation_account`` when putting it back
            would join duties for an account that holds it.
    """
    back = RoleManager().reset(name, actor=actor_label(session))
    _record(session, "access.role.reset", f"role:{back.name}")
    return _view(back.name)


@router.post("/{name}/offers", response_model=ServerRoleOut)
def settle_offers(
    name: str, body: ServerRoleOffers, session: Annotated[dict, Depends(require_elevated)]
) -> ServerRoleOut:
    """
    Take or leave the permissions a newer release ships for a role the operator edited.

    Args:
        name: The role's name.
        body: The offered permissions to add and the ones to leave out for good.
        session: The authenticated, elevated session.

    Returns:
        The role as it is now.

    Raises:
        AccessNotFoundError: 404 (``roles.unknown``) when there is none.
        AccessError: 400 (``roles.offer_unknown``) for a permission that is not on offer;
            ``roles.separation_role`` or ``roles.separation_account`` when taking them would join
            duties.
    """
    settled = RoleManager().settle_offers(
        name, accept=body.accept, dismiss=body.dismiss, actor=actor_label(session)
    )
    _record(
        session,
        "access.role.offer",
        f"role:{settled.name}",
        accepted=sorted(body.accept),
        dismissed=sorted(body.dismiss),
    )
    return _view(settled.name)
