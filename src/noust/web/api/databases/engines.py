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
            (``PUT .../credentials``); PostgreSQL and MongoDB do not, nor
            does a container.
        kind: ``host`` for the server's own engine, ``container`` for one
            Docker runs; ``name`` is then its instance key
            (``postgresql@project.service``).
        container: The container's name.
        project: Its Compose project.
        service: Its Compose service.
        image: The image it runs.
        app: The application it belongs to (its Compose project is the
            application's).
        access: ``full``, or ``limited`` when the credentials the container
            carries only let Noust in as the application's own account.
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
    kind: str = "host"
    container: str | None = None
    project: str | None = None
    compose_service: str | None = None
    image: str | None = None
    app: str | None = None
    access: str | None = None


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


class VersionChoiceResponse(BaseModel):
    """
    One version of a flavour this server can be given.

    Attributes:
        source: ``distribution`` (the release's own packages) or ``upstream``
            (the engine's repository, added with its pinned key).
        default: What installs when no version is chosen.
    """

    version: str
    source: str
    default: bool = False


class FlavourChoiceResponse(BaseModel):
    """
    A flavour as the install dialog offers it.

    Attributes:
        flavour: ``postgresql``, ``mysql``, ``mariadb``, ``redis``,
            ``valkey`` or ``mongodb``; what ``POST .../install`` takes.
        engine: The engine that runs it, the ``{engine}`` of every other route.
        installed: This flavour is the one installed.
        installable: It can be installed now.
        blocked: Why not, as a stable code: ``installed``, ``conflict`` (the
            engine's other flavour is installed), ``not_available`` (nothing
            publishes it for this release) or ``no_apt``.
        reason: The same in one English sentence, with what to do.
        versions: The versions offered, oldest first.
    """

    flavour: str
    engine: str
    display_name: str
    installed: bool
    installable: bool
    blocked: str | None = None
    reason: str | None = None
    versions: list[VersionChoiceResponse] = Field(default_factory=list)


class DistributionResponse(BaseModel):
    """
    The distribution the catalog was computed for.

    Attributes:
        known: Whether Noust knows which versions it ships; on a release it
            does not know, only the distribution's own packages are offered.
    """

    id: str
    codename: str
    name: str
    known: bool


class EngineCatalogResponse(BaseModel):
    """Response for ``GET /api/databases/engines/catalog``."""

    distribution: DistributionResponse
    apt: bool
    flavours: list[FlavourChoiceResponse]


class EngineInstallRequest(BaseModel):
    """
    What to install. Both fields are optional, and so is the body.

    Attributes:
        flavour: A flavour of the engine in the path (``mariadb`` for
            ``mysql``, ``valkey`` for ``redis``). The path may name it too.
        version: One of the catalog's versions; the distribution's when
            empty.
    """

    flavour: str | None = Field(default=None, max_length=32)
    version: str | None = Field(default=None, max_length=16)


class EngineSettingResponse(BaseModel):
    """
    One setting of an engine.

    Attributes:
        key: The engine's own name for it; stable, the key the console
            translates ``description`` by.
        kind: ``addresses``, ``port``, ``integer``, ``size``,
            ``duration_ms``, ``seconds``, ``gigabytes``, ``boolean``,
            ``enum``, ``timezone`` or ``snapshots``.
        current: What the running engine uses; null when it did not answer.
        configured: What Noust's file sets; null when it sets nothing.
        recommended: Noust's advice for this server's memory and cores;
            null when it depends on the applications.
        restart: Changing it restarts the engine.
        listen: It decides where the engine listens; a non-loopback value
            needs ``confirm_exposure``.
        editable: Noust changes it; ``locked_reason`` says why not.
        source: The file the engine read the current value from (PostgreSQL).
    """

    key: str
    kind: str
    unit: str | None = None
    description: str
    current: str | None = None
    configured: str | None = None
    recommended: str | None = None
    restart: bool
    choices: list[str] = Field(default_factory=list)
    minimum: float | None = None
    maximum: float | None = None
    listen: bool = False
    editable: bool = True
    locked_reason: str | None = None
    source: str | None = None


class EngineSettingsResponse(BaseModel):
    """
    Response for ``GET /api/databases/engines/{engine}/settings``.

    Attributes:
        file: The file Noust writes the settings to.
        running: The engine answered, so ``current`` is known.
        memory_bytes: The memory the recommendations were computed from.
        cpus: The processors they were computed from.
    """

    engine: str
    display_name: str
    file: str
    running: bool
    memory_bytes: int
    cpus: int
    settings: list[EngineSettingResponse]


class EngineSettingsRequest(BaseModel):
    """
    Settings to change.

    Attributes:
        values: New values by key, as text (``256MB``, ``on``, ``127.0.0.1``);
            ``default`` removes a setting from Noust's file.
        confirm_exposure: The operator accepts that the engine will listen
            beyond loopback. Without it such a change is refused with the
            exposure warning, on the ``confirm_exposure`` field.
    """

    values: dict[str, str] = Field(default_factory=dict)
    confirm_exposure: bool = False


class EngineSettingsOutcomeResponse(BaseModel):
    """
    What applying settings did.

    Attributes:
        changed: The keys whose value changed.
        action: ``none``, ``reload``, ``runtime`` (applied to the running
            engine) or ``restart``.
        exposed: The engine now listens beyond loopback.
        warnings: What the operator must know, in English.
    """

    engine: str
    display_name: str
    file: str
    changed: list[str]
    action: str
    exposed: bool = False
    warnings: list[str] = Field(default_factory=list)


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
                stored_account=bool(status.get("stored_account")),
                kind=str(status.get("kind") or "host"),
                container=status.get("container"),
                project=status.get("project"),
                compose_service=status.get("compose_service"),
                image=status.get("image"),
                app=status.get("app"),
                access=status.get("access"),
            )
            for status in service(session).engines()
        ]
    )


