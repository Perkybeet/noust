# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Who is behind a request, read from the payload its credential authenticated.

The payload is the dictionary :func:`noust.web.auth.require_auth` hands to
every endpoint; it has carried ``type``, ``sid`` and ``scope`` since 1.x and
now also carries the account, the role and the permissions. This module is the
one reading of it, so the audit log (:func:`actor_of`), the permission checks
and the fleet headers all name the same principal.
"""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from noust.web.permissions.roles import permissions_for_scope

KIND_ACCOUNT = "account"
KIND_MASTER = "master"
KIND_TOKEN = "token"  # noqa: S105 - a kind of principal, not a credential
KIND_FLEET = "fleet"
KIND_ANONYMOUS = "anonymous"


@dataclass(frozen=True)
class Principal:
    """
    The person or credential a request acts as.

    Attributes:
        kind: ``account``, ``master`` (the master token, in a session or as
            a Bearer), ``token`` (an API token), ``fleet`` (a central acting
            for one of its operators) or ``anonymous``.
        id: The account id, the token's name, or the fleet token's name.
        name: The username, ``master``, ``token:<name>``, or the central's
            operator for a fleet request.
        role: The account's role, or the role a central forwarded.
        via: How the credential arrived: ``session``, ``bearer``, or
            ``fleet:<token name>``.
        source: The client address.
        grant: For the master token, how it holds the console: ``compat``,
            ``break_glass`` or ``recovery``.
        permissions: What it may do.
    """

    kind: str
    id: str | None
    name: str
    role: str | None
    via: str | None
    source: str | None
    grant: str | None
    permissions: frozenset[str]


def permissions_of(payload: Mapping[str, Any]) -> frozenset[str]:
    """
    What a payload may do.

    Args:
        payload: An authenticated payload.

    Returns:
        Its ``permissions``; for a payload built before permissions existed
        (a test double, a WebSocket ticket from 3.0), what its scope allowed.
    """
    granted = payload.get("permissions")
    if granted is not None:
        return frozenset(granted)
    return permissions_for_scope(str(payload.get("scope") or "read"))


def principal_of(payload: Mapping[str, Any] | None) -> Principal:
    """
    Read the principal out of a payload.

    Args:
        payload: An authenticated payload, or None for an anonymous request.

    Returns:
        The principal.
    """
    if not payload:
        return Principal(KIND_ANONYMOUS, None, "anonymous", None, None, None, None, frozenset())
    source = payload.get("ip")
    permissions = permissions_of(payload)
    if payload.get("fleet"):
        token = str(payload.get("token_name") or "fleet")
        return Principal(
            KIND_FLEET,
            token,
            str(payload.get("on_behalf_of") or token),
            payload.get("role"),
            f"fleet:{token}",
            source,
            None,
            permissions,
        )
    if payload.get("account_id") is not None:
        return Principal(
            KIND_ACCOUNT,
            str(payload["account_id"]),
            str(payload.get("username") or payload["account_id"]),
            payload.get("role"),
            str(payload.get("source") or "session"),
            source,
            None,
            permissions,
        )
    kind = payload.get("type")
    if kind == "api_token":
        name = str(payload.get("token_name") or "")
        return Principal(
            KIND_TOKEN,
            name,
            f"token:{name}",
            payload.get("role"),
            "bearer",
            source,
            None,
            permissions,
        )
    return Principal(
        KIND_MASTER,
        None,
        "master",
        None,
        "session" if kind == "session" else "bearer",
        source,
        payload.get("grant"),
        permissions,
    )


def actor_of(payload: Mapping[str, Any] | None) -> Any | None:
    """
    The audit log's :class:`noust.core.audit.Actor` for a request.

    Args:
        payload: An authenticated payload, or None.

    Returns:
        The actor, or None while :mod:`noust.core.audit` is not installed:
        callers then record through :class:`noust.web.auth.AuditLogger`.
    """
    try:
        audit = importlib.import_module("noust.core.audit")
    except ImportError:
        return None
    actor_type = getattr(audit, "Actor", None)
    if actor_type is None:
        return None
    principal = principal_of(payload)
    return actor_type(
        kind=principal.kind,
        id=principal.id,
        name=principal.name,
        role=principal.role or principal.grant,
        via=principal.via,
        source=principal.source,
    )
