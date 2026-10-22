# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/auth/scim``: configure the identity providers that provision accounts here.

The administrator's side of SCIM (:mod:`noust.web.api.scim` is the provider's): which
sources there are, what their accounts start as, and their bearer tokens. A token is
shown once, when it is issued; only its last characters are shown afterwards. Every change
is the security officer's, in sudo mode, because a source decides who may have an account.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core.accounts.scim import SETTINGS, ScimManager
from noust.core.accounts.scim_store import ScimSource
from noust.core.exceptions import ValidationError
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.api.federation_models import Mapping
from noust.web.api.scim import BASE_PATH
from noust.web.api.sso_flow import origin_of
from noust.web.auth import actor_label, audit_event, get_client_ip, require_auth
from noust.web.pydantic_compat import dump_model
from noust.web.server import get_token_manager

router = APIRouter(route_class=NoustErrorRoute)


def manager() -> ScimManager:
    """
    Returns:
        The SCIM manager, over the console's own account manager.
    """
    accounts = get_token_manager().accounts
    return ScimManager(accounts.store, accounts=accounts)


class ScimSourceInfo(BaseModel):
    """
    A source of provisioning, without its token.

    Attributes:
        name: Its short name.
        label: What a person reads about it.
        enabled: Whether its token is accepted.
        token_hint: The last characters of its token, to tell tokens apart.
        token_created_at: When the token was issued.
        last_used_at: When it last authenticated a request.
        last_used_ip: From where.
        default_role: The role of an account it creates.
        default_team: The team a new account joins.
        default_team_role: Its role there.
        link_by_email: Attach a user to the account that already has their e-mail.
        allowed_domains: Only these e-mail domains; any when empty.
        group_sync: Whether its groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
        users: How many people it has provisioned.
        created_at: When it was configured.
        created_by: Who configured it.
    """

    name: str
    label: str
    enabled: bool
    token_hint: str
    token_created_at: float
    last_used_at: float | None = None
    last_used_ip: str | None = None
    default_role: str
    default_team: str | None = None
    default_team_role: str | None = None
    link_by_email: bool
    allowed_domains: list[str]
    group_sync: bool
    group_mappings: list[Mapping]
    users: int
    created_at: float
    created_by: str | None = None

    @classmethod
    def of(cls, source: ScimSource, users: int) -> ScimSourceInfo:
        """
        Args:
            source: A source.
            users: How many people it provisioned.

        Returns:
            Its public description.
        """
        shown = source.to_dict()
        keep = {key: shown[key] for key in cls.__annotations__ if key in shown}
        return cls(**keep, users=users)


class ScimSourcesResponse(BaseModel):
    """
    The sources, and where they are to be pointed.

    Attributes:
        base_url: The address to give each identity provider as the SCIM endpoint.
        sources: Every configured source.
    """

    base_url: str
    sources: list[ScimSourceInfo]


class ScimSourceSettings(BaseModel):
    """
    The settings of a source to set; omitted ones are left as they are.

    Attributes:
        label: What a person reads about it.
        enabled: Whether its token is accepted.
        default_role: ``viewer``, ``operator`` or ``admin``.
        default_team: The team a created account joins.
        default_team_role: Its role there.
        link_by_email: Attach a user to the account of the same e-mail.
        allowed_domains: Only these e-mail domains.
        group_sync: Let its groups decide roles, memberships and scoped roles.
        group_mappings: What each group means.
        rotate_token: Issue a new token (the old one stops working at once).
    """

    label: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None
    default_role: str | None = Field(default=None, max_length=16)
    default_team: str | None = Field(default=None, max_length=64)
    default_team_role: str | None = Field(default=None, max_length=16)
    link_by_email: bool | None = None
    allowed_domains: list[str] | None = None
    group_sync: bool | None = None
    group_mappings: list[Mapping] | None = None
    rotate_token: bool = False


class ScimSourceSaved(BaseModel):
    """
    A source after it was set, and its token when one was issued.

    Attributes:
        source: The source as it is now.
        token: The bearer token, in clear, exactly once; None when the source kept its own.
        base_url: The SCIM endpoint to give the identity provider.
    """

    source: ScimSourceInfo
    token: str | None = None
    base_url: str


class ScimRemoved(BaseModel):
    """
    A confirmation that a source was removed.

    Attributes:
        success: Always true.
        removed: Its name.
    """

    success: bool
    removed: str


