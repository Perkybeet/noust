# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/public``: a database made reachable from the network, on purpose.

A database is private; this is the deliberate, reversible exception. A client
of :class:`~noust.managers.database.public_access.PublicAccess`, like ``noust db
public``: the plan says everything that would be done and everything the
operator accepts; opening needs that acceptance (``confirm``) and sudo mode and
answers with the new account's password, once; closing undoes all of it.

Opening and closing are requests, not jobs: they take seconds, and a job would
keep the password in its result.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.database_exposures import Exposure
from noust.core.teams import scoped_store
from noust.managers.database.public_access import PublicAccess, PublicPlan
from noust.managers.database.registry import DatabaseRegistry
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)


class PublicRequest(BaseModel):
    """
    What to make reachable, and how.

    Attributes:
        engine: The engine.
        database: The database (a slot number for Redis).
        port: The public port, 1024 to 65535; a free one near the engine's when omitted.
        bind_address: Where to listen: ``0.0.0.0`` (every IPv4 address, the
            default), ``::`` or one address of this server.
        sources: Who may reach it: addresses or CIDRs; anyone when empty.
        tls: ``required`` (the default where the engine can) or ``optional``,
            which lets the password and the data cross the network in clear.
        profile: What the account made for it may do: ``owner`` (the default),
            ``read_write`` or ``read_only``.
        confirm: The operator accepted every warning of the plan. Without it
            nothing is changed and the warnings come back to be shown.
    """

    engine: str
    database: str
    port: int | None = Field(default=None, ge=1, le=65535)
    bind_address: str | None = Field(default=None, max_length=64)
    sources: list[str] = Field(default_factory=list, max_length=20)
    tls: str | None = Field(default=None, max_length=16)
    profile: str = Field(default="owner", max_length=16)
    confirm: bool = False


class FirewallPlanResponse(BaseModel):
    """
    What the firewall would be told.

    Attributes:
        backend: ``ufw``, ``firewalld``, ``nftables`` or ``none``.
        active: It filters traffic now.
        manages: Noust changes it (ufw or firewalld, installed).
        sources: Who the port is opened to.
        port: The port.
    """

    backend: str
    active: bool
    manages: bool
    sources: list[str]
    port: int


class PublicPlanResponse(BaseModel):
    """
    What making a database reachable would do. Nothing was changed.

    Attributes:
        warnings: What the operator accepts by confirming.
        target: Where the proxy forwards to: the engine's own loopback port.
    """

    engine: str
    database: str
    port: int
    bind_address: str
    sources: list[str]
    tls: str
    profile: str
    remote_user: str
    unit: str
    target: str
    firewall: FirewallPlanResponse
    warnings: list[str]


class ExposureResponse(BaseModel):
    """
    A database that is reachable from the network.

    Attributes:
        state: ``opening``, ``exposed`` or ``closing`` (a closing one was
            interrupted or could not be fully undone: close it again).
        listening: The proxy's port is open now.
        remote_user: The account made for it; its password is only shown once,
            or by ``POST .../reveal``.
    """

    engine: str
    database: str
    port: int
    bind_address: str
    sources: list[str]
    tls: str
    remote_user: str
    profile: str
    unit: str
    state: str
    created_at: str
    created_by: str | None = None
    listening: bool = False
    firewall: dict[str, Any] | None = None

    _iso_timestamps = iso_offset_validator("created_at")


class PublicListResponse(BaseModel):
    """Every database that is reachable from the network."""

    exposures: list[ExposureResponse]
    total: int


class ConnectionResponse(BaseModel):
    """
    How to connect to a public database.

    Attributes:
        password: The account's password; null when it is not being shown.
        url: The connection string; the password is masked when not shown.
        options: The query options that make a client use TLS.
    """

    host: str
    port: int
    database: str
    username: str
    password: str | None = None
    tls: str
    url: str
    options: dict[str, str] = Field(default_factory=dict)


class PublicOpenedResponse(BaseModel):
    """
    A database made reachable. The password is in ``connection``, this once.

    Attributes:
        exposure: The record.
        connection: How to connect, with the password.
        warnings: What the operator accepted.
    """

    exposure: ExposureResponse
    connection: ConnectionResponse
    warnings: list[str]


class PublicClosedResponse(BaseModel):
    """
    A database made private again.

    Attributes:
        removed: What was taken away, in order.
        already_private: The database was not public.
    """

    engine: str
    database: str
    removed: list[str]
    already_private: bool


class PublicDetailResponse(BaseModel):
    """
    One database's exposure, and whether it is public at all.

    Attributes:
        public: Whether it is reachable from the network.
        exposure: The record, when it is.
        connection: How to connect, the password masked.
    """

    public: bool
    exposure: ExposureResponse | None = None
    connection: ConnectionResponse | None = None


def _manager(session: dict[str, Any]) -> PublicAccess:
    """
    Build the manager a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The manager over the request's database service.
    """
    return PublicAccess(service(session))


def _exposure(record: Exposure, listening: set[int]) -> ExposureResponse:
    """
    Turn a record into its response.

    Args:
        record: The exposure.
        listening: The ports something listens on now.

    Returns:
        The response.
    """
    return ExposureResponse(
        engine=record.engine,
        database=record.db_name,
        port=record.port,
        bind_address=record.bind_address,
        sources=list(record.sources),
        tls=record.tls,
        remote_user=record.remote_user,
        profile=record.profile,
        unit=record.unit,
        state=record.state,
        created_at=record.created_at,
        created_by=record.created_by,
        listening=record.port in listening,
        firewall=record.firewall,
    )


