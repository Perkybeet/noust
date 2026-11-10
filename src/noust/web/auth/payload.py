# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The session payload every request resolves to: whose it is, what it may do, whether it
is elevated, and the error a refused credential raises.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

from noust.web.auth.clock import _now
from noust.web.auth.config import get_global_token_manager
from noust.web.auth.constants import API_TOKEN_SID_PREFIX, FLEET_SCOPE, MASTER_SID, SCOPE_RANK
from noust.web.permissions import Permission
from noust.web.permissions.enforce import has_permission
from noust.web.permissions.principal import KIND_MASTER, principal_of
from noust.web.permissions.roles import GRANT_COMPAT, GRANT_PERMISSIONS, legacy_scope


def extend_elevation(payload: dict[str, Any]) -> float | None:
    """
    Keep a session's sudo mode open after an elevated action; see :meth:`TokenManager.extend_elevation`.

    Args:
        payload: The payload of the session that just performed one.

    Returns:
        The new deadline, or None when nothing moved, including when the
        server was never initialised.
    """
    manager = get_global_token_manager()
    if manager is None:
        return None
    return manager.extend_elevation(payload)


def is_elevated(payload: dict[str, Any]) -> bool:
    """
    Report whether a session payload is currently inside its sudo-mode window.

    Args:
        payload: A verified session payload, as :func:`require_auth` builds
            it. Only a cookie session ever carries a meaningful
            ``elevated_until``; see :func:`noust.web.api.deps.ensure_elevated`
            for who is asked at all.

    Returns:
        True while the session is inside its sudo-mode window: confirmed
        through ``POST /api/auth/elevate`` (or a passkey) and kept open by the
        elevated actions since, per :meth:`TokenManager.extend_elevation`.
        Reading this changes nothing.
    """
    elevated_until = payload.get("elevated_until")
    if elevated_until is None:
        return False
    return float(elevated_until) > _now()


def scope_satisfies(granted: str, required: str) -> bool:
    """
    Report whether a granted scope covers a required one.

    Args:
        granted: Scope carried by the credential.
        required: Scope the operation demands.

    Returns:
        True when the grant is at or above the requirement. An unknown scope
        on either side fails closed, and so does a raw ``fleet`` scope: an
        admitted fleet payload carries the scope :func:`admit_fleet` gave it,
        so one still saying ``fleet`` skipped admission and is worth nothing.
    """
    wanted = SCOPE_RANK.get(required)
    if wanted is None or granted == FLEET_SCOPE:
        return False
    return SCOPE_RANK.get(granted, -1) >= wanted


def sees_command_lines(payload: Mapping[str, Any]) -> bool:
    """
    Decide whether a credential may read other processes' command lines.

    A process's argv routinely carries a secret it was started with - a
    database password or an API token passed as a flag - and every process
    listing the panel serves would otherwise hand it to a ``read`` token given
    to a dashboard. The policy, stated once for every listing: ``admin`` sees
    the command line, anything less sees the process name.

    Args:
        payload: The authenticated payload.

    Returns:
        True for a credential holding ``secrets.reveal``: an admin, the
        master token, and an ``admin`` token of 3.0.
    """
    return has_permission(payload, Permission.SECRETS_REVEAL)


def actor_label(session: Mapping[str, Any]) -> str:
    """
    A short, non-secret label naming who is behind a session payload.

    The one way an action records who did it: every audit record, every
    audit log line and every job's actor go through here, so the Activity
    page can filter the audit log by the same actor a job shows. Reading the
    payload by hand is how three routers came to log ``session=unknown`` for
    every call - they read a ``session_id`` key that payloads never had.

    A credential is named by what it is, as the audit trail names its principal
    (:func:`noust.web.permissions.principal.principal_of`): an account by its username,
    an API token by ``token:<name>``, the master token by ``master`` - in a browser as
    well as a Bearer. The master token in a browser used to be twelve characters of its
    session's id here and ``master`` in the audit trail, and the console drew the one
    credential under two names (item 152). Which browser it was is in the sessions list
    and in the audit trail's own ``source``; a name says who acted.

    Args:
        session: The authenticated session payload, as :func:`require_auth`
            builds it.

    Returns:
        The username of an account's session, ``"master"``,
        ``"token:<name>"``, or ``"<fleet token name> on behalf of <actor>"`` for a
        central acting for one of its operators; the first 12 characters of the id of
        a session that is none of those.
    """
    on_behalf_of = session.get("on_behalf_of")
    if session.get("fleet") and on_behalf_of:
        return f"{session.get('token_name') or 'fleet'} on behalf of {on_behalf_of}"
    if session.get("account_id") is not None and session.get("username"):
        # A person, by the name they sign in with: the audit trail names who
        # acted, not which of their sessions (ENS op.exp.8, art. 24.3).
        return str(session["username"])
    sid = str(session.get("sid") or "unknown")
    if sid in (MASTER_SID, "unknown") or sid.startswith(API_TOKEN_SID_PREFIX):
        return sid
    principal = principal_of(session)
    if principal.kind == KIND_MASTER:
        return principal.name
    return sid[:12]


