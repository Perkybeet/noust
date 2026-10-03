# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/engines``: what is installed, running, supported, and its controls.

Each engine is described with its capabilities (what tabs the console draws:
``sql``, ``tables``, ``keys``, ``documents``, ``read_only``, ``users``,
``profiles``, ``dump``, ``metrics``), where its version stands in upstream
support, and what the operator must know about it (``warnings``: MongoDB
without authorization, Redis without a password).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import DatabaseEngineError
from noust.managers.service_manager import ServiceManager
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import (
    ActionResponse,
    SupportNoticeResponse,
    queue,
    service,
    session_actor,
)
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType, database_engine_job

router = APIRouter(route_class=NoustErrorRoute)


class EngineInfo(BaseModel):
    """
    A database engine and whether it is usable on this host.

    Attributes:
        port: The port the server listens on when it runs, its default
            otherwise.
        service: The systemd unit it runs as on this server.
        capabilities: What the engine can do; the console draws its tabs
            from these instead of testing engine names.
        support: Upstream support for the installed version.
        warnings: What the operator must know about the installation.
        stored_account: Noust signs in with an account the operator stores
            (``PUT .../credentials``); PostgreSQL and MongoDB do not.
    """

    name: str
    display_name: str
    installed: bool
    version: str | None = None
    running: bool = False
    port: int
    service: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    support: SupportNoticeResponse | None = None
    warnings: list[str] = Field(default_factory=list)
    stored_account: bool = False


class EngineCredentialsRequest(BaseModel):
    """
    The account Noust signs in to an engine with.

    Attributes:
        user: The administrative user; empty keeps the stored one. Redis has
            none.
        password: Its password; empty keeps the stored one.
    """

    user: str | None = Field(default=None, max_length=128)
    password: str | None = Field(default=None, max_length=1024)


class EngineListResponse(BaseModel):
    """Response for listing engines."""

    engines: list[EngineInfo]


class EngineStatusResponse(BaseModel):
    """Response for the status of one engine."""

    engine: str
    display_name: str
    installed: bool
    version: str | None = None
    running: bool
    port: int
    service: str
    capabilities: list[str] = Field(default_factory=list)
    support: SupportNoticeResponse | None = None
    warnings: list[str] = Field(default_factory=list)


class EngineLogsResponse(BaseModel):
    """Response carrying journal output for an engine's service."""

    engine: str
    service: str
    logs: str
    lines: int


class PrivilegesResponse(BaseModel):
    """Response for ``GET /api/databases/engines/{engine}/privileges``."""

    engine: str
    privileges: list[str]


def _support(value: object) -> SupportNoticeResponse | None:
    """
    Build the support notice model from a status dictionary's value.

    Args:
        value: The ``support`` entry of a status, or None.

    Returns:
        The model, or None.
    """
    return SupportNoticeResponse(**value) if isinstance(value, dict) else None


@router.get("/engines", response_model=EngineListResponse)
def list_engines(session: Annotated[dict, Depends(get_current_session)]) -> EngineListResponse:
    """
    List every engine Noust can manage and its state on this host.

    Args:
        session: The authenticated session.

    Returns:
        The engines.
    """
    return EngineListResponse(
        engines=[
            EngineInfo(
                name=status["engine"],
                display_name=status["display_name"],
                installed=status["installed"],
                version=status.get("version"),
                running=bool(status.get("running")),
                port=status["port"],
                service=status.get("service"),
                capabilities=list(status.get("capabilities", [])),
                support=_support(status.get("support")),
                warnings=list(status.get("warnings", [])),
            )
            for status in service(session).engines()
        ]
    )


