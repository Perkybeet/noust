# Copyright (c) 2024-2026 Yago López Prado
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
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, create_model

from noust.central.features import FEATURES
from noust.core import sealing
from noust.core.exceptions import ConfigError, NoustError
from noust.core.secrets import secrets_dir
from noust.web.api.deps import CodedText, NoustErrorRoute, require_elevated
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


class FeatureReason(CodedText):
    """
    Why a function is not available here, as a sentence with a code.

    Attributes:
        text: The sentence in English, always sent.
        hint: How to fix it in English, when the sentence has a fix.
    """

    text: str
    hint: str | None = None


class FeatureState(BaseModel):
    """
    Whether one function of this Noust works here.

    Attributes:
        available: It would work here now.
        reason: Why it would not: ``features.hub_role`` on a hub, ``features.needs_*`` for what
            the machine lacks, and the engine's own sentences (``docker.*``) for Docker. None when
            ``available``.
    """

    available: bool
    reason: FeatureReason | None = None


def _feature_states() -> type[BaseModel]:
    """
    Build the model of every function's state, one property each.

    The table of functions (:mod:`noust.central.features`) is the one list, so the model is made
    from it: the OpenAPI schema, and the console's types generated from it, name each function and
    cannot name one that is not there.

    Returns:
        The model.
    """
    states: type[BaseModel] = create_model(  # type: ignore[call-overload]
        "FeatureStates", **dict.fromkeys(FEATURES, (FeatureState, ...))
    )
    return states


if TYPE_CHECKING:

    class FeatureStates(BaseModel):
        """What the model made from the table has: a :class:`FeatureState` for each key."""

        def __getattr__(self, key: str) -> FeatureState:
            """
            Args:
                key: A function's key.

            Returns:
                Its state.
            """

else:
    FeatureStates = _feature_states()


class SessionCentral(CentralInfo):
    """
    What the session says of the central: :class:`CentralInfo` and what it can do.

    Attributes:
        features: For each function of this Noust, whether it works here and why not. Absent from a
            server older than 4.3, which the console reads as "what its role allows", as it always
            did.
    """

    features: FeatureStates | None = None


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
        Its role and its secrets' state. What it can do is not asked here: unlocking a sealed
        central must not wait on a look at the machine.

    Raises:
        ConfigError: When ``central.role`` holds something that is not a role.
    """
    from noust.central import central_info

    info = central_info(with_features=False)
    return CentralInfo(role=info["role"], sealed=info["sealed"], locked=info["locked"])


def session_central() -> SessionCentral | None:
    """
    Describe the central for ``GET /api/auth/session``, never failing it.

    The session is how the console starts at all; a mistyped ``central.role`` must not keep the
    operator from signing in to fix it, nor a machine that cannot be read keep the console from
    starting.

    Returns:
        The description with what each function can do here, or None when the role cannot be read
        (logged). When only the machine cannot be read the description has no ``features`` (logged),
        which the console reads as what the role allows, as it did before 4.3.
    """
    from noust.central import central_info, feature_states

    try:
        info = central_info(with_features=False)
    except ConfigError as exc:
        logger.warning("The central's role cannot be read: %s", exc.message)
        return None
    try:
        info["features"] = feature_states()
    except (NoustError, OSError) as exc:
        # An error boundary: what the machine has is read through its managers, and a fault in
        # one of them must not take the sign-in with it.
        logger.warning("What this machine can do could not be read: %s", exc)
    return SessionCentral(**info)


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
