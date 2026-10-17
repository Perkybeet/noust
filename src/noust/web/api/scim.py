# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/scim/v2``: the SCIM 2.0 endpoint an identity provider provisions through.

An endpoint translates HTTP to :class:`~noust.core.accounts.scim.ScimService` and back
(rule 3); what a call does is decided there. The caller is not a session or an API token but
a *source*: it presents the bearer token issued for it (``noust auth scim set``), and the
routes are public to the permission layer for that reason, the token being checked here, once,
by :func:`scim_source`. A token that is unknown or whose source is switched off is refused
as any guessed credential is: counted against the address, which a lockout then refuses.

Answers use ``application/scim+json`` and the protocol's own error body, because the
providers that call this read ``scimType`` and the status, not Noust's error contract.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response

from noust.core.accounts.scim import (
    SCHEMA_GROUP,
    SCHEMA_USER,
    ScimError,
    ScimManager,
    ScimService,
    hash_token,
)
from noust.core.accounts.scim_store import ScimSource
from noust.web.api.sso_flow import origin_of
from noust.web.auth import audit_event, bearer_token, get_client_ip, record_auth_failure
from noust.web.server import get_token_manager

router = APIRouter()

MEDIA_TYPE = "application/scim+json"
BASE_PATH = "/api/scim/v2"

#: A source's "last used" is written at most this often: a provider's sync is a burst.
_TOUCH_EVERY = 60.0

F = TypeVar("F", bound=Callable[..., Any])


def manager() -> ScimManager:
    """
    Returns:
        The SCIM manager, over the console's own account manager.
    """
    return ScimManager(get_token_manager().accounts.store, accounts=get_token_manager().accounts)


def reply(body: Any, status: int = 200, headers: dict[str, str] | None = None) -> Response:
    """
    Args:
        body: A JSON document.
        status: The HTTP status.
        headers: Extra headers.

    Returns:
        The answer, as ``application/scim+json`` and out of every cache.
    """
    response = Response(content=json.dumps(body), status_code=status, media_type=MEDIA_TYPE)
    response.headers["Cache-Control"] = "no-store"
    for key, value in (headers or {}).items():
        response.headers[key] = value
    return response


def scim_endpoint(handler: F) -> F:
    """
    Answer a :class:`~noust.core.accounts.scim.ScimError` in the protocol's own error body.

    Args:
        handler: A route handler.

    Returns:
        The handler, wrapped.
    """

    @wraps(handler)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        try:
            return handler(*args, **kwargs)
        except ScimError as exc:
            return reply(exc.to_dict(), exc.status)

    return wrapped  # type: ignore[return-value]


def scim_source(request: Request) -> ScimSource:
    """
    Authenticate the provider: the source whose bearer token this is.

    Args:
        request: The request.

    Returns:
        The source.

    Raises:
        HTTPException: 401 for a token that is absent, unknown or belongs to a source that is
            switched off. The provider is not told which; the audit trail is.
    """
    client_ip = get_client_ip(request)
    scim = manager()
    token = bearer_token(request)
    source = scim.authenticate(token)
    if source is None:
        known = scim.data.source_by_token(hash_token(token or ""))
        audit_event(
            "auth.scim.denied",
            "failure",
            client_ip=client_ip,
            target="/api/scim/v2",
            detail=(
                "token of a source that is switched off"
                if known is not None
                else ("no bearer token" if token is None else "unknown token")
            ),
        )
        record_auth_failure(client_ip, request.url.path, "scim")
        raise HTTPException(
            status_code=401,
            detail="Authentication failed",
            headers={"WWW-Authenticate": 'Bearer realm="noust-scim"'},
        )
    scim.data.touch_source(source.name, client_ip, min_interval=_TOUCH_EVERY)
    return source


def service_for(request: Request, source: ScimSource) -> ScimService:
    """
    Args:
        request: The request.
        source: The authenticated source.

    Returns:
        The service its calls are answered by, locations pointing at where it was reached.
    """
    return manager().service(
        source, base_url=origin_of(request) + BASE_PATH, client_ip=get_client_ip(request)
    )


