# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The FastAPI dependencies every route resolves: the credential, CSRF and the permission.
"""

from __future__ import annotations

import logging
import secrets
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException, Request, status

from noust.web.auth.config import get_audit_logger, get_global_token_manager, get_security_config
from noust.web.auth.connection import bearer_token, get_client_ip
from noust.web.auth.constants import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CSRF_HEADER_NAMES,
    SAFE_METHODS,
    SESSION_COOKIE_NAME,
)
from noust.web.auth.credentials import verify_credential
from noust.web.auth.fleet import ensure_fleet_allowed
from noust.web.auth.payload import actor_label
from noust.web.permissions import PUBLIC
from noust.web.permissions.enforce import (
    PermissionDenied,
    check_gates,
    check_permission,
    required_permissions,
    route_template,
)
from noust.web.permissions.principal import scope_of
from noust.web.permissions.scopes import RESOURCE, rule_for

logger = logging.getLogger("noust.web.auth")


def _unauthorized(detail: str) -> HTTPException:
    """
    Build a 401 response.

    Args:
        detail: Message for the client.

    Returns:
        The exception to raise.
    """
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _check_csrf(request: Request, payload: dict[str, Any], client_ip: str) -> None:
    """
    Enforce the CSRF token on cookie-authenticated mutations.

    Args:
        request: The incoming request.
        payload: The verified session payload.
        client_ip: The caller's address, for the audit record.

    Raises:
        HTTPException: 403 when the CSRF token is missing or wrong.
    """
    if request.method.upper() in SAFE_METHODS:
        return

    presented = next(
        (request.headers[name] for name in CSRF_HEADER_NAMES if request.headers.get(name)), ""
    )
    expected = payload.get("csrf", "")
    if presented and expected and secrets.compare_digest(presented, expected):
        return

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="auth.csrf",
            result="denied",
            client_ip=client_ip,
            actor=actor_label(payload),
            resource=request.url.path,
            detail=f"missing or invalid {CSRF_HEADER_NAME} header",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Missing or invalid CSRF token. Send the {CSRF_COOKIE_NAME} cookie value "
            f"in the {CSRF_HEADER_NAME} header, or authenticate with a Bearer token."
        ),
    )


def ensure_permission(request: Request, payload: Mapping[str, Any], permission: str) -> None:
    """
    Refuse a request whose principal lacks a permission, and audit the refusal.

    The route's own permission is enforced by :func:`require_auth`; a handler
    whose need depends on what it is asked calls this for the rest.

    Args:
        request: The incoming request.
        payload: The authenticated payload.
        permission: A :class:`~noust.web.permissions.Permission` value.

    Raises:
        PermissionDenied: 403, per
            :func:`noust.web.permissions.enforce.check_permission`.
    """
    try:
        check_permission(payload, permission)
    except PermissionDenied as exc:
        # A person told to enrol a factor or accept the notice is not being
        # refused anything; auditing each poll the console makes meanwhile
        # would bury the refusals that matter.
        if exc.error == "permission_denied":
            _audit_permission_refusal(request, payload, exc.reason)
        raise


def _audit_permission_refusal(request: Request, payload: Mapping[str, Any], reason: str) -> None:
    """
    Record a request refused for want of a permission.

    Args:
        request: The request.
        payload: Who made it.
        reason: The sentence it was refused with.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.scope",
            result="denied",
            client_ip=get_client_ip(request),
            actor=actor_label(payload),
            resource=request.url.path,
            detail=reason,
        )


def authorize(request: Request, payload: Mapping[str, Any], needed: list[str] | None) -> None:
    """
    Hold a principal to everything its route needs.

    Args:
        request: The incoming request.
        payload: The authenticated payload.
        needed: What :func:`~noust.web.permissions.enforce.required_permissions`
            answered; None for a route no map names.

    Raises:
        PermissionDenied: 403 when a permission is missing, or the route
            declares none: a route nobody classified is refused, not served.
    """
    if needed is None:
        route = f"{request.method} {request.url.path}"
        logger.error("No permission is declared for %s; refusing it", route)
        _audit_permission_refusal(request, payload, f"{route} declares no permission")
        raise PermissionDenied(
            "permission_undeclared",
            "",
            f"{route} declares no permission",
            "This is a defect in Noust: every route must name its permission in "
            "noust.web.permissions. Report it; the route is refused until then.",
        )
    if _team_decides(request, payload):
        # What the route needs is held per team, and which team is the resource's:
        # noust.web.api.scope decides, once it knows the resource is the caller's to
        # know about (404 first, so a team's resource never answers 403). What stands
        # before every permission still stands here.
        for permission in needed:
            check_gates(payload, permission)
        return
    for permission in needed:
        ensure_permission(request, payload, permission)


