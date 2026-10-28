# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a node admits from a central that is applying the fleet's settings.

A person saves a setting of the fleet on the central, in sudo mode, and the central applies it to
every server - also later, when a server comes back, with nobody in front of the console. Those
pushes cannot carry a person's sudo mode, so they carry the reference of the change that person
approved (:mod:`noust.fleet.settings.signature`), and this module is where a node decides what
that is worth. The node takes the central's word for the person and the change - the central is
who the fleet token says it is - and for nothing else:

- **Only for the fleet's settings.** A push is admitted on the routes of
  :mod:`noust.fleet.settings.routes` and no other, and the permissions it holds are the ones those
  routes ask for, not a role's. A signed request to anything else is refused
  (``fleet_sync_refused``) whatever the central claims, so a compromised central cannot use the
  confirmation it gives itself for a deploy, a database or a secret.
- **Only with the ceiling at ``admin``.** A node held to ``deploy`` or ``read`` takes none of it
  (``fleet_sync_ceiling``): the ceiling is the node's, set on the node.
- **Only when the two headers name the same change.** The elevation header alone, or naming
  another change, confirms nothing.

The admission is one more branch of :func:`noust.web.auth.fleet.admit_fleet` and the allowlist one
more check of :func:`noust.web.auth.fleet.ensure_fleet_allowed`; both live here so that the rest
of the node's fleet wall is as it was.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any

from starlette.requests import HTTPConnection

from noust.core.server_text import ServerText, say
from noust.web.auth.config import get_audit_logger
from noust.web.auth.connection import get_client_ip
from noust.web.auth.constants import FLEET_ACTOR_HEADER, FLEET_ELEVATED_HEADER
from noust.web.auth.payload import FleetCredentialRefused, actor_label
from noust.web.permissions.roles import legacy_scope

__all__ = [
    "SYNC_CEILING_CODE",
    "SYNC_ROUTE_CODE",
    "admit_sync",
    "is_sync",
    "refuse_sync",
    "sync_permissions",
    "sync_route_refusal",
]

#: The ``error`` a node answers with when it is held below what the fleet's settings need.
SYNC_CEILING_CODE = "fleet_sync_ceiling"

#: The ``error`` a node answers with when a signed push asks for anything else.
SYNC_ROUTE_CODE = "fleet_sync_refused"


def is_sync(payload: Mapping[str, Any]) -> bool:
    """
    Args:
        payload: An admitted payload.

    Returns:
        Whether the request is a central's push of the fleet's settings.
    """
    return bool(payload.get("fleet_sync"))


def sync_permissions() -> frozenset[str]:
    """
    Returns:
        The permissions the routes of the fleet's settings ask for: all a push is given.
    """
    from noust.fleet.settings.routes import sync_operations
    from noust.web.permissions import PUBLIC
    from noust.web.permissions.registry import permission_for_route

    return frozenset(
        permission
        for method, template in sync_operations()
        if (permission := permission_for_route(method, template)) is not None
        and permission != PUBLIC
    )


def admit_sync(payload: dict[str, Any], connection: HTTPConnection) -> dict[str, Any] | None:
    """
    Admit a request signed as a push of the fleet's settings.

    Args:
        payload: The raw payload of a fleet token. Modified in place.
        connection: The request or handshake that presented it.

    Returns:
        The payload, admitted; None when the request is not signed as a push, and
        :func:`~noust.web.auth.fleet.admit_fleet` goes on as it always did.

    Raises:
        FleetCredentialRefused: 400 for a signature that is not exactly the format.
    """
    from noust.fleet.settings.signature import SyncSignature, SyncSignatureError

    try:
        parsed = SyncSignature.parse(
            connection.headers.get(FLEET_ACTOR_HEADER),
            connection.headers.get(FLEET_ELEVATED_HEADER),
        )
    except SyncSignatureError as exc:
        raise FleetCredentialRefused(
            400, "validation_error", exc.message, exc.details or "Check the central's version."
        ) from exc
    if parsed is None:
        return None
    policy = importlib.import_module("noust.fleet.policy")
    access = policy.current_access()
    permissions = frozenset(p for p in sync_permissions() if policy.permits(access, p))
    signature = parsed.signature
    payload["fleet"] = True
    payload["role"] = None
    payload["permissions"] = permissions
    payload["scope"] = legacy_scope(permissions)
    payload["on_behalf_of"] = f"{signature.person} (change {signature.change})"
    payload["fleet_sync"] = {"person": signature.person, "change": signature.change}
    # The confirmation is the central's to give only where the node's ceiling lets it write the
    # fleet's settings at all; below that the request is refused by sync_route_refusal.
    payload["elevation_attested"] = parsed.attested and access.level == "admin"
    return payload


def sync_route_refusal(method: str, path: str) -> ServerText | None:
    """
    Say why a signed push may not do what it asks, if it may not.

    Args:
        method: The HTTP method; ``GET`` for a WebSocket handshake.
        path: The request path.

    Returns:
        None when the push is on the fleet's settings and this node's ceiling is ``admin``;
        otherwise the sentence to refuse with.
    """
    from noust.fleet.settings.routes import matches_sync_route

    access = importlib.import_module("noust.fleet.policy").current_access()
    if access.level != "admin":
        return say(
            "fleet_settings.sync_ceiling",
            level=str(access.level),
            command="noust fleet access --level admin",
        )
    if not matches_sync_route()(method, path):
        return say("fleet_settings.sync_route", method=method.upper(), path=path)
    return None


def refuse_sync(connection: HTTPConnection, payload: Mapping[str, Any], text: ServerText) -> None:
    """
    Audit a refused push and raise the answer.

    Args:
        connection: The request or handshake.
        payload: The admitted payload.
        text: The sentence from :func:`sync_route_refusal`.

    Raises:
        FleetCredentialRefused: Always, a 403 whose ``error`` is :data:`SYNC_CEILING_CODE` or
            :data:`SYNC_ROUTE_CODE` and which carries the sentence as a code.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.fleet",
            result="denied",
            client_ip=get_client_ip(connection),
            actor=actor_label(payload),
            resource=str(connection.scope.get("path", "")),
            detail=text.text,
        )
    code = SYNC_CEILING_CODE if text.code == "fleet_settings.sync_ceiling" else SYNC_ROUTE_CODE
    refusal = FleetCredentialRefused(403, code, text.text, text.hint or "")
    body = refusal.detail
    if isinstance(body, dict):
        body["coded"] = text.to_dict()
    raise refusal