def finish(service: ScimService) -> None:
    """
    End the sessions and API tokens of every account the call switched off.

    Args:
        service: The service the call went through.
    """
    token_manager = get_token_manager()
    for account_id in service.ended:
        token_manager.end_account_access(account_id)


# ---------------------------------------------------------------- discovery

_RESOURCE_SCHEMAS = [
    {
        "id": SCHEMA_USER,
        "name": "User",
        "description": "A person who may have an account here.",
        "attributes": [
            {"name": "userName", "type": "string", "required": True, "mutability": "readWrite"},
            {"name": "externalId", "type": "string", "required": False},
            {"name": "displayName", "type": "string", "required": False},
            {"name": "emails", "type": "complex", "multiValued": True, "required": True},
            {"name": "active", "type": "boolean", "required": False},
        ],
    },
    {
        "id": SCHEMA_GROUP,
        "name": "Group",
        "description": "A group whose members get the roles the source's mapping says.",
        "attributes": [
            {"name": "displayName", "type": "string", "required": True},
            {"name": "members", "type": "complex", "multiValued": True, "required": False},
        ],
    },
]


@router.get("/ServiceProviderConfig")
@scim_endpoint
def service_provider_config(_source: ScimSource = Depends(scim_source)) -> Response:
    """
    Say what this server supports, as providers ask before they provision.

    Args:
        _source: The authenticated source.

    Returns:
        The configuration: PATCH, filtering by equality, no bulk, no sorting, no passwords.
    """
    return reply(
        {
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
            "patch": {"supported": True},
            "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
            "filter": {"supported": True, "maxResults": 200},
            "changePassword": {"supported": False},
            "sort": {"supported": False},
            "etag": {"supported": False},
            "authenticationSchemes": [
                {
                    "type": "oauthbearertoken",
                    "name": "Bearer token",
                    "description": "The token issued for this source by noust auth scim set.",
                    "primary": True,
                }
            ],
        }
    )


@router.get("/ResourceTypes")
@scim_endpoint
def resource_types(_source: ScimSource = Depends(scim_source)) -> Response:
    """
    Args:
        _source: The authenticated source.

    Returns:
        The two resource types: User and Group.
    """
    return reply(
        [
            {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"],
                "id": name,
                "name": name,
                "endpoint": f"/{name}s",
                "schema": schema,
            }
            for name, schema in (("User", SCHEMA_USER), ("Group", SCHEMA_GROUP))
        ]
    )


@router.get("/Schemas")
@scim_endpoint
def schemas(_source: ScimSource = Depends(scim_source)) -> Response:
    """
    Args:
        _source: The authenticated source.

    Returns:
        The attributes this server models.
    """
    return reply(_RESOURCE_SCHEMAS)


# -------------------------------------------------------------------- users


@router.get("/Users")
@scim_endpoint
def list_users(
    request: Request,
    source: ScimSource = Depends(scim_source),
    filter: str | None = Query(default=None, max_length=512),
    startIndex: int | None = Query(default=None, ge=1, le=1_000_000),
    count: int | None = Query(default=None, ge=0, le=10_000),
) -> Response:
    """
    List the users this source provisioned, or find one by ``userName`` or ``externalId``.

    Args:
        request: The request.
        source: The authenticated source.
        filter: ``attribute eq "value"``.
        startIndex: The 1-based index of the first result.
        count: How many, at most 200.

    Returns:
        A ListResponse.
    """
    return reply(service_for(request, source).list_users(filter, startIndex, count))


