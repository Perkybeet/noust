# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central itself over HTTP: its role and its sealed secrets.

A thin layer over :mod:`noust.central` and :mod:`noust.core.sealing`
(rule 3). The console reads the central's state in the session payload
(:func:`central_state`) and unlocks it here; ``noust central unlock`` is the
other door, over a local socket, into the same :func:`noust.core.sealing.unlock`.

Unlocking asks for a signed-in ``admin`` session in sudo mode and the
passphrase. Both are available on a locked central: sealing covers the
secret store (node keys and tokens, integration keys, backup passwords),
while the console's own credentials - the signing key, the master token, the
two-factor state and the sessions - live in the web state directory and are
never sealed. So a locked central still signs its operator in and confirms
sudo mode, and the passphrase is never the only thing between a stolen
browser tab and every node's key. A wrong passphrase is counted by the same
lockout as a wrong credential, and every attempt is audited without it.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from noust.core import sealing
from noust.core.exceptions import ConfigError
from noust.core.secrets import secrets_dir
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label, get_audit_logger, get_client_ip, record_auth_failure

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

#: The path unlock attempts are audited and counted under.
UNLOCK_RESOURCE = "/api/central/unlock"


class CentralInfo(BaseModel):
    """
    What the console needs to know about the central it is signed in to.

    Attributes:
        role: ``server`` (deploys applications here too) or ``hub`` (manages
            other servers only; the console hides local deployments).
        sealed: Whether the secret store is sealed under a passphrase.
        locked: Whether it is sealed and this process has not been unlocked:
            no node can be reached until it is.
    """

    role: str
    sealed: bool
    locked: bool


class UnlockRequest(BaseModel):
    """
    Body of ``POST /api/central/unlock``.

    Attributes:
        passphrase: The passphrase the store was sealed with. Never logged.
    """

    passphrase: str = Field(min_length=1, max_length=1024)


def central_state() -> CentralInfo:
    """
    Describe the central: :func:`noust.central.central_info`, as a model.

    Returns:
        Its role and its secrets' state.

    Raises:
        ConfigError: When ``central.role`` holds something that is not a role.
    """
    from noust.central import central_info

    info = central_info()
    return CentralInfo(role=info["role"], sealed=info["sealed"], locked=info["locked"])


def session_central() -> CentralInfo | None:
    """
    Describe the central for ``GET /api/auth/session``, never failing it.

    The session is how the console starts at all; a mistyped
    ``central.role`` must not keep the operator from signing in to fix it.

    Returns:
        The description, or None when the role cannot be read (logged).
    """
    try:
        return central_state()
    except ConfigError as exc:
        logger.warning("The central's role cannot be read: %s", exc.message)
        return None


def _audit(request: Request, session: dict[str, Any], result: str, detail: str) -> None:
    """
    Record an unlock attempt. The passphrase is never part of it.

    Args:
        request: The incoming request.
        session: The credential that asked.
        result: ``success`` or ``denied``.
        detail: What happened.
    """
    audit = get_audit_logger()
    if audit is not None:
        audit.record(
            action="central.unlock",
            result=result,
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=UNLOCK_RESOURCE,
            detail=detail,
        )


@router.post("/unlock", response_model=CentralInfo)
def unlock_central(
    body: UnlockRequest,
    request: Request,
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> CentralInfo:
    """
    Unlock the central's sealed secrets with their passphrase.

    Needs an ``admin`` credential in sudo mode; see the module docstring for
    why both are available while the central is locked. Tunnels open lazily
    afterwards, on the next request that needs a node.

    Args:
        body: The passphrase.
        request: The incoming request, for the audit record and the lockout.
        session: The authenticated, elevated session.

    Returns:
        The central's state, now unlocked.

    Raises:
        WrongPassphraseError: 403 ``wrong_passphrase``; counted towards the
            lockout like a wrong credential.
        SealError: 409 when the store is not sealed or its header is damaged.
    """
    try:
        sealing.unlock(secrets_dir(), body.passphrase)
    except sealing.WrongPassphraseError:
        record_auth_failure(get_client_ip(request), UNLOCK_RESOURCE, "passphrase")
        _audit(request, session, "denied", "wrong passphrase")
        raise
    except sealing.SealError as exc:
        _audit(request, session, "denied", exc.message)
        raise
    _audit(request, session, "success", "the sealed secrets were unlocked")
    return central_state()
