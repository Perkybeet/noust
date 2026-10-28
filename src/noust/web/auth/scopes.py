# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The scope (read, deploy, admin) a request needs and whether the credential has it.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status

from noust.web.auth.config import get_audit_logger
from noust.web.auth.connection import get_client_ip
from noust.web.auth.constants import (
    _NODE_PROXY_PATH,
    DEPLOY_SCOPE_PATHS,
    DEPLOY_SCOPE_PATTERNS,
    SAFE_METHODS,
)
from noust.web.auth.payload import actor_label, scope_satisfies
from noust.web.versioning import canonical_api_path


def required_scope(method: str, path: str) -> str:
    """
    The 3.0 scope policy: what a request needed before routes had permissions.

    Reads need ``read``. The mutations that move an existing application - an
    update, a rollback, activating a release - need ``deploy``. Every other
    mutation, creating an application included, needs ``admin``. Since 3.1
    every route of this server declares a permission instead
    (:mod:`noust.web.permissions`); this rule is kept for the one case no map
    can answer - a path of a node, reached through the proxy, that this
    server does not know - so a newer node is reached no more loosely than
    3.0 reached it.

    Args:
        method: The HTTP method.
        path: The request path. A node-proxied path
            (``/api/nodes/{node}/api/...``) is judged by what follows the
            node, exactly as it would be judged locally.

    Returns:
        The minimum scope.
    """
    proxied = _NODE_PROXY_PATH.fullmatch(path)
    if proxied is not None:
        path = proxied.group(1)
    # /api/v1/... is the same route as /api/..., on this server and on a node.
    path = canonical_api_path(path)
    verb = method.upper()
    if verb in SAFE_METHODS:
        return "read"
    if verb == "POST" and (
        path in DEPLOY_SCOPE_PATHS or any(p.match(path) for p in DEPLOY_SCOPE_PATTERNS)
    ):
        return "deploy"
    return "admin"


def ensure_scope(request: Request, payload: dict[str, Any], required: str) -> None:
    """
    Refuse a request whose credential does not carry the scope it needs.

    For the handlers whose need depends on what they are asked (an unmasked
    ``.env``, the console's own journal): the route's permission is enforced
    before they run, by :func:`require_auth`. A payload's ``scope`` is the 3.0
    scope its permissions amount to (:func:`noust.web.permissions.roles.legacy_scope`).

    Args:
        request: The incoming request.
        payload: The verified session payload; its ``scope`` was set where the
            credential was resolved. A payload without one fails closed as
            ``read``.
        required: The scope the operation demands.

    Raises:
        HTTPException: 403 when the scope is insufficient. The refusal is
            audited with the token's name, never the token.
    """
    granted = str(payload.get("scope") or "read")
    if scope_satisfies(granted, required):
        return

    audit = get_audit_logger()
    if audit:
        audit.record(
            action="auth.scope",
            result="denied",
            client_ip=get_client_ip(request),
            actor=actor_label(payload),
            resource=request.url.path,
            detail=f"scope '{granted}' below required '{required}'",
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"This credential's scope is '{granted}', and {request.method} "
            f"{request.url.path} requires '{required}'. Issue a token with the "
            "right scope from Settings, or POST /api/auth/tokens."
        ),
    )
