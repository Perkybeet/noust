# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Enforcing the route maps: what a request needs, and refusing it when it lacks it.

:func:`noust.web.auth.require_auth` - the dependency every API route resolves,
installed on the API router itself so no route can forget it - calls
:func:`required_permissions` once it knows who is asking, and
:func:`check_permission` for each answer. The route is identified by the
template FastAPI matched, not by the path, so ``/api/apps/types`` and
``/api/apps/{domain}`` are never confused, and a route missing from every map
is refused rather than waved through.

Two routes need more than their template says, and only these two:
``PATCH /api/config``, whose body names the key it changes, and the node
proxy, whose permission is that of the node's own path.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from fastapi import HTTPException, Request

from noust.web.permissions import PUBLIC, Permission
from noust.web.permissions.principal import permissions_of
from noust.web.permissions.registry import (
    NODE_PROXY_TEMPLATE,
    match_path,
    permission_for_route,
    proxied_path,
)

logger = logging.getLogger(__name__)

#: The OpenAPI extension every operation carries its permission in, beside
#: ``x-noust-requires-elevation``: a central, the console and any client read
#: what a call needs from the schema instead of keeping a copy of this table.
PERMISSION_EXTENSION = "x-noust-permission"

#: Configuration sections that are security settings: who reaches the console
#: and how, the sign-in policy, the audit trail, fleet membership, and the
#: four-eyes approvals - an admin who could turn those off would need nobody.
SECURITY_CONFIG_SECTIONS = frozenset(
    {"web", "auth", "security", "audit", "central", "fleet", "approval"}
)

_ROUTE_INDEX_ATTRIBUTE = "noust_route_index"

RouteIndex = dict[Any, list[tuple[frozenset[str], Any, str]]]


class PermissionDenied(HTTPException):
    """
    A request its principal may not make.

    An ``HTTPException`` carrying its own ``error`` code, so the API's error
    boundary answers it in the one contract. ``permission_denied`` for a
    missing permission; ``mfa_required`` and ``notice_required`` for an
    account that must enrol a second factor, or accept the usage notice,
    before anything else.

    Attributes:
        permission: The permission that was needed.
        reason: The sentence answered, for the audit record.
    """

    def __init__(self, error: str, permission: str, detail: str, hint: str) -> None:
        """
        Args:
            error: Machine-readable code.
            permission: The permission that was needed.
            detail: What was refused.
            hint: What to do instead.
        """
        super().__init__(
            status_code=403,
            detail={"error": error, "detail": detail, "hint": hint, "fields": None},
        )
        self.error = error
        self.permission = permission
        self.reason = detail


def has_permission(payload: Mapping[str, Any], permission: str) -> bool:
    """
    Report whether a payload holds a permission.

    Args:
        payload: An authenticated payload.
        permission: A :class:`Permission` value, or :data:`PUBLIC`.

    Returns:
        True when it may. Holding it is not always enough to act: an account
        that must enrol a second factor or accept the notice first is refused
        by :func:`check_permission` anyway.
    """
    return permission == PUBLIC or permission in permissions_of(payload)


def ensure_notice_accepted(payload: Mapping[str, Any], permission: str = Permission.SELF) -> None:
    """
    Refuse a principal that has not accepted the usage notice yet.

    What is one's own (``self``) stays open without it, so the notice can be
    read and accepted - except what gives standing power: sudo mode and a new
    API token call this themselves.

    Args:
        payload: An authenticated payload; a token's carries its owner's state.
        permission: What was asked, for the refusal.

    Raises:
        PermissionDenied: 403 ``notice_required``.
    """
    if payload.get("notice_pending"):
        raise PermissionDenied(
            "notice_required",
            permission,
            "Accept the usage notice before doing anything else",
            "Read it in GET /api/auth/session and accept it: POST /api/auth/notice/accept.",
        )


def check_permission(payload: Mapping[str, Any], permission: str) -> None:
    """
    Refuse a principal that may not do what a route needs.

    Args:
        payload: An authenticated payload.
        permission: What the route needs.

    Raises:
        PermissionDenied: 403 ``mfa_required`` for an account without a
            second factor, ``notice_required`` for one that has not accepted
            the usage notice (both for anything but its own session), and
            ``permission_denied`` for a permission it does not hold.
    """
    if permission == PUBLIC:
        return
    if permission != Permission.SELF:
        if payload.get("mfa_pending"):
            raise PermissionDenied(
                "mfa_required",
                permission,
                "Set up two-factor authentication before doing anything else",
                "Enrol an authenticator: POST /api/auth/2fa/enroll, then /api/auth/2fa/confirm.",
            )
        ensure_notice_accepted(payload, permission)
    if permission in permissions_of(payload):
        return
    who = payload.get("role") or payload.get("grant") or payload.get("scope") or "this credential"
    raise PermissionDenied(
        "permission_denied",
        permission,
        f"This needs the '{permission}' permission, which {who} does not hold",
        "Ask a security officer for an account with a role that holds it.",
    )