def _team_decides(request: Request, payload: Mapping[str, Any]) -> bool:
    """
    Whether the permission of this request is judged against a resource's team.

    Args:
        request: The incoming request.
        payload: The authenticated payload.

    Returns:
        True for a principal that does not see every team, on a route addressed
        to one resource: :func:`noust.web.api.scope.enforce_scope` holds it to the
        permission it has in that resource's team after finding the resource.
    """
    if not scope_of(payload).restricted:
        return False
    template = route_template(request)
    rule = rule_for(request.method, template) if template is not None else None
    return rule is not None and rule.scoping == RESOURCE


async def require_auth(request: Request) -> dict[str, Any]:
    """
    FastAPI dependency enforcing authentication and permissions on an endpoint.

    Installed on the API router itself, so every route under ``/api`` runs it
    whether or not its handler asks for the payload, and every handler that
    does gets the same payload - FastAPI resolves a dependency once per
    request. A route the permission maps call public (the sign-in, the
    invitation pages) is let through with an empty payload and no credential
    is looked at.

    Accepts, in order, an ``Authorization: Bearer`` session token, API token
    or master token (for the CLI), or the session cookie. A session - in the
    cookie or in the header - additionally requires its CSRF header on every
    unsafe method; the master token and API tokens are not sessions and do
    not have one.

    Whichever channel is used, a credential that does not match is counted by
    :func:`record_auth_failure`, so the lockout applies to master token guessing
    on any endpoint and not only to ``/api/auth/login``. The principal is then
    held to the permission its route declares
    (:mod:`noust.web.permissions`), here, at the same chokepoint, so a new
    endpoint is covered the moment it is mapped and refused until it is.

    Args:
        request: The incoming request.

    Returns:
        The session payload, also stored on ``request.state.session``; empty
        for a public route.

    Raises:
        HTTPException: 401 when unauthenticated, 403 on a CSRF failure or a
            missing permission, 500 when the server was never initialised.
    """
    needed = await required_permissions(request)
    if needed == [PUBLIC]:
        return {}

    manager = get_global_token_manager()
    if manager is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Authentication system not initialized",
        )

    config = get_security_config()
    client_ip = get_client_ip(request, config)
    resource = request.url.path

    bearer = bearer_token(request)
    if bearer:
        payload = verify_credential(
            bearer, client_ip, resource=resource, source="bearer", connection=request
        )
        if payload is None:
            raise _unauthorized("Invalid or expired authentication token")
        if payload.get("type") == "session":
            # A session is held to the session's rules whichever header
            # carries it: the CSRF token here, sudo mode in ensure_elevated.
            # Only the master token and API tokens, which are no session, go
            # without. A client that logged in with ``bearer: true`` received
            # the CSRF token in the same response as the session token.
            _check_csrf(request, payload, client_ip)
        request.state.session = payload
        # A fleet token's refusals name why a central may never do this, which
        # says more than the permission it also lacks.
        await ensure_fleet_allowed(request, payload)
        authorize(request, payload, needed)
        return payload

    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if cookie:
        payload = verify_credential(
            cookie, client_ip, resource=resource, source="cookie", connection=request
        )
        if payload is None:
            raise _unauthorized("Session expired or revoked. Please log in again.")
        _check_csrf(request, payload, client_ip)
        request.state.session = payload
        await ensure_fleet_allowed(request, payload)
        authorize(request, payload, needed)
        renewed = manager.renew_session(payload)
        if renewed is not None:
            request.state.renewed_session = renewed
        return payload

    raise _unauthorized("Not authenticated")
