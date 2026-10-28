# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node admits from the central that manages it, and what it refuses whatever the central claims.
"""

from __future__ import annotations

import importlib
import ipaddress
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import Request
from starlette.requests import HTTPConnection

from noust.web.auth.config import get_audit_logger
from noust.web.auth.connection import get_client_ip
from noust.web.auth.constants import (
    API_TOKEN_SCOPES,
    FLEET_ACTOR_HEADER,
    FLEET_ACTOR_PATTERN,
    FLEET_ACTOR_ROLE_HEADER,
    FLEET_ACTOR_SCOPE_HEADER,
    FLEET_AUTH_PATHS,
    FLEET_ELEVATED_HEADER,
    FLEET_PROTECTED_CONFIG_SECTIONS,
    FLEET_REFUSED_PREFIXES,
    FLEET_REFUSED_WRITES,
    FLEET_ROLE_PATTERN,
    FORWARDING_HEADERS,
    SCOPE_RANK,
)
from noust.web.auth.payload import FleetCredentialRefused, actor_label, is_fleet
from noust.web.permissions.enforce import patched_config_key
from noust.web.permissions.roles import (
    ROLE_PERMISSIONS,
    legacy_scope,
    permissions_for_role,
    permissions_for_scope,
)


def fleet_origin_refusal(connection: HTTPConnection | None) -> str | None:
    """
    Say why a connection may not carry a fleet token, if it may not.

    The peer is the TCP peer, not :func:`get_client_ip`'s answer: a trusted
    proxy's ``X-Forwarded-For`` names somebody else by design. And loopback
    is not enough on its own, because a reverse proxy on this machine is
    loopback too; the headers such a proxy adds are what tell it apart from
    the SSH tunnel, which adds none.

    Args:
        connection: The request or handshake, or None when the caller has
            none to offer - which fails closed.

    Returns:
        None when the token may be used, otherwise the sentence to refuse with.
    """
    if connection is None:
        return "A fleet token is only accepted on a request this server can see the peer of."
    peer = connection.client.host if connection.client else ""
    try:
        loopback = ipaddress.ip_address(peer).is_loopback
    except ValueError:
        loopback = False
    if not loopback:
        return (
            "A fleet token is only accepted from this server's own loopback, "
            "where the central's SSH tunnel arrives."
        )
    forwarded = [name for name in FORWARDING_HEADERS if name in connection.headers]
    if forwarded:
        return (
            "A fleet token is not accepted through a reverse proxy "
            f"(the request carries {', '.join(forwarded)}); the central reaches this "
            "server through its SSH tunnel only."
        )
    return None


def admit_fleet(payload: dict[str, Any], connection: HTTPConnection | None) -> dict[str, Any]:
    """
    Turn a fleet token's raw payload into what the request may do.

    The one place a fleet payload is admitted: the peer must be the tunnel,
    ``X-Noust-Actor`` names the operator on the central, the token is
    narrowed to ``X-Noust-Actor-Scope``, and ``X-Noust-Elevated`` records
    whether the central confirmed that operator's sudo mode. The raw
    ``fleet`` scope is replaced, so a payload that skipped this satisfies no
    scope at all (see :func:`scope_satisfies`). Missing or invalid,
    ``X-Noust-Actor-Scope`` grants ``read``, never ``admin``: an older
    central, or a caller on this central with no human actor to narrow to
    (a status poll), must ask for admin explicitly rather than receive it by
    omission.

    Args:
        payload: The raw payload of a fleet token. Modified in place.
        connection: The request or handshake that presented it.

    Returns:
        The same payload, admitted.

    Raises:
        FleetCredentialRefused: 401 when the peer is not the tunnel, 400 when
            a fleet header is malformed.
    """
    refusal = fleet_origin_refusal(connection)
    if refusal is not None or connection is None:
        raise FleetCredentialRefused(
            401,
            "fleet_origin",
            refusal or "A fleet token needs a connection.",
            "Reach this server through the central, or use a token of your own.",
        )

    actor = connection.headers.get(FLEET_ACTOR_HEADER)
    if actor is not None and not FLEET_ACTOR_PATTERN.fullmatch(actor):
        raise FleetCredentialRefused(
            400,
            "validation_error",
            f"{FLEET_ACTOR_HEADER} must be 1 to 64 letters, digits or ._:@+-, "
            "starting with a letter or digit.",
            "The central sends its operator's label; check the central's version.",
        )

    # Fail closed: absent or malformed, the grant is the lowest scope, not
    # the highest. An older central omitting the header, or a request this
    # central makes with no operator behind it, gets read, never admin by
    # accident.
    granted = "read"
    actor_scope = connection.headers.get(FLEET_ACTOR_SCOPE_HEADER)
    if actor_scope is not None:
        if actor_scope not in SCOPE_RANK:
            raise FleetCredentialRefused(
                400,
                "validation_error",
                f"{FLEET_ACTOR_SCOPE_HEADER} must be one of: {', '.join(API_TOKEN_SCOPES)}.",
                "The central sends its operator's scope; check the central's version.",
            )
        granted = actor_scope

    # A 3.1 central names its operator's role; this node grants what its own
    # table gives that role, never what the central says it may do.
    role: str | None = None
    base = permissions_for_scope(granted)
    actor_role = connection.headers.get(FLEET_ACTOR_ROLE_HEADER)
    if actor_role is not None:
        if not FLEET_ROLE_PATTERN.fullmatch(actor_role):
            raise FleetCredentialRefused(
                400,
                "validation_error",
                f"{FLEET_ACTOR_ROLE_HEADER} must be a role name: lower-case letters, '-' or '_'.",
                "The central sends its operator's role; check the central's version.",
            )
        role = actor_role if actor_role in ROLE_PERMISSIONS else "viewer"
        base = permissions_for_role(role)
    permits = fleet_ceiling()
    permissions = frozenset(permission for permission in base if permits(permission))

    payload["fleet"] = True
    payload["role"] = role
    payload["permissions"] = permissions
    payload["scope"] = legacy_scope(permissions)
    payload["on_behalf_of"] = actor
    payload["elevation_attested"] = connection.headers.get(FLEET_ELEVATED_HEADER) == "1"
    return payload


def fleet_ceiling() -> Callable[[str], bool]:
    """
    The most this node lets any central do, as a test of one permission.

    ``noust fleet authorize --access read|deploy|admin`` (and ``noust fleet
    access``) set it on the node; :mod:`noust.fleet.policy` reads it. A fleet
    request is granted its role's permissions intersected with this.

    Returns:
        A function saying whether the ceiling permits a permission.
    """
    policy = importlib.import_module("noust.fleet.policy")
    current_access = getattr(policy, "current_access", None)
    permits = getattr(policy, "permits", None)
    if current_access is None or permits is None:
        # INTEGRATION POINT (B5a): noust.fleet.policy.current_access() and
        # permits() are being added with the per-node ceiling. Until they
        # exist the ceiling is the one 3.0 had - admin, everything - so a
        # node upgraded ahead of its ceiling keeps working as before.
        return lambda permission: True
    access = current_access()
    return lambda permission: bool(permits(access, permission))


def _under(path: str, prefix: str) -> bool:
    """
    Report whether a path is a prefix or below it, segment-wise.

    Args:
        path: The request path.
        prefix: A path prefix without a trailing slash.

    Returns:
        True for ``prefix`` itself and anything under ``prefix/``.
    """
    return path == prefix or path.startswith(prefix + "/")


def fleet_refusal(method: str, path: str, config_key: str | None = None) -> str | None:
    """
    The operations a fleet token may never perform, stated once.

    A central manages a node's applications, services, databases and
    backups with its operator's authority. It does not manage the node's
    own credentials - tokens, two-factor authentication, sessions, sudo mode,
    WebSocket tickets - nor who may reach the node's console, nor fleet
    enrollment: those stay with whoever holds root on the node, so a
    compromised central cannot lock that operator out, widen the node's
    exposure, or mint itself a credential that outlives revocation.

    Args:
        method: The HTTP method; ``GET`` for a WebSocket handshake.
        path: The request path.
        config_key: The dotted key a ``PATCH /api/config`` body addresses, when
            the request is one; ignored otherwise.

    Returns:
        None when the operation is allowed, otherwise the sentence to refuse with.
    """
    verb = method.upper()
    if path in FLEET_AUTH_PATHS:
        return None
    for prefix in FLEET_REFUSED_PREFIXES:
        if _under(path, prefix):
            return (
                f"A central cannot reach {path} on a node: credentials and fleet "
                "enrollment belong to the node's own operator."
            )
    if (verb, path) in FLEET_REFUSED_WRITES:
        return (
            f"A central cannot {verb} {path} on a node: it carries the node's console "
            "security settings."
        )
    if verb == "PATCH" and path == "/api/config" and config_key is not None:
        section = config_key.strip().split(".", 1)[0]
        if section in FLEET_PROTECTED_CONFIG_SECTIONS:
            return (
                f"A central cannot change '{config_key}' on a node: the '{section}' "
                "settings belong to the node's own operator."
            )
    return None


def _refuse_fleet_operation(
    connection: HTTPConnection, payload: Mapping[str, Any], reason: str
) -> None:
    """
    Audit and raise a refused fleet operation.

    Args:
        connection: The request or handshake.
        payload: The admitted fleet payload.
        reason: The sentence from :func:`fleet_refusal`.

    Raises:
        FleetCredentialRefused: Always, as a 403.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.fleet",
            result="denied",
            client_ip=get_client_ip(connection),
            actor=actor_label(payload),
            resource=str(connection.scope.get("path", "")),
            detail=reason,
        )
    raise FleetCredentialRefused(
        403,
        "forbidden",
        reason,
        "Run it on the node itself, as its operator.",
    )


async def ensure_fleet_allowed(request: Request, payload: Mapping[str, Any]) -> None:
    """
    Refuse a fleet request :func:`fleet_refusal` names, at ``require_auth``.

    Args:
        request: The incoming request.
        payload: The verified payload.

    Raises:
        FleetCredentialRefused: 403 when the operation is one a fleet token
            may not perform.
    """
    if not is_fleet(payload):
        return
    method = request.method.upper()
    path = request.url.path
    config_key = (
        await patched_config_key(request) if (method, path) == ("PATCH", "/api/config") else None
    )
    reason = fleet_refusal(method, path, config_key)
    if reason is not None:
        _refuse_fleet_operation(request, payload, reason)


def ensure_fleet_handshake_allowed(connection: HTTPConnection, payload: Mapping[str, Any]) -> None:
    """
    Refuse a fleet WebSocket handshake :func:`fleet_refusal` names.

    Args:
        connection: The pending handshake.
        payload: The verified payload.

    Raises:
        FleetCredentialRefused: 403 when the stream is one a fleet token may
            not open.
    """
    if not is_fleet(payload):
        return
    reason = fleet_refusal("GET", str(connection.scope.get("path", "")))
    if reason is not None:
        _refuse_fleet_operation(connection, payload, reason)