@router.get("/engines/catalog", response_model=EngineCatalogResponse)
def get_engine_catalog(
    session: Annotated[dict, Depends(get_current_session)],
) -> EngineCatalogResponse:
    """
    Say which flavours and versions can be installed on this server.

    MySQL and MariaDB, Redis and Valkey are separate choices; one is never
    offered while the other is installed.

    Args:
        session: The authenticated session.

    Returns:
        The catalog for this distribution.
    """
    return EngineCatalogResponse(**service(session).install_catalog())


@router.get("/engines/{engine}/settings", response_model=EngineSettingsResponse)
def get_engine_settings(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> EngineSettingsResponse:
    """
    Read an engine's settings: current, configured and recommended.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        The settings.
    """
    return EngineSettingsResponse(**service(session).engine_settings(engine).to_dict())


@router.put("/engines/{engine}/settings", response_model=EngineSettingsOutcomeResponse)
def put_engine_settings(
    engine: str,
    request: EngineSettingsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> EngineSettingsOutcomeResponse:
    """
    Change an engine's settings, with sudo mode.

    The file is written, checked by the engine's own tool, and the engine
    restarted, reloaded or changed at runtime; when it does not answer, the
    previous settings are put back and the error carries its journal.

    Args:
        engine: Engine name.
        request: The new values and the exposure confirmation.
        session: The authenticated, elevated session.

    Returns:
        What changed and how it was applied.
    """
    outcome = service(session).change_engine_settings(
        engine, request.values, confirm_exposure=request.confirm_exposure
    )
    return EngineSettingsOutcomeResponse(**outcome.to_dict())


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
    Read journal output for an engine's service, or a container's log.

    Args:
        engine: Engine name or instance key.
        lines: How many lines to return.
        session: The authenticated session.

    Returns:
        The log output.
    """
    manager = service(session).manager(engine)
    unit = manager.service_unit()
    if manager.instance is not None:
        logs = manager.container_logs(lines) or "No logs available"
    else:
        logs = ServiceManager(verbose=False).logs(unit, lines=lines) or "No logs available"
    return EngineLogsResponse(engine=manager.ENGINE_NAME, service=unit, logs=logs, lines=lines)


@router.post("/engines/{engine}/install", response_model=JobAcceptedResponse, status_code=202)
def install_engine(
    engine: str,
    session: Annotated[dict, Depends(get_current_session)],
    request: EngineInstallRequest | None = None,
) -> JobAcceptedResponse:
    """
    Queue the installation of an engine, in a flavour and version of the catalog.

    Installation drives the distribution package manager, so it runs as a
    job. What it will install is decided first, so a version this server
    cannot have, or MariaDB while MySQL is installed, is refused here rather
    than in the job. Without a body it installs what 3.2 did.

    Args:
        engine: Engine name, or a flavour's (``mariadb``, ``valkey``).
        session: The authenticated session.
        request: The flavour and version, both optional.

    Returns:
        The queued job.

    Raises:
        HTTPException: 409 when the engine is already installed.
    """
    choice = request or EngineInstallRequest()
    decided = service(session).plan_engine_install(
        engine, flavour=choice.flavour or None, version=choice.version or None
    )
    manager = decided.manager
    if decided.already_installed:
        raise HTTPException(status_code=409, detail=f"{decided.display_name} is already installed")
    what = decided.describe()
    kwargs: dict[str, object] = {"engine": manager.ENGINE_NAME, "action": "install"}
    if decided.flavour is not None:
        kwargs["flavour"] = decided.flavour
    if decided.plan is not None and choice.version:
        kwargs["version"] = decided.plan.version
    accepted = queue(
        session,
        job_type=JobType.CUSTOM,
        name=f"Install {what}",
        description=f"Installing {what}",
        func=database_engine_job,
        kwargs=kwargs,
        metadata={
            "engine": manager.ENGINE_NAME,
            "plan": decided.plan.to_dict() if decided.plan is not None else None,
        },
        message=f"Installation queued for {what}",
        pass_actor=False,
    )
    audit.record(
        "db.install",
        target=f"db:{manager.ENGINE_NAME}",
        details={
            "job": accepted.job_id,
            "flavour": decided.flavour,
            "version": kwargs.get("version"),
        },
    )
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
    manager.refuse_in_container("uninstall")
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