class CredentialRefused(HTTPException):
    """
    A real credential presented where, or how, it is not accepted.

    Not a guess, so it is audited but never counted by the lockout: a fleet
    token from outside its tunnel, an API token from outside its networks or
    whose owner was disabled, the master token as a Bearer under the ENS
    profile. Raised where the credential is resolved, so no endpoint can
    accept one. It is an ``HTTPException`` because the API's error boundary
    already answers those in the one contract, and a dict ``detail`` carries
    its own ``error`` code there.

    Attributes:
        reason: The sentence answered, for the audit record.
        action: The audit action it is recorded under.
    """

    def __init__(
        self, status_code: int, error: str, detail: str, hint: str, *, action: str = "auth.fleet"
    ) -> None:
        """
        Args:
            status_code: 401 for a credential from the wrong place, 400 for a
                malformed header, 403 for a refused operation.
            error: Machine-readable code for the error contract.
            detail: What was refused.
            hint: What to do instead.
            action: The audit action.
        """
        super().__init__(
            status_code=status_code,
            detail={"error": error, "detail": detail, "hint": hint, "fields": None},
            headers={"WWW-Authenticate": "Bearer"} if status_code == 401 else None,
        )
        self.reason = detail
        self.action = action


class FleetCredentialRefused(CredentialRefused):
    """A fleet token presented where, or how, it is not accepted."""


def is_fleet(payload: Mapping[str, Any]) -> bool:
    """
    Report whether a payload was authenticated by a central's fleet token.

    Args:
        payload: A verified payload.

    Returns:
        True for an admitted fleet payload, or a raw one that still says so.
    """
    return bool(payload.get("fleet")) or payload.get("scope") == FLEET_SCOPE


def master_payload(
    client_ip: str | None, generation: str | None = None, grant: str = GRANT_COMPAT
) -> dict[str, Any]:
    """
    The payload the master token authenticates, wherever it is presented.

    Args:
        client_ip: Address presenting it.
        generation: Which issue of the master token it was, from
            :meth:`TokenManager.master_token_generation`, so a stream can
            tell later whether it has been rotated since.
        grant: How it holds the console (:meth:`TokenManager.master_grant`).

    Returns:
        A payload named :data:`MASTER_SID` with the grant's permissions.
    """
    permissions = GRANT_PERMISSIONS[grant]
    return {
        "type": "master",
        "sid": MASTER_SID,
        "ip": client_ip,
        "scope": legacy_scope(permissions),
        "generation": generation,
        "grant": grant,
        "permissions": permissions,
        # The one credential 3.0 exempted from sudo mode as a Bearer, kept
        # for compatibility; the ENS profile refuses the Bearer outright.
        "elevation_exempt": True,
    }


def credential_is_current(payload: Mapping[str, Any]) -> bool:
    """
    Re-check a credential verified earlier; see :meth:`TokenManager.credential_is_current`.

    Args:
        payload: The payload a stream was opened with.

    Returns:
        True while it would still be accepted. False when the server was
        never initialised: a stream nobody can vouch for is not current.
    """
    manager = get_global_token_manager()
    if manager is None:
        return False
    return manager.credential_is_current(payload)


def credential_key(payload: Mapping[str, Any]) -> str:
    """
    Name the credential behind a payload for per-credential accounting.

    Args:
        payload: A verified payload.

    Returns:
        ``master``, ``token:<name>``, or ``session:<family>`` - the sign-in,
        not the sid, so the connections a session opened before and after a
        renewal count against one budget. A fleet token is keyed per actor
        too: one token carries every operator of a central, and eight log
        streams shared by all of them would be one busy operator's worth.
    """
    if payload.get("type") == "session":
        return f"session:{payload.get('family') or payload.get('sid')}"
    if payload.get("fleet") and payload.get("on_behalf_of"):
        return f"{payload.get('sid') or 'unknown'}#{payload['on_behalf_of']}"
    return str(payload.get("sid") or "unknown")