@router.post("/Users")
@scim_endpoint
def create_user(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Provision a person.

    Args:
        request: The request.
        source: The authenticated source.
        body: The user document.

    Returns:
        201 with the user and its ``Location``.
    """
    service = service_for(request, source)
    created = service.create_user(body)
    finish(service)
    return reply(created, 201, {"Location": created["meta"]["location"]})


@router.get("/Users/{user_id}")
@scim_endpoint
def get_user(user_id: str, request: Request, source: ScimSource = Depends(scim_source)) -> Response:
    """
    Args:
        user_id: The id the provider uses.
        request: The request.
        source: The authenticated source.

    Returns:
        The user.
    """
    return reply(service_for(request, source).get_user(user_id))


@router.put("/Users/{user_id}")
@scim_endpoint
def replace_user(
    user_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Args:
        user_id: The id the provider uses.
        request: The request.
        source: The authenticated source.
        body: The whole user document.

    Returns:
        The user after.
    """
    service = service_for(request, source)
    changed = service.replace_user(user_id, body)
    finish(service)
    return reply(changed)


@router.patch("/Users/{user_id}")
@scim_endpoint
def patch_user(
    user_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Args:
        user_id: The id the provider uses.
        request: The request.
        source: The authenticated source.
        body: A PatchOp.

    Returns:
        The user after.
    """
    service = service_for(request, source)
    changed = service.patch_user(user_id, body)
    finish(service)
    return reply(changed)


@router.delete("/Users/{user_id}")
@scim_endpoint
def delete_user(
    user_id: str, request: Request, source: ScimSource = Depends(scim_source)
) -> Response:
    """
    Switch the person's account off and forget the entry.

    Args:
        user_id: The id the provider uses.
        request: The request.
        source: The authenticated source.

    Returns:
        204.
    """
    service = service_for(request, source)
    service.delete_user(user_id)
    finish(service)
    return Response(status_code=204)


# ------------------------------------------------------------------- groups


@router.get("/Groups")
@scim_endpoint
def list_groups(
    request: Request,
    source: ScimSource = Depends(scim_source),
    filter: str | None = Query(default=None, max_length=512),
    startIndex: int | None = Query(default=None, ge=1, le=1_000_000),
    count: int | None = Query(default=None, ge=0, le=10_000),
) -> Response:
    """
    Args:
        request: The request.
        source: The authenticated source.
        filter: ``attribute eq "value"``.
        startIndex: The 1-based index of the first result.
        count: How many, at most 200.

    Returns:
        A ListResponse.
    """
    return reply(service_for(request, source).list_groups(filter, startIndex, count))


@router.post("/Groups")
@scim_endpoint
def create_group(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Args:
        request: The request.
        source: The authenticated source.
        body: The group document.

    Returns:
        201 with the group and its ``Location``.
    """
    service = service_for(request, source)
    created = service.create_group(body)
    finish(service)
    return reply(created, 201, {"Location": created["meta"]["location"]})


@router.get("/Groups/{group_id}")
@scim_endpoint
def get_group(
    group_id: str, request: Request, source: ScimSource = Depends(scim_source)
) -> Response:
    """
    Args:
        group_id: The id the provider uses.
        request: The request.
        source: The authenticated source.

    Returns:
        The group.
    """
    return reply(service_for(request, source).get_group(group_id))


@router.put("/Groups/{group_id}")
@scim_endpoint
def replace_group(
    group_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Args:
        group_id: The id the provider uses.
        request: The request.
        source: The authenticated source.
        body: The whole group document.

    Returns:
        The group after.
    """
    service = service_for(request, source)
    changed = service.replace_group(group_id, body)
    finish(service)
    return reply(changed)


@router.patch("/Groups/{group_id}")
@scim_endpoint
def patch_group(
    group_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Args:
        group_id: The id the provider uses.
        request: The request.
        source: The authenticated source.
        body: A PatchOp.

    Returns:
        The group after.
    """
    service = service_for(request, source)
    changed = service.patch_group(group_id, body)
    finish(service)
    return reply(changed)


@router.delete("/Groups/{group_id}")
@scim_endpoint
def delete_group(
    group_id: str, request: Request, source: ScimSource = Depends(scim_source)
) -> Response:
    """
    Args:
        group_id: The id the provider uses.
        request: The request.
        source: The authenticated source.

    Returns:
        204.
    """
    service = service_for(request, source)
    service.delete_group(group_id)
    finish(service)
    return Response(status_code=204)
