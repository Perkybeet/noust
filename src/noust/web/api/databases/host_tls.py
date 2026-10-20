# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/engines/{engine}/tls``: a host engine that serves a certificate clients can verify (C-27).

A client of :class:`~noust.managers.database.host_tls.HostTls`, like ``noust db tls``: the same checks
of the certificate and its key, the same proof that the engine presents the certificate it was given,
in the same words. Reading the state and planning are requests; giving an engine a certificate,
taking it off and refreshing the copy after a renewal reload or restart the engine and wait for it to
answer, so they are jobs with sudo mode. A restart is refused without ``confirm`` before the job
exists, as ``confirmation_required``, whoever asks (the guard is in the manager too).

Not to be confused with ``/api/databases/instances/{name}/tls``, the certificate of Noust's own
authority that an engine in a container serves: that one is for engines Noust runs itself, this one
for the PostgreSQL, MySQL and MariaDB of the server, with the certificate of a name clients verify
(Let's Encrypt's, or one the operator uploaded).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import ConfirmationRequired, DatabaseError, ValidationError
from noust.managers.database.host_tls import ENGINES, HostTls
from noust.managers.database.service import DatabaseService
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType

router = APIRouter(route_class=NoustErrorRoute)


class HostTlsRequest(BaseModel):
    """
    The certificate to give an engine.

    Attributes:
        hostname: The name clients reach the engine by; the certificate must cover it.
        source: ``acme`` (the Let's Encrypt lineage named after the host), ``lineage`` (another
            lineage) or ``custom`` (a certificate uploaded to Noust).
        certificate: The lineage's or the upload's name, for ``lineage`` and ``custom``.
        confirm: The operator accepted the engine's restart, where there is one (MySQL and MariaDB
            read the certificate only when they start; PostgreSQL reloads).
    """

    hostname: str = Field(..., min_length=1, max_length=253)
    source: str = Field(default="acme", max_length=16)
    certificate: str | None = Field(default=None, max_length=128)
    confirm: bool = False


class HostTlsPlanResponse(BaseModel):
    """
    What giving an engine a certificate would do. Nothing was changed.

    Attributes:
        engine: The engine.
        hostname: The name clients reach it by.
        source: Where the certificate comes from.
        certificate: The lineage's or the upload's name.
        fullchain: The certificate chain that will be copied.
        directory: The engine's own copy of it.
        fingerprint: The certificate's SHA-256 fingerprint, which the engine is asked to present.
        not_after: When it expires.
        restart: Whether the engine restarts.
    """

    engine: str
    hostname: str
    source: str
    certificate: str | None = None
    fullchain: str
    directory: str
    fingerprint: str
    not_after: str
    restart: bool


class HostTlsStatusResponse(BaseModel):
    """
    What an engine is given, and whether it still matches its source.

    Attributes:
        engine: The engine.
        enabled: It serves a certificate of Noust's.
        record: What was given: host name, source, fingerprint, expiry, last refresh.
        warnings: What the operator should know (a renewal not loaded, an expiry near).
        source_changed: The source certificate was renewed or replaced and the engine still serves
            the old copy: refresh it.
        served: The fingerprint the engine presents now, asked of the engine itself.
        days_left: Days until the certificate expires.
    """

    engine: str
    enabled: bool
    record: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    source_changed: bool | None = None
    served: str | None = None
    days_left: int | None = None


def _engine_name(engine: str) -> str:
    """
    Check the engine a path names.

    Args:
        engine: The path's ``{engine}``.

    Returns:
        It, unchanged.

    Raises:
        ValidationError: 400 for an engine Noust cannot give a certificate to.
    """
    if engine not in ENGINES:
        raise ValidationError(
            f"{engine} cannot be given a certificate by Noust",
            details=f"PostgreSQL, MySQL and MariaDB can ({', '.join(ENGINES)}).",
            field="engine",
        )
    return engine


Engine = Annotated[str, Depends(_engine_name)]


def _tls_of(databases: DatabaseService, engine: str) -> HostTls:
    """
    Build the manager over a service.

    Args:
        databases: The service.
        engine: The engine.

    Returns:
        The manager over the server's engine.
    """
    return HostTls(databases.manager(engine))


def _tls(session: dict[str, Any], engine: str) -> HostTls:
    """
    Build the manager every route here uses.

    Args:
        session: The authenticated session.
        engine: The engine.

    Returns:
        The manager over the server's engine.
    """
    return _tls_of(service(session), _engine_name(engine))