def _plan(plan: PublicPlan) -> PublicPlanResponse:
    """
    Turn a plan into its response.

    Args:
        plan: The plan.

    Returns:
        The response.
    """
    return PublicPlanResponse(
        engine=plan.engine,
        database=plan.database,
        port=plan.port,
        bind_address=plan.bind_address,
        sources=list(plan.sources),
        tls=plan.tls,
        profile=plan.profile,
        remote_user=plan.remote_user,
        unit=plan.unit,
        target=plan.target,
        firewall=FirewallPlanResponse(**plan.firewall),
        warnings=plan.warnings,
    )


def _asked(request: PublicRequest) -> dict[str, Any]:
    """
    Read what a request asks for.

    Args:
        request: The body.

    Returns:
        The keyword arguments of :meth:`PublicAccess.plan` and :meth:`PublicAccess.expose`.
    """
    return {
        "port": request.port,
        "bind_address": request.bind_address,
        "sources": request.sources,
        "tls": request.tls,
        "profile": request.profile,
    }


@router.get("/public", response_model=PublicListResponse)
def list_public(
    session: Annotated[dict, Depends(get_current_session)],
) -> PublicListResponse:
    """
    List the databases that are reachable from the network.

    Args:
        session: The authenticated session.

    Returns:
        Every exposure, with whether its port listens now. A team member sees
        those of the databases its teams own: a port is a fact about the host,
        but whose database it forwards to is not for another team to read.
    """
    manager = _manager(session)
    listening = manager.listening()
    visible = scoped_store().visible_database_keys()
    records = manager.listing()
    if visible is not None:
        records = [
            record
            for record in records
            if (record.db_name, DatabaseRegistry.canonical(record.engine) or record.engine)
            in visible
        ]
    items = [_exposure(record, listening) for record in records]
    return PublicListResponse(exposures=items, total=len(items))


@router.post("/public/plan", response_model=PublicPlanResponse)
def plan_public(
    request: PublicRequest, session: Annotated[dict, Depends(get_current_session)]
) -> PublicPlanResponse:
    """
    Say what making a database reachable would do and what it asks the operator to accept.

    Changes nothing, and is what the console shows before it asks for sudo
    mode. A request that could not succeed (the ENS profile, an engine without
    a password, a port that is taken, TLS the engine cannot require) is
    refused here, with the reason.

    Args:
        request: What to open and how.
        session: The authenticated session.

    Returns:
        The plan.
    """
    return _plan(_manager(session).plan(request.engine, request.database, **_asked(request)))


@router.post("/public", response_model=PublicOpenedResponse, status_code=201)
def open_public(
    request: PublicRequest, session: Annotated[dict, Depends(require_elevated)]
) -> PublicOpenedResponse:
    """
    Make a database reachable from the network: a public port, an account, the firewall.

    Sudo mode. Without ``confirm`` nothing is changed and the answer is a 400
    ``confirmation_required`` carrying every warning; with it, the port, the account of its own, the TLS
    requirement and the firewall rule are made, in that order, and undone if
    one fails. The account's password is in the answer, this once.

    Args:
        request: What to open and how, and the confirmation.
        session: The authenticated, elevated session.

    Returns:
        The exposure and how to connect.
    """
    manager = _manager(session)
    outcome = manager.expose(
        request.engine, request.database, confirm=request.confirm, **_asked(request)
    )
    listening = manager.listening()
    return PublicOpenedResponse(
        exposure=_exposure(outcome.exposure, listening),
        connection=ConnectionResponse(**outcome.connection),
        warnings=outcome.warnings,
    )


@router.get("/public/{engine}/{database}", response_model=PublicDetailResponse)
def get_public(
    engine: str, database: str, session: Annotated[dict, Depends(get_current_session)]
) -> PublicDetailResponse:
    """
    Say whether a database is reachable from the network, and how.

    Args:
        engine: The engine.
        database: The database.
        session: The authenticated session.

    Returns:
        ``public`` and, when it is, the exposure and the connection with the
        password masked.
    """
    manager = _manager(session)
    record = manager.get(engine, database)
    if record is None:
        return PublicDetailResponse(public=False)
    return PublicDetailResponse(
        public=True,
        exposure=_exposure(record, manager.listening()),
        connection=ConnectionResponse(**manager.connection(record, None)),
    )


@router.post("/public/{engine}/{database}/reveal", response_model=ConnectionResponse)
def reveal_public(
    engine: str, database: str, session: Annotated[dict, Depends(require_elevated)]
) -> ConnectionResponse:
    """
    Show how to connect, with the account's password, again.

    Sudo mode, and audited as a password shown.

    Args:
        engine: The engine.
        database: The database.
        session: The authenticated, elevated session.

    Returns:
        The connection details with the password.
    """
    return ConnectionResponse(**_manager(session).reveal(engine, database))


@router.delete("/public/{engine}/{database}", response_model=PublicClosedResponse)
def close_public(
    engine: str, database: str, session: Annotated[dict, Depends(require_elevated)]
) -> PublicClosedResponse:
    """
    Make a database private again: undo everything making it public did.

    Sudo mode. Removes what the record says was made and nothing else; one
    step that cannot be done now leaves the record ``closing`` and is named in
    the error, and running this again finishes it. Connections that are open
    end with the proxy.

    Args:
        engine: The engine.
        database: The database.
        session: The authenticated, elevated session.

    Returns:
        What was removed.
    """
    outcome = _manager(session).make_private(engine, database)
    return PublicClosedResponse(
        engine=outcome.engine,
        database=outcome.database,
        removed=outcome.removed,
        already_private=outcome.already_private,
    )
