# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The GitHub access a central leases to this server: ``/api/sources/leases``.

A thin layer over :class:`~noust.integrations.github.leases.SourceLeases` (rule 3). The GitHub App
lives on the central and its private key never leaves it; the central gives a server it manages
an installation token scoped to the repositories the server serves, readable for contents only,
good for an hour, and renews it before it ends (:mod:`noust.fleet.sources`). This is where the
server takes it.

- **Only a central leases.** A person with the permission to deploy cannot put a token here: it would
  be used to clone other teams' repositories on this server. The route checks that the caller is
  a fleet token, and the ceiling (``deploy``) does the rest.
- **The token goes in and never comes out.** It is a sealed secret file; the answer and the audit
  trail say which lease, for how many repositories and until when.
- **A server's operator can read what it holds**, when each ends and when each central last called,
  which is what a clone that fails for want of one is explained by.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.server_text import ServerText, say
from noust.integrations.github.leases import Lease, SourceLeases
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute, require_auth
from noust.web.api.managed_settings import CentralCallOut, _centrals, central_of
from noust.web.auth.payload import is_fleet

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict[str, Any], Depends(require_auth)]


class LeaseIn(BaseModel):
    """
    A token a central leases to this server.

    Attributes:
        key: The central's name for this lease (host, installation and part); leasing the same
            key again replaces it.
        host: The GitHub host the token is for.
        installation_id: The installation it was issued from.
        repositories: ``owner/repo`` it reads.
        token: The installation token. A secret: kept sealed, never answered back.
        expires_in: Seconds it lives from now; at most two hours.
        permissions: What it may do; only ``{"contents": "read"}`` is accepted.
    """

    key: str = Field(max_length=200)
    host: str = Field(max_length=300)
    installation_id: int
    repositories: list[str] = Field(max_length=2000)
    token: str = Field(max_length=2000, repr=False)
    expires_in: int
    permissions: dict[str, str] | None = None


class LeaseOut(BaseModel):
    """
    A lease this server holds. Never the token.

    Attributes:
        central: The central that leased it.
        key: Its name.
        host: The GitHub host.
        installation_id: The installation.
        repositories: What it reads, sorted.
        permissions: What it may do.
        received_at: When it arrived.
        expires_at: When it stops being used, on this server's clock.
        expires_in: Seconds left; 0 once it ended.
        ended: Whether it ended: a clone of its repositories fails until the central renews it.
    """

    central: str
    key: str
    host: str
    installation_id: int
    repositories: list[str]
    permissions: dict[str, str]
    received_at: str
    expires_at: str
    expires_in: int
    ended: bool


class LeasesOut(BaseModel):
    """
    Every lease this server holds, and the centrals that call it.

    Attributes:
        items: The leases, by central and key.
        centrals: The centrals that call this server and when each last did.
    """

    items: list[LeaseOut]
    centrals: list[CentralCallOut] = Field(default_factory=list)


class LeasesDroppedOut(BaseModel):
    """
    The answer to taking leases back.

    Attributes:
        dropped: How many were forgotten.
    """

    dropped: int


def _stamp(moment: float) -> str:
    """
    Args:
        moment: Epoch seconds.

    Returns:
        ISO 8601 UTC.
    """
    return datetime.fromtimestamp(moment, timezone.utc).isoformat()


def lease_out(lease: Lease, now: float | None = None) -> LeaseOut:
    """
    Args:
        lease: A lease held.
        now: Epoch seconds; the clock by default.

    Returns:
        Its API shape.
    """
    moment = time.time() if now is None else now
    return LeaseOut(
        central=lease.central,
        key=lease.key,
        host=lease.host,
        installation_id=lease.installation_id,
        repositories=list(lease.repositories),
        permissions=dict(lease.permissions),
        received_at=_stamp(lease.received_at),
        expires_at=_stamp(lease.expires_at),
        expires_in=max(int(lease.expires_at - moment), 0),
        ended=lease.expires_at <= moment,
    )


def _central_only(session: dict[str, Any]) -> str:
    """
    Args:
        session: The authenticated payload.

    Returns:
        The central the request is from.

    Raises:
        HTTPException: 403 ``central_only`` for anyone who is not a central.
    """
    if not is_fleet(session):
        text: ServerText = say("sources.central_only")
        raise HTTPException(
            status_code=403,
            detail={
                "error": "central_only",
                "detail": text.text,
                "hint": text.hint,
                "fields": None,
                "coded": text.to_dict(),
            },
        )
    return central_of(session)


@router.get("/leases", response_model=LeasesOut)
def list_leases(session: Session) -> LeasesOut:
    """
    List the GitHub access centrals have leased to this server.

    Args:
        session: The authenticated session.

    Returns:
        Each lease with when it ends, and the centrals that call this server.
    """
    now = time.time()
    return LeasesOut(
        items=[lease_out(lease, now) for lease in SourceLeases().list()], centrals=_centrals()
    )


@router.put("/leases", response_model=LeaseOut)
def hold_lease(body: LeaseIn, session: Session) -> LeaseOut:
    """
    Take a token a central leases to this server, replacing the lease of the same key.

    Args:
        body: The lease.
        session: The authenticated session; only a central's.

    Returns:
        The lease, without its token.

    Raises:
        HTTPException: 403 ``central_only`` for anyone but a central.
        ValidationError: 400 when it is not a lease this server will keep.
    """
    central = _central_only(session)
    lease = SourceLeases().hold(
        central=central,
        key=body.key,
        host=body.host,
        installation_id=body.installation_id,
        repositories=body.repositories,
        token=body.token,
        expires_in=body.expires_in,
        permissions=body.permissions,
    )
    audit.record(
        "sources.lease.hold",
        actor=session_actor(session),
        target=f"central:{central}",
        details={
            "key": lease.key,
            "host": lease.host,
            "installation": lease.installation_id,
            "repositories": len(lease.repositories),
            "expires_in": body.expires_in,
        },
    )
    return lease_out(lease)


@router.delete("/leases", response_model=LeasesDroppedOut)
def drop_leases(
    session: Session,
    key: Annotated[str | None, Query(max_length=200, description="Only this lease")] = None,
) -> LeasesDroppedOut:
    """
    Forget leases of the calling central, one or all.

    Args:
        session: The authenticated session; only a central's, and only its own leases.
        key: Only this lease; every one of the central's when omitted.

    Returns:
        How many were forgotten.

    Raises:
        HTTPException: 403 ``central_only`` for anyone but a central.
    """
    central = _central_only(session)
    dropped = SourceLeases().drop(central, key)
    if dropped:
        audit.record(
            "sources.lease.drop",
            actor=session_actor(session),
            target=f"central:{central}",
            details={"key": key or "*", "dropped": dropped},
        )
    return LeasesDroppedOut(dropped=dropped)
