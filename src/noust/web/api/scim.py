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
    ScimError,
    ScimManager,
    ScimService,
    hash_token,
)
from noust.core.accounts.scim_schema import (
    list_response,
    parse_attribute_list,
    project,
    project_list,
    resource_type_documents,
    schema_documents,
)
from noust.core.accounts.scim_store import ScimSource
from noust.web.api.sso_flow import origin_of
from noust.web.auth import audit_event, bearer_token, get_client_ip, record_auth_failure
from noust.web.server import get_token_manager

MEDIA_TYPE = "application/scim+json"


class ScimResponse(Response):
    """An answer in the protocol's own media type, which is what the schema says it is."""

    media_type = MEDIA_TYPE


# Not ``NoustErrorRoute``: RFC 7644 fixes the error body of this surface (a SCIM Error message
# with the status as a string), which a provider's client parses; every handler answers its
# refusals itself through :func:`scim_endpoint`.
router = APIRouter(default_response_class=ScimResponse)
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
    response = ScimResponse(content=json.dumps(body), status_code=status)
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
def resource_types(request: Request, _source: ScimSource = Depends(scim_source)) -> Response:
    """
    Args:
        request: The request.
        _source: The authenticated source.

    Returns:
        A ListResponse of the two resource types: User and Group.
    """
    return reply(list_response(resource_type_documents(origin_of(request) + BASE_PATH)))


@router.get("/ResourceTypes/{name}")
@scim_endpoint
def resource_type(
    name: str, request: Request, _source: ScimSource = Depends(scim_source)
) -> Response:
    """
    Args:
        name: ``User`` or ``Group``.
        request: The request.
        _source: The authenticated source.

    Returns:
        The resource type.

    Raises:
        ScimError: 404 when there is none of that name.
    """
    for document in resource_type_documents(origin_of(request) + BASE_PATH):
        if document["id"] == name:
            return reply(document)
    raise ScimError(404, f"No resource type {name[:64]!r}")


@router.get("/Schemas")
@scim_endpoint
def schemas(request: Request, _source: ScimSource = Depends(scim_source)) -> Response:
    """
    Args:
        request: The request.
        _source: The authenticated source.

    Returns:
        A ListResponse of the attributes this server models.
    """
    return reply(list_response(schema_documents(origin_of(request) + BASE_PATH)))


@router.get("/Schemas/{schema_id}")
@scim_endpoint
def schema(
    schema_id: str, request: Request, _source: ScimSource = Depends(scim_source)
) -> Response:
    """
    Args:
        schema_id: The schema's URN.
        request: The request.
        _source: The authenticated source.

    Returns:
        The schema.

    Raises:
        ScimError: 404 when there is none of that id.
    """
    for document in schema_documents(origin_of(request) + BASE_PATH):
        if document["id"] == schema_id:
            return reply(document)
    raise ScimError(404, f"No schema {schema_id[:128]!r}")


Shape = tuple[list[str], list[str]]


def shaping(
    attributes: str | None = Query(default=None, max_length=512),
    excludedAttributes: str | None = Query(default=None, max_length=512),
) -> Shape:
    """
    Args:
        attributes: What to return besides what is always returned.
        excludedAttributes: What not to return.

    Returns:
        Both, parsed.
    """
    return parse_attribute_list(attributes), parse_attribute_list(excludedAttributes)


def search_request(body: dict[str, Any]) -> tuple[str | None, int | None, int | None, Shape]:
    """
    Read a SearchRequest (RFC 7644 section 3.4.3): the query of a list, in a body.

    Args:
        body: The posted document.

    Returns:
        The filter, ``startIndex``, ``count`` and the projection.

    Raises:
        ScimError: 400 ``invalidValue`` for one that is not what it should be.
    """
    filter_text = body.get("filter")
    if filter_text is not None and (not isinstance(filter_text, str) or len(filter_text) > 512):
        raise ScimError(400, "filter must be a string of at most 512 characters", "invalidFilter")
    numbers: list[int | None] = []
    for key, low, high in (("startIndex", 1, 1_000_000), ("count", 0, 10_000)):
        value = body.get(key)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high
        ):
            raise ScimError(
                400, f"{key} must be a whole number from {low} to {high}", "invalidValue"
            )
        numbers.append(value)
    return (
        filter_text,
        numbers[0],
        numbers[1],
        (
            parse_attribute_list(body.get("attributes")),
            parse_attribute_list(body.get("excludedAttributes")),
        ),
    )