def host_tls_job(
    engine: str | None,
    action: str,
    hostname: str | None = None,
    source: str = "acme",
    certificate: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Give an engine a certificate, take it off, or bring every engine's copy up to its source.

    Each reloads or restarts the engine and waits for it to answer, which is work that must not sit
    on a request.

    Args:
        engine: The engine; None for a refresh of every engine.
        action: ``enable``, ``disable`` or ``refresh``.
        hostname: The name clients reach the engine by (``enable``).
        source: ``acme``, ``lineage`` or ``custom`` (``enable``).
        certificate: The lineage's or the upload's name (``enable``).
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The engine's status afterwards; for a refresh, what happened to each engine.
    """
    from noust.web.api.databases.jobs import _done, _service

    databases = _service(job_context, f"Certificate of {engine or 'every engine'}", actor)
    if action == "refresh":
        report: dict[str, Any] = {}
        for name in ENGINES:
            report.update(_tls_of(databases, name).refresh())
        _done(job_context)
        return report
    manager = _tls_of(databases, _engine_name(engine or ""))
    if action == "enable":
        result = manager.enable(
            hostname or "", confirmed=True, source=source, certificate=certificate
        )
    else:
        result = manager.disable(confirmed=True)
    _done(job_context)
    return result


@router.get("/engines/tls", response_model=list[HostTlsStatusResponse])
def list_status(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[HostTlsStatusResponse]:
    """
    Say which engines serve a certificate of Noust's, and whether each is current.

    Args:
        session: The authenticated session.

    Returns:
        One entry per engine Noust can give a certificate to; one that is not installed says so in
        its warnings.
    """
    views = []
    for engine in ENGINES:
        try:
            views.append(HostTlsStatusResponse(**_tls(session, engine).status()))
        except DatabaseError as exc:
            views.append(HostTlsStatusResponse(engine=engine, enabled=False, warnings=[str(exc)]))
    return views


@router.post("/engines/tls/refresh", response_model=JobAcceptedResponse, status_code=202)
def refresh(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Queue bringing each engine's copy up to its certificate and proving what it presents, with sudo mode.

    certbot runs the same thing after every renewal that succeeds; run it after uploading a new
    certificate.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The queued job; its result says, per engine, whether the renewed certificate was loaded.
    """
    return queue(
        session,
        job_type=JobType.DATABASE,
        name="Refresh the certificates of the engines",
        description="Loading the renewed certificate into the engines that serve one",
        func=host_tls_job,
        kwargs={"engine": None, "action": "refresh"},
        metadata={"action": "host-tls-refresh"},
        message="The engines will present the renewed certificate",
    )


@router.get("/engines/{engine}/tls", response_model=HostTlsStatusResponse)
def get_status(
    engine: Engine, session: Annotated[dict, Depends(get_current_session)]
) -> HostTlsStatusResponse:
    """
    Say what an engine is given and whether it still matches its source.

    Args:
        engine: ``postgresql`` or ``mysql`` (MySQL and MariaDB).
        session: The authenticated session.

    Returns:
        The state, with the fingerprint the engine presents asked of the engine itself.

    Raises:
        ValidationError: 400 for an engine Noust cannot give a certificate to.
    """
    return HostTlsStatusResponse(**_tls(session, engine).status())


@router.post("/engines/{engine}/tls/plan", response_model=HostTlsPlanResponse)
def plan_tls(
    engine: Engine,
    request: HostTlsRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> HostTlsPlanResponse:
    """
    Check a certificate for an engine and say what giving it would do. Changes nothing.

    The certificate and its key are checked (the key is its own, it covers the name, it has not
    expired).

    Args:
        engine: ``postgresql`` or ``mysql``.
        request: The name and where the certificate comes from.
        session: The authenticated session.

    Returns:
        The files, the fingerprint, the expiry and whether the engine restarts.

    Raises:
        ValidationError: 400 when a check fails.
        DatabaseEngineError: When the engine is not running.
    """
    return HostTlsPlanResponse(
        **_tls(session, engine).plan(
            request.hostname, source=request.source, certificate=request.certificate
        )
    )


@router.put("/engines/{engine}/tls", response_model=JobAcceptedResponse, status_code=202)
def enable_tls(
    engine: Engine,
    request: HostTlsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue giving an engine the certificate of a host name, with sudo mode.

    The certificate is copied for the engine's account and the engine is told to use the copy
    (PostgreSQL reloads; MySQL and MariaDB restart once); then the engine is asked what it presents.
    When it presents another, its settings are put back and the job fails.

    Args:
        engine: ``postgresql`` or ``mysql``.
        request: The name, where the certificate comes from, and the restart accepted.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: 400 when a check fails.
        ConfirmationRequired: 400 ``confirmation_required`` when the engine restarts and the
            operator has not accepted it.
    """
    manager = _tls(session, engine)
    plan = manager.plan(request.hostname, source=request.source, certificate=request.certificate)
    if plan["restart"] and not request.confirm:
        raise ConfirmationRequired(
            [
                f"Giving {engine} a certificate restarts it: it reads the certificate only when it "
                "starts, so every connection is dropped for a few seconds."
            ]
        )
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Serve the certificate of {request.hostname} on {engine}",
        description=f"Giving {engine} the certificate of {request.hostname}",
        func=host_tls_job,
        kwargs={
            "engine": engine,
            "action": "enable",
            "hostname": request.hostname,
            "source": request.source,
            "certificate": request.certificate,
        },
        metadata={"engine": engine, "action": "host-tls-enable", "hostname": request.hostname},
        message=f"{engine} will serve the certificate of {request.hostname}",
    )


@router.delete("/engines/{engine}/tls", response_model=JobAcceptedResponse, status_code=202)
def disable_tls(
    engine: Engine,
    session: Annotated[dict, Depends(require_elevated)],
    confirm: Annotated[bool, Query(description="Accept the engine's restart")] = False,
) -> JobAcceptedResponse:
    """
    Queue taking the certificate off an engine: its settings go back as they were. Sudo mode.

    Args:
        engine: ``postgresql`` or ``mysql``.
        session: The authenticated, elevated session.
        confirm: The operator accepted the engine's restart, where there is one.

    Returns:
        The queued job.

    Raises:
        ConfirmationRequired: 400 ``confirmation_required`` when the engine restarts and the
            operator has not accepted it.
    """
    if _tls(session, engine).needs_restart() and not confirm:
        raise ConfirmationRequired(
            [
                f"Taking the certificate off {engine} restarts it: every connection is dropped for a few seconds."
            ]
        )
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Take the certificate off {engine}",
        description=f"Putting the settings of {engine} back as they were",
        func=host_tls_job,
        kwargs={"engine": engine, "action": "disable"},
        metadata={"engine": engine, "action": "host-tls-disable"},
        message=f"{engine} will no longer serve a certificate of Noust's",
    )
