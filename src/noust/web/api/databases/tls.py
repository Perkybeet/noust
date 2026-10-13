# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances/{name}/tls``: an instance that serves TLS.

An instance can serve TLS with a certificate signed by Noust's own authority;
an application linked with ``verify-ca`` or ``verify-full`` checks it against
that authority's public certificate. All of it is
:class:`~noust.managers.database.instance_tls.InstanceTls`, which ``noust db
instance tls`` calls too. Reading the status and planning change nothing and
answer at once; turning TLS on, off or over, and renewing the certificate,
restart the engine, ask for sudo mode and run as jobs.

These are the server's, not a team's: the authority is one for the whole server
and an instance is nobody's.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.managers.database.instance_tls import InstanceTls
from noust.managers.database.managed import ManagedInstances
from noust.managers.database.tls_authority import TlsAuthority
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.databases.jobs import tls_job
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class TlsModeRequest(BaseModel):
    """
    A mode to serve TLS in.

    Attributes:
        mode: ``off``; ``optional`` (a client may ask for TLS); or ``required``
            (nothing else gets in).
    """

    mode: str = Field(..., max_length=16)


class CertificateResponse(BaseModel):
    """
    What a certificate says of itself.

    Attributes:
        days_left: Whole days until it expires; negative once it has.
        expiring: Whether Noust is about to renew it.
    """

    path: str
    subject: str
    issuer: str
    names: list[str] = Field(default_factory=list)
    not_before: str
    not_after: str
    days_left: int
    expiring: bool
    fingerprint: str


class TlsStatusResponse(BaseModel):
    """
    How an instance serves TLS.

    Attributes:
        mode: ``off``, ``optional`` or ``required``.
        certificate: The instance's certificate; null while the mode is ``off``.
        ca: The authority's public certificate, the file applications are
            pointed at; null while the mode is ``off``.
        supported: Each mode, with the reason the engine cannot serve it, or
            null when it can.
    """

    name: str
    key: str
    mode: str
    certificate: CertificateResponse | None = None
    ca: CertificateResponse | None = None
    supported: dict[str, str | None] = Field(default_factory=dict)


class TlsPlanResponse(BaseModel):
    """
    What changing an instance's TLS would do. Nothing was changed.

    Attributes:
        restarts: Whether the engine restarts.
        apps: The applications that use its databases and lose their connections
            for the length of the restart.
        names: The names the certificate will carry.
        blockers: What stops the change.
        warnings: What the operator should know first.
    """

    name: str
    mode: str
    current: str
    restarts: bool
    apps: list[str] = Field(default_factory=list)
    names: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class TlsAuthorityResponse(BaseModel):
    """Noust's certificate authority: null until an instance first serves TLS."""

    authority: CertificateResponse | None = None


def _tls(session: dict[str, Any]) -> InstanceTls:
    """
    Build what a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The TLS manager over the request's database service.
    """
    return InstanceTls(ManagedInstances(service(session)))


@router.get("/instances/tls-authority", response_model=TlsAuthorityResponse)
def get_authority(session: Annotated[dict, Depends(get_current_session)]) -> TlsAuthorityResponse:
    """
    Show Noust's certificate authority, the one applications are told to trust.

    Args:
        session: The authenticated session.

    Returns:
        Its public certificate, or null when none has been made yet.
    """
    found = TlsAuthority().existing()
    return TlsAuthorityResponse(authority=CertificateResponse(**found.to_dict()) if found else None)


@router.get("/instances/{name}/tls", response_model=TlsStatusResponse)
def get_tls(name: str, session: Annotated[dict, Depends(get_current_session)]) -> TlsStatusResponse:
    """
    Say how an instance serves TLS.

    Args:
        name: The instance.
        session: The authenticated session.

    Returns:
        Its mode, its certificate and the authority's, and which modes its engine can serve.

    Raises:
        DatabaseNotFoundError: There is no such instance.
    """
    return TlsStatusResponse(**_tls(session).status(name))


@router.post("/instances/{name}/tls/plan", response_model=TlsPlanResponse)
def plan_tls(
    name: str, request: TlsModeRequest, session: Annotated[dict, Depends(get_current_session)]
) -> TlsPlanResponse:
    """
    Say what serving TLS in a mode would do. Changes nothing.

    Args:
        name: The instance.
        request: The mode.
        session: The authenticated session.

    Returns:
        The plan, with the applications that lose their connections and what blocks it.

    Raises:
        ValidationError: The mode is not one.
        DatabaseNotFoundError: There is no such instance.
    """
    return TlsPlanResponse(**_tls(session).plan(name, request.mode).to_dict())


@router.put("/instances/{name}/tls", response_model=JobAcceptedResponse, status_code=202)
def set_tls(
    name: str, request: TlsModeRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue making an instance serve TLS in a mode, with sudo mode.

    The engine restarts; when it does not come up the previous configuration is
    put back and the job fails with the engine's own log.

    Args:
        name: The instance.
        request: The mode.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: The mode is not one, or the engine cannot serve it.
        DatabaseNotFoundError: There is no such instance.
    """
    plan = _tls(session).plan(name, request.mode)
    if plan.blockers:
        # The refusal the job would give, now rather than in a failed job.
        _tls(session).set_mode(name, request.mode)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Serve TLS as {request.mode} on {name}",
        description=f"Restarting {name} with TLS {request.mode}",
        func=tls_job,
        kwargs={"name": name, "mode": request.mode},
        metadata={"instance": name, "action": "tls", "mode": request.mode},
        message=f"{name} will serve TLS as {request.mode}",
    )


@router.post("/instances/{name}/tls/renew", response_model=JobAcceptedResponse, status_code=202)
def renew_tls(
    name: str, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue signing an instance's certificate again, with sudo mode.

    Args:
        name: The instance.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        DatabaseNotFoundError: There is no such instance.
    """
    _tls(session).managed.get(name)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Renew the certificate of {name}",
        description=f"Restarting {name} on a new certificate",
        func=tls_job,
        kwargs={"name": name},
        metadata={"instance": name, "action": "renew-tls"},
        message=f"{name} will serve a new certificate",
    )
