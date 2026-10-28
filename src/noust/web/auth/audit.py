# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The audit record of a refused or counted credential, and the key a lockout is counted under.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from noust.web.auth.config import (
    SecurityConfig,
    get_audit_logger,
    get_brute_force_protection,
    get_global_token_manager,
)
from noust.web.auth.connection import shared_address
from noust.web.auth.constants import SIGN_IN_LOCKOUT_PREFIX
from noust.web.auth.payload import actor_label

#: Prefix of the lockout key a retired fleet token's failures are counted
#: under, instead of the address it arrived from (see :func:`failure_key`).
FLEET_LOCKOUT_PREFIX = "fleet:"


def failure_key(credential: str | None, client_ip: str) -> str:
    """
    Choose what a failed credential is counted against in the lockout.

    The address, always, except for a revoked or expired fleet token: the
    central that still holds one arrives from loopback, where the operator's
    SSH-tunnelled console arrives too, and counting it there would lock the
    operator out for the lockout's length. Its failures are counted under
    ``fleet:<token name>`` instead, which locks nothing anybody uses.

    Args:
        credential: The credential that failed, if one was presented.
        client_ip: The address it came from.

    Returns:
        The lockout key.
    """
    manager = get_global_token_manager()
    if credential and manager is not None:
        name = manager.retired_fleet_token_name(credential)
        if name is not None:
            return f"{FLEET_LOCKOUT_PREFIX}{name}"
    return client_ip


def sign_in_lockout_key(
    client_ip: str, identifier: str | None, config: SecurityConfig | None = None
) -> str:
    """
    Choose what a sign-in attempt is counted and refused under.

    The address, for an address that is one client's. Behind an address many
    people share (:func:`shared_address`), the address and the name the
    attempt named: five typos by one operator on an SSH tunnel would
    otherwise lock every operator on it out at once. What still bounds a
    guesser there is the per-account lockout, which counts every wrong
    password or code on the account whatever the address
    (:meth:`~noust.core.accounts.AccountManager.record_failure`).

    Args:
        client_ip: The resolved client address.
        identifier: The username or e-mail the attempt named; None for the
            master token, which is counted under a name of its own.
        config: Configuration to use; the installed one by default.

    Returns:
        The lockout key: the address itself, or a ``sign-in:`` key.
    """
    if not shared_address(client_ip, config):
        return client_ip
    if identifier is None:
        return f"{SIGN_IN_LOCKOUT_PREFIX}{client_ip}:master"
    # Cut and folded like the names it stands for, so a key is bounded and one
    # person's typing in another case is still one person.
    name = "".join(char for char in identifier.strip().lower() if char.isprintable())[:254]
    return f"{SIGN_IN_LOCKOUT_PREFIX}{client_ip}:account:{name}"


def audit_event(
    event: str,
    outcome: str,
    *,
    client_ip: str,
    session: Mapping[str, Any] | None = None,
    target: str | None = None,
    detail: str | None = None,
) -> None:
    """
    Record one event of the console's identity layer in the audit trail.

    The one door the sign-in, account and token endpoints record through, so
    the events keep one shape: a name from the audit catalog
    (``auth.login``, ``user.create``...), the actor as :func:`actor_label`
    names it, the address, the target and one sentence. Never a credential.

    Args:
        event: Catalog name of what happened.
        outcome: ``success``, ``failure``, ``denied`` or ``warning``.
        client_ip: Where the request came from.
        session: The authenticated payload, or None for an anonymous caller.
        target: What it was done to, such as ``account:maria``.
        detail: One sentence of context.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action=event,
            result=outcome,
            client_ip=client_ip,
            actor=actor_label(session) if session else "anonymous",
            resource=target,
            detail=detail,
        )


def record_auth_failure(
    client_ip: str, resource: str, source: str, *, lockout_key: str | None = None
) -> None:
    """
    Count and audit one rejected credential, whatever channel it arrived on.

    This is the only place that increments the lockout counter for a bad
    credential. A cookie, a ``Bearer`` header on any endpoint and a WebSocket
    handshake all land here, so an attacker cannot reset the count by changing
    endpoint or by moving from HTTP to ``/ws``.

    Args:
        client_ip: Address the credential came from.
        resource: Path that was being reached.
        source: Channel the credential arrived on, for the audit record.
        lockout_key: What to count the failure against, when it is not the
            address (:func:`failure_key`).
    """
    key = lockout_key or client_ip
    protection = get_brute_force_protection()
    if protection is not None:
        protection.record_failure(key)

    detail = f"invalid credential presented via {source}"
    if key.startswith(FLEET_LOCKOUT_PREFIX):
        detail = (
            f"revoked or expired fleet token {key.removeprefix(FLEET_LOCKOUT_PREFIX)} "
            f"presented via {source}; counted under the token, not the address"
        )
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="auth.credential",
            result="denied",
            client_ip=client_ip,
            resource=resource,
            detail=detail,
        )