@router.post("/.search")
@scim_endpoint
def search_everything(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    Search users and groups together, with the query in the body (RFC 7644 section 3.4.3).

    A filter that only one of them can answer is answered by that one. The window is applied to
    the users first and then the groups, up to 200 of each.

    Args:
        request: The request.
        source: The authenticated source.
        body: A SearchRequest.

    Returns:
        A ListResponse of both kinds.
    """
    filter_text, start, count, shape = search_request(body)
    service = service_for(request, source)
    found: list[dict[str, Any]] = []
    total = 0
    for listing in (service.list_users, service.list_groups):
        try:
            answer = listing(filter_text, 1, 200)
        except ScimError as exc:
            if exc.scim_type == "invalidFilter":
                continue
            raise
        found.extend(answer["Resources"])
        total += int(answer["totalResults"])
    first = (start or 1) - 1
    window = found[first : first + count] if count is not None else found[first:]
    return reply(
        project_list(
            {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
                "totalResults": total,
                "startIndex": first + 1,
                "itemsPerPage": len(window),
                "Resources": window,
            },
            *shape,
        )
    )


# -------------------------------------------------------------------- users


@router.get("/Users")
@scim_endpoint
def list_users(
    request: Request,
    source: ScimSource = Depends(scim_source),
    filter: str | None = Query(default=None, max_length=512),
    startIndex: int | None = Query(default=None, ge=1, le=1_000_000),
    count: int | None = Query(default=None, ge=0, le=10_000),
    shape: Shape = Depends(shaping),
) -> Response:
    """
    List the users this source provisioned, or find one by ``userName`` or ``externalId``.

    Args:
        request: The request.
        source: The authenticated source.
        filter: ``attribute eq "value"``.
        startIndex: The 1-based index of the first result.
        count: How many, at most 200.
        shape: ``attributes`` and ``excludedAttributes``.

    Returns:
        A ListResponse.
    """
    return reply(
        project_list(service_for(request, source).list_users(filter, startIndex, count), *shape)
    )


@router.post("/Users/.search")
@scim_endpoint
def search_users(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    List users with the query in the body, as providers do when it is too long for an address.

    Args:
        request: The request.
        source: The authenticated source.
        body: A SearchRequest.

    Returns:
        A ListResponse.
    """
    filter_text, start, count, shape = search_request(body)
    return reply(
        project_list(service_for(request, source).list_users(filter_text, start, count), *shape)
    )


@router.post("/Users", status_code=201)
@scim_endpoint
def create_user(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
    shape: Shape = Depends(shaping),
) -> Response:
    """
    Provision a person.

    Args:
        request: The request.
        source: The authenticated source.
        body: The user document.
        shape: ``attributes`` and ``excludedAttributes``.

    Returns:
        201 with the user and its ``Location``.
    """
    service = service_for(request, source)
    created = service.create_user(body)
    finish(service)
    return reply(project(created, *shape), 201, {"Location": created["meta"]["location"]})


@router.get("/Users/{user_id}")
@scim_endpoint
def get_user(
    user_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    shape: Shape = Depends(shaping),
) -> Response:
    """
    Args:
        user_id: The id the provider uses.
        request: The request.
        source: The authenticated source.
        shape: ``attributes`` and ``excludedAttributes``.

    Returns:
        The user.
    """
    return reply(project(service_for(request, source).get_user(user_id), *shape))


@router.put("/Users/{user_id}")
@scim_endpoint
def replace_user(
    user_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
    shape: Shape = Depends(shaping),
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
    return reply(project(changed, *shape))


@router.patch("/Users/{user_id}")
@scim_endpoint
def patch_user(
    user_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
    shape: Shape = Depends(shaping),
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
    return reply(project(changed, *shape))


@router.delete("/Users/{user_id}", status_code=204, response_class=Response)
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
    shape: Shape = Depends(shaping),
) -> Response:
    """
    Args:
        request: The request.
        source: The authenticated source.
        filter: ``attribute eq "value"``.
        startIndex: The 1-based index of the first result.
        count: How many, at most 200.
        shape: ``attributes`` and ``excludedAttributes``.

    Returns:
        A ListResponse.
    """
    return reply(
        project_list(service_for(request, source).list_groups(filter, startIndex, count), *shape)
    )


@router.post("/Groups/.search")
@scim_endpoint
def search_groups(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
) -> Response:
    """
    List groups with the query in the body.

    Args:
        request: The request.
        source: The authenticated source.
        body: A SearchRequest.

    Returns:
        A ListResponse.
    """
    filter_text, start, count, shape = search_request(body)
    return reply(
        project_list(service_for(request, source).list_groups(filter_text, start, count), *shape)
    )


@router.post("/Groups", status_code=201)
@scim_endpoint
def create_group(
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
    shape: Shape = Depends(shaping),
) -> Response:
    """
    Args:
        request: The request.
        source: The authenticated source.
        body: The group document.
        shape: ``attributes`` and ``excludedAttributes``.

    Returns:
        201 with the group and its ``Location``.
    """
    service = service_for(request, source)
    created = service.create_group(body)
    finish(service)
    return reply(project(created, *shape), 201, {"Location": created["meta"]["location"]})


@router.get("/Groups/{group_id}")
@scim_endpoint
def get_group(
    group_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    shape: Shape = Depends(shaping),
) -> Response:
    """
    Args:
        group_id: The id the provider uses.
        request: The request.
        source: The authenticated source.
        shape: ``attributes`` and ``excludedAttributes``.

    Returns:
        The group.
    """
    return reply(project(service_for(request, source).get_group(group_id), *shape))


@router.put("/Groups/{group_id}")
@scim_endpoint
def replace_group(
    group_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
    shape: Shape = Depends(shaping),
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
    return reply(project(changed, *shape))


@router.patch("/Groups/{group_id}")
@scim_endpoint
def patch_group(
    group_id: str,
    request: Request,
    source: ScimSource = Depends(scim_source),
    body: dict[str, Any] = Body(...),
    shape: Shape = Depends(shaping),
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
    return reply(project(changed, *shape))


@router.delete("/Groups/{group_id}", status_code=204, response_class=Response)
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