@router.get("/engines/{engine}/status", response_model=EngineStatusResponse)
def get_engine_status(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> EngineStatusResponse:
    """
    Report the state of one engine.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The engine status.
    """
    manager = service(session).manager(engine)
    status = manager.get_status()
    if status.get("running"):
        status["port"] = manager.server_port()
    return EngineStatusResponse(
        engine=status["engine"],
        display_name=status["display_name"],
        installed=status["installed"],
        version=status.get("version"),
        running=bool(status.get("running")),
        port=status["port"],
        service=status["service"],
        capabilities=list(status.get("capabilities", [])),
        support=_support(status.get("support")),
        warnings=list(status.get("warnings", [])),
    )


@router.get("/engines/{engine}/privileges", response_model=PrivilegesResponse)
def get_engine_privileges(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> PrivilegesResponse:
    """
    List the privileges an engine's grant dialog may offer.

    The manager's own whitelist is the one definition of what Noust will
    grant, so the console reads it from here instead of keeping a copy.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The engine's valid privileges, sorted for a stable listing.
    """
    manager = service(session).manager(engine)
    return PrivilegesResponse(
        engine=manager.ENGINE_NAME, privileges=sorted(manager.VALID_PRIVILEGES)
    )


@router.get("/engines/{engine}/logs", response_model=EngineLogsResponse)
def get_engine_logs(
    engine: str,
    session: Annotated[dict, Depends(get_current_session)],
    lines: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> EngineLogsResponse:
    """
    Read journal output for an engine's service.

    Args:
        engine: Engine name.
        lines: How many lines to return.
        session: The authenticated session.

    Returns:
        The log output.
    """
    manager = service(session).manager(engine)
    unit = manager.service_unit()
    logs = ServiceManager(verbose=False).logs(unit, lines=lines) or "No logs available"
    return EngineLogsResponse(engine=manager.ENGINE_NAME, service=unit, logs=logs, lines=lines)


@router.post("/engines/{engine}/install", response_model=JobAcceptedResponse, status_code=202)
def install_engine(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue the installation of an engine.

    Installation drives the distribution package manager, so it runs as a job.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 409 when the engine is already installed.
    """
    manager = service(session).manager(engine)
    if manager.is_installed():
        raise HTTPException(status_code=409, detail=f"{manager.DISPLAY_NAME} is already installed")
    accepted = queue(
        session,
        job_type=JobType.CUSTOM,
        name=f"Install {manager.DISPLAY_NAME}",
        description=f"Installing {manager.DISPLAY_NAME}",
        func=database_engine_job,
        kwargs={"engine": manager.ENGINE_NAME, "action": "install"},
        metadata={"engine": manager.ENGINE_NAME},
        message=f"Installation queued for {manager.DISPLAY_NAME}",
        pass_actor=False,
    )
    audit.record("db.install", target=f"db:{manager.ENGINE_NAME}", details={"job": accepted.job_id})
    return accepted


@router.post("/engines/{engine}/uninstall", response_model=JobAcceptedResponse, status_code=202)
def uninstall_engine(
    engine: str,
    session: Annotated[dict, Depends(require_elevated)],
    purge: Annotated[bool, Query(description="Also remove configuration and data")] = False,
) -> JobAcceptedResponse:
    """
    Queue the removal of an engine, with sudo mode.

    Removing an engine can take every database it hosts with it.

    Args:
        engine: Engine name.
        purge: Also remove configuration and data.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    manager = service(session).manager(engine)
    accepted = queue(
        session,
        job_type=JobType.CUSTOM,
        name=f"Uninstall {manager.DISPLAY_NAME}",
        description=f"Uninstalling {manager.DISPLAY_NAME}",
        func=database_engine_job,
        kwargs={"engine": manager.ENGINE_NAME, "action": "uninstall", "purge": purge},
        metadata={"engine": manager.ENGINE_NAME, "purge": purge},
        message=f"Removal queued for {manager.DISPLAY_NAME}",
        pass_actor=False,
    )
    audit.record(
        "db.uninstall",
        actor=session_actor(session),
        target=f"db:{manager.ENGINE_NAME}",
        details={"job": accepted.job_id, "purge": purge},
    )
    return accepted


@router.put("/engines/{engine}/credentials", response_model=ActionResponse)
def set_engine_credentials(
    engine: str,
    request: EngineCredentialsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ActionResponse:
    """
    Store the account Noust signs in to an engine with, once it works, with sudo mode.

    The engine is asked first, through the code that will use the account:
    a refusal answers with the engine's own message and saves nothing.

    Args:
        engine: Engine name.
        request: The user and the password.
        session: The authenticated, elevated session.

    Returns:
        The outcome.
    """
    manager = service(session).manager(engine)
    service(session).set_credentials(engine, request.user or None, request.password or None)
    return ActionResponse(
        success=True, message=f"Noust signs in to {manager.DISPLAY_NAME} with that account"
    )


@router.post("/engines/{engine}/start", response_model=ActionResponse)
def start_engine(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Start an engine's service.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    manager = service(session).manager(engine)
    if not manager.is_installed():
        raise DatabaseEngineError(
            f"{manager.DISPLAY_NAME} is not installed",
            details="Install it before starting it.",
        )
    if manager.is_running():
        return ActionResponse(success=True, message=f"{manager.DISPLAY_NAME} is already running")
    manager.start()
    return ActionResponse(success=True, message=f"{manager.DISPLAY_NAME} started")


@router.post("/engines/{engine}/stop", response_model=ActionResponse)
def stop_engine(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Stop an engine's service.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    manager = service(session).manager(engine)
    if not manager.is_running():
        return ActionResponse(success=True, message=f"{manager.DISPLAY_NAME} is not running")
    manager.stop()
    return ActionResponse(success=True, message=f"{manager.DISPLAY_NAME} stopped")


@router.post("/engines/{engine}/restart", response_model=ActionResponse)
def restart_engine(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Restart an engine's service.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The action outcome.
    """
    manager = service(session).manager(engine)
    if not manager.is_installed():
        raise DatabaseEngineError(
            f"{manager.DISPLAY_NAME} is not installed",
            details="Install it before restarting it.",
        )
    manager.restart()
    return ActionResponse(success=True, message=f"{manager.DISPLAY_NAME} restarted")
