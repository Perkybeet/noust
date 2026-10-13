# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
TCP and UDP ports published in front of a backend, over the API.

A thin translation of :mod:`noust.managers.stream_exposure`, which ``noust stream``
calls too. A published port is the server's, not an application's: it changes nginx
and the firewall, so opening and closing one is changing the host and asks for sudo
mode, while looking at what is published and planning one are reads.

Nothing here decides whether a port may be published: the manager answers every
question about it (what listens there, who holds it) before it touches anything, and
nginx's own words are the error when it refuses the configuration.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.stream_store import StreamExposure
from noust.managers.stream_exposure import (
    DEFAULT_TIMEOUT,
    MAX_TIMEOUT,
    MIN_TIMEOUT,
    StreamExposures,
    StreamPlan,
    StreamResult,
    StreamSupport,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class StreamSupportModel(BaseModel):
    """
    Whether nginx here can publish a port, and what is missing.

    Attributes:
        available: Everything needed is in place or Noust can put it in place.
        module: ``static``, ``dynamic`` or ``missing``.
        loader: ``present``, ``installable``, ``foreign`` or ``manual``.
        problem: What is missing, when something is.
        hint: How to fix it: a command, or the line to add.
    """

    available: bool
    module: str
    loader: str
    problem: str | None = None
    hint: str | None = None


class StreamFirewallModel(BaseModel):
    """
    What Noust opened in the firewall for a port.

    Attributes:
        backend: ``ufw``, ``firewalld`` or ``none``.
        active: The firewall filters traffic.
        made: The rules this port added, which are the only ones closed with it.
        note: Why nothing was changed, when nothing was.
    """

    backend: str = "none"
    active: bool = False
    made: list[dict[str, Any]] = Field(default_factory=list)
    note: str = ""


class StreamExposureModel(BaseModel):
    """
    A published port.

    Attributes:
        name: What it is called.
        protocol: ``tcp`` or ``udp``.
        listen_port: The port visitors reach.
        target: The backend, ``host:port``.
        target_host: The backend's address.
        target_port: The backend's port.
        sources: Addresses allowed in; empty is anyone.
        timeout_seconds: How long a quiet connection or session is kept.
        app_domain: The application it is for, when it is for one.
        firewall: What was opened for it.
        state: ``opening``, ``open`` or ``closing``.
        created_at: When it was recorded.
        created_by: Who asked.
    """

    name: str
    protocol: str
    listen_port: int
    target: str
    target_host: str
    target_port: int
    sources: list[str]
    timeout_seconds: int
    app_domain: str | None = None
    firewall: StreamFirewallModel
    state: str
    created_at: str = ""
    created_by: str | None = None


class StreamListResponse(BaseModel):
    """
    Every published port, and what nginx here offers.

    Attributes:
        exposures: The published ports, by port.
        support: Whether another can be published.
    """

    exposures: list[StreamExposureModel]
    support: StreamSupportModel


class StreamRequest(BaseModel):
    """
    A port to publish.

    Attributes:
        name: What to call it: lower case letters, digits and dashes.
        protocol: ``tcp`` or ``udp``.
        listen_port: The port to publish.
        target_port: The backend's port; the application's own when ``app_domain`` is given.
        target_host: The backend's address; this machine by default.
        sources: Addresses or networks allowed in; none is anyone.
        timeout_seconds: How long a quiet connection or session is kept.
        app_domain: The application it is for.
    """

    name: str
    protocol: str = "tcp"
    listen_port: int
    target_port: int | None = None
    target_host: str = "127.0.0.1"
    sources: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=DEFAULT_TIMEOUT, ge=MIN_TIMEOUT, le=MAX_TIMEOUT)
    app_domain: str | None = None


class StreamPlanResponse(BaseModel):
    """
    What publishing a port would do.

    Attributes:
        exposure: The exposure as it would be recorded.
        config: The text of its nginx file.
        file: Where that file goes.
        loader: ``present``, or ``write`` when Noust would write the block that carries it.
        support: What nginx offers.
        warnings: Things that are probably wrong and do not stop it.
        firewall: The rules the firewall would be asked for.
    """

    exposure: StreamExposureModel
    config: str
    file: str
    loader: str
    support: StreamSupportModel
    warnings: list[str]
    firewall: list[dict[str, Any]]