class ScimUserInfo(BaseModel):
    """
    A person a source provisioned.

    Attributes:
        id: The id the provider uses.
        user_name: Its ``userName``.
        account: The account's name.
        status: The account's status.
        role: The account's role.
        disabled_by_scim: Whether the provider switched it off.
        groups: The groups it pushed them into.
    """

    id: str
    user_name: str
    account: str
    status: str
    role: str
    disabled_by_scim: bool
    groups: list[str]


class ScimUsersResponse(BaseModel):
    """
    The roster of a source.

    Attributes:
        total: How many people it provisioned.
        users: The first 500.
    """

    total: int
    users: list[ScimUserInfo]


@router.get("/sources", response_model=ScimSourcesResponse)
def list_sources(
    request: Request, _session: dict[str, Any] = Depends(require_auth)
) -> ScimSourcesResponse:
    """
    List the sources of provisioning, without their tokens.

    Args:
        request: The request, for the address to point providers at.
        _session: The authenticated payload.

    Returns:
        The sources and the SCIM endpoint.
    """
    scim = manager()
    return ScimSourcesResponse(
        base_url=origin_of(request) + BASE_PATH,
        sources=[
            ScimSourceInfo.of(source, scim.data.count_users(source.name))
            for source in scim.sources()
        ],
    )


@router.put("/sources/{name}", response_model=ScimSourceSaved)
def put_source(
    name: str,
    request: Request,
    body: ScimSourceSettings,
    session: dict[str, Any] = Depends(require_elevated),
) -> ScimSourceSaved:
    """
    Add a source or change one, in sudo mode: it decides who may have an account.

    Args:
        name: The source's name.
        request: The request.
        body: The settings to set.
        session: The authenticated payload, in sudo mode.

    Returns:
        The source as it is now and, when one was issued, its token (the only time).

    Raises:
        ValidationError: When a setting is refused or names something that does not exist;
            nothing is written then.
    """
    given = dump_model(body)
    rotate = bool(given.pop("rotate_token"))
    changes = {key: value for key, value in given.items() if value is not None and key in SETTINGS}
    if body.group_mappings is not None:
        changes["group_mappings"] = [
            {key: value for key, value in item.items() if value} for item in given["group_mappings"]
        ]
    scim = manager()
    existed = scim.source(name) is not None
    saved, token = scim.configure(name, changes, rotate_token=rotate, actor=actor_label(session))
    audit_event(
        "auth.scim.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"source:{saved.name}",
        detail=(
            ("changed " if existed else "added ")
            + ", ".join(sorted([*changes, *(["token"] if token else [])]))
        ),
    )
    return ScimSourceSaved(
        source=ScimSourceInfo.of(saved, scim.data.count_users(saved.name)),
        token=token,
        base_url=origin_of(request) + BASE_PATH,
    )


@router.delete("/sources/{name}", response_model=ScimRemoved)
def delete_source(
    name: str, request: Request, session: dict[str, Any] = Depends(require_elevated)
) -> ScimRemoved:
    """
    Remove a source and its roster, in sudo mode. The accounts it made stay as they are.

    Args:
        name: The source's name.
        request: The request.
        session: The authenticated payload, in sudo mode.

    Returns:
        A confirmation.

    Raises:
        HTTPException: 404 when there is no such source.
    """
    try:
        removed = manager().remove(name)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    audit_event(
        "auth.scim.config",
        "success",
        client_ip=get_client_ip(request),
        session=session,
        target=f"source:{removed.name}",
        detail="removed",
    )
    return ScimRemoved(success=True, removed=removed.name)


@router.get("/sources/{name}/users", response_model=ScimUsersResponse)
def list_users(name: str, _session: dict[str, Any] = Depends(require_auth)) -> ScimUsersResponse:
    """
    List the people a source provisioned, with their account's state.

    Args:
        name: The source's name.
        _session: The authenticated payload.

    Returns:
        The roster, the first 500.

    Raises:
        HTTPException: 404 when there is no such source.
    """
    scim = manager()
    try:
        source = scim.require_source(name)
    except ValidationError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    total, users = scim.data.list_users(source.name, limit=500)
    found: list[ScimUserInfo] = []
    for user in users:
        account = scim.accounts.get(user.account_id)
        if account is None:
            continue
        found.append(
            ScimUserInfo(
                id=user.id,
                user_name=user.user_name,
                account=account.username,
                status=account.status,
                role=account.role,
                disabled_by_scim=user.disabled_by_scim,
                groups=[group.display_name for group in scim.data.groups_of(user.id)],
            )
        )
    return ScimUsersResponse(total=total, users=found)