def _route_index(request: Request) -> RouteIndex:
    """
    Map every endpoint of the application to the routes it serves.

    Built once per application and kept on its state; rebuilt if the number
    of routes changed, which only a test adding routes after startup does.

    Args:
        request: The request, for its application.

    Returns:
        Endpoint callable to ``(methods, path regex, path template)`` entries.
    """
    app = request.app
    cached = getattr(app.state, _ROUTE_INDEX_ATTRIBUTE, None)
    if cached is not None and cached[0] == len(app.routes):
        index: RouteIndex = cached[1]
        return index
    # Imported here: the OpenAPI module imports the dependencies that import
    # this one, and by the time a request arrives every module is loaded.
    from noust.web.api.openapi import api_routes

    index = {}
    for route in api_routes(app.routes):
        index.setdefault(route.endpoint, []).append(
            (frozenset(route.methods or ()), route.path_regex, str(route.path_format))
        )
    app.state.noust_route_index = (len(app.routes), index)
    return index


def route_template(request: Request) -> str | None:
    """
    The path template of the route a request matched.

    Found by the endpoint Starlette put in the scope and the route's own
    pattern, which works the same whether FastAPI flattens included routers
    (the releases distributions ship) or keeps them nested (0.13x).

    Args:
        request: The request, after routing.

    Returns:
        The template, such as ``/api/apps/{domain}``, or None when the
        request did not reach a route of this application.
    """
    endpoint = request.scope.get("endpoint")
    if endpoint is None:
        return None
    method = request.method.upper()
    path = str(request.scope.get("path", ""))
    candidates = _route_index(request).get(endpoint, [])
    for methods, regex, template in candidates:
        if (method in methods or (method == "HEAD" and "GET" in methods)) and regex.match(path):
            return template
    return None


async def patched_config_key(request: Request) -> str:
    """
    Read the dotted key a ``PATCH /api/config`` body addresses.

    The body is bounded by the middleware, which read it already, and
    Starlette caches it on the request, so the endpoint parses the same bytes
    afterwards.

    Args:
        request: The incoming request.

    Returns:
        The key, or ``""`` when the body does not name one as a string - which
        the endpoint refuses on its own - so a malformed body is never
        mistaken for an allowed key.
    """
    try:
        body = json.loads(await request.body() or b"null")
    except (ValueError, UnicodeDecodeError):
        return ""
    key = body.get("path") if isinstance(body, dict) else None
    return key if isinstance(key, str) else ""


async def required_permissions(request: Request) -> list[str] | None:
    """
    Everything a request needs, from its route and, for two routes, its content.

    Args:
        request: The request, after routing.

    Returns:
        The permissions, all of which the principal must hold; ``[PUBLIC]``
        for a public route; None when the route is in no map, which the
        caller refuses.
    """
    template = route_template(request)
    if template is None:
        return None
    method = request.method.upper()
    permission = permission_for_route(method, template)
    if permission is None:
        return None
    needed = [permission]
    if (method, template) == ("PATCH", "/api/config"):
        section = (await patched_config_key(request)).strip().split(".", 1)[0]
        if section in SECURITY_CONFIG_SECTIONS:
            needed.append(Permission.SECURITY_MANAGE)
    elif template == NODE_PROXY_TEMPLATE:
        inner = proxied_path(str(request.scope.get("path", "")))
        if inner:
            needed.append(_proxied_permission(method, inner))
    return needed


#: What a node's path this server does not know needs, by the 3.0 scope rule
#: (reads ``read``, update and rollback ``deploy``, anything else ``admin``):
#: a node newer than its central is reached no more loosely than 3.0 did.
_LEGACY_SCOPE_PERMISSIONS = {
    "read": Permission.FLEET_READ,
    "deploy": Permission.APPS_DEPLOY,
    "admin": Permission.APPS_MANAGE,
}


def _proxied_permission(method: str, inner: str) -> str:
    """
    What a request through the node proxy needs, besides ``fleet.read``.

    Args:
        method: The HTTP method.
        inner: The node's own path, from ``/api`` on.

    Returns:
        The permission this server's map gives that path, or, for a path it
        does not know, the one its 3.0 scope rule asks for.
    """
    matched = match_path(method, inner)
    if matched is not None:
        return Permission.FLEET_READ if matched[1] == PUBLIC else matched[1]
    # Imported here: noust.web.auth imports this module.
    from noust.web.auth import required_scope

    return _LEGACY_SCOPE_PERMISSIONS[required_scope(method, inner)]


def annotate_permissions(schema: dict[str, Any], routes: Iterable[Any]) -> dict[str, Any]:
    """
    Publish each operation's permission as ``x-noust-permission``.

    Args:
        schema: The OpenAPI document. Modified in place.
        routes: The application's routes.

    Returns:
        The same document.
    """
    from noust.web.api.openapi import api_routes

    paths: dict[str, Any] = schema.get("paths", {})
    for route in api_routes(list(routes)):
        if not route.include_in_schema:
            continue
        operations = paths.get(route.path_format, {})
        for method in route.methods or ():
            permission = permission_for_route(method, str(route.path_format))
            operation = operations.get(method.lower())
            if permission is not None and isinstance(operation, dict):
                operation[PERMISSION_EXTENSION] = permission
    return schema