class StreamResultResponse(BaseModel):
    """
    What publishing or closing a port did.

    Attributes:
        exposure: The exposure.
        warnings: Things the operator should know.
        firewall: A sentence on the firewall.
    """

    exposure: StreamExposureModel
    warnings: list[str]
    firewall: str


def _support(support: StreamSupport) -> StreamSupportModel:
    """
    Args:
        support: What nginx offers.

    Returns:
        The response model.
    """
    return StreamSupportModel(**support.to_dict())


def _exposure(exposure: StreamExposure) -> StreamExposureModel:
    """
    Args:
        exposure: A published port.

    Returns:
        The response model.
    """
    data = exposure.to_dict()
    data["firewall"] = (
        StreamFirewallModel(**exposure.firewall) if exposure.firewall else StreamFirewallModel()
    )
    return StreamExposureModel(**data)


def _result(result: StreamResult) -> StreamResultResponse:
    """
    Args:
        result: What was done.

    Returns:
        The response model.
    """
    return StreamResultResponse(
        exposure=_exposure(result.exposure),
        warnings=list(result.warnings),
        firewall=result.firewall,
    )


def _asked(data: StreamRequest) -> dict[str, Any]:
    """
    Args:
        data: The request body.

    Returns:
        The arguments of :class:`StreamExposures`'s ``plan`` and ``open``.
    """
    return {
        "protocol": data.protocol,
        "listen_port": data.listen_port,
        "target_port": data.target_port,
        "target_host": data.target_host,
        "sources": data.sources,
        "timeout_seconds": data.timeout_seconds,
        "app_domain": data.app_domain,
    }


@router.get("", response_model=StreamListResponse)
def list_streams(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> StreamListResponse:
    """
    List the published ports, and say whether nginx here can publish another.

    Args:
        session: Authenticated session, injected.

    Returns:
        The ports and what nginx offers.
    """
    streams = StreamExposures()
    return StreamListResponse(
        exposures=[_exposure(row) for row in streams.all()],
        support=_support(streams.support()),
    )


@router.post("/plan", response_model=StreamPlanResponse)
def plan_stream(
    data: StreamRequest, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> StreamPlanResponse:
    """
    Check a port and say what publishing it would do, changing nothing.

    Args:
        data: The port.
        session: Authenticated session, injected.

    Returns:
        The nginx file, the firewall rules and what to look out for.

    Raises:
        ValidationError: The port is held or a field is not acceptable (400).
        DependencyError: nginx cannot publish a port, and why.
    """
    plan: StreamPlan = StreamExposures().plan(data.name, **_asked(data))
    return StreamPlanResponse(
        exposure=_exposure(plan.exposure),
        config=plan.config,
        file=str(plan.file),
        loader=plan.loader,
        support=_support(plan.support),
        warnings=list(plan.warnings),
        firewall=list(plan.firewall),
    )


@router.post("", response_model=StreamResultResponse, status_code=201)
def open_stream(
    data: StreamRequest, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> StreamResultResponse:
    """
    Publish a port: its nginx file, nginx's reload, and the firewall.

    Needs sudo mode: it changes what the whole machine answers on.

    Args:
        data: The port.
        session: Authenticated and elevated session, injected.

    Returns:
        The exposure and what the firewall came to.

    Raises:
        ValidationError: The port is held, or nginx refused the configuration (400,
            with nginx's own output).
        DependencyError: nginx cannot publish a port, and why.
        SecurityError: The firewall refused a rule; the port is closed again.
    """
    return _result(StreamExposures().open(data.name, actor=actor_label(session), **_asked(data)))


@router.delete("/{name}", response_model=StreamResultResponse)
def close_stream(
    name: str, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> StreamResultResponse:
    """
    Close a published port: the firewall rules made for it, its file, and nginx's use of it.

    Needs sudo mode. Asking again after a failure finishes it.

    Args:
        name: The exposure.
        session: Authenticated and elevated session, injected.

    Returns:
        The exposure that was closed.

    Raises:
        ResourceNotFoundError: There is no such port (404).
        SecurityError: The firewall refused to remove a rule; the same call finishes it.
    """
    return _result(StreamExposures().close(name, actor=actor_label(session)))
