# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where the logs of applications are shipped to (3.4, checklist B-73).

A client of :mod:`noust.managers.log_drains`, like ``noust logdrain``: ``GET``
lists the destinations with how the last attempt went, ``PUT`` makes one or changes
one, ``DELETE`` removes it, ``POST .../test`` sends it one line and says what it
answered. A destination's token is written and never read back: the answer says
whether it has one. These are the server's settings, so they need the permission to
manage them and sudo mode, and a team that sees only its own applications does not
reach them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.log_drain_store import Drain
from noust.managers import log_shipping
from noust.managers.log_drains import LogDrains
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class DrainInfo(BaseModel):
    """A destination of logs and how the last attempt went."""

    name: str
    kind: str = Field(description="loki, axiom, newrelic, http, otlp or syslog")
    settings: dict[str, Any] = Field(description="What is not secret: address, labels, transport")
    apps: list[str] = Field(description="The applications it receives; empty is every one")
    enabled: bool
    has_secret: bool = Field(description="Whether it has a token or a password; never the value")
    last_ok_at: str | None = None
    last_error: str | None = None
    last_error_at: str | None = None
    shipped: int = Field(description="Lines it has received in all")
    plaintext: bool = Field(
        default=False, description="Its address is http:// or its syslog is not TLS"
    )


class DrainList(BaseModel):
    """The destinations and the unit that ships to them."""

    drains: list[DrainInfo]
    total: int
    service: dict[str, Any] = Field(
        description="installed, active and systemd's state of the shipper's unit"
    )


class SaveDrainRequest(BaseModel):
    """A destination of logs."""

    kind: str = Field(..., description="loki, axiom, newrelic, http, otlp or syslog")
    settings: dict[str, Any] = Field(
        default_factory=dict, description="Address, labels, transport...: see the kind"
    )
    apps: list[str] = Field(default_factory=list, description="Narrow it to these applications")
    secret: str | None = Field(
        default=None, description="Its token or password; written, never read back"
    )
    keep_secret: bool = Field(
        default=False, description="Leave the token it has as it is when editing"
    )
    enabled: bool = True


class TestDrainResponse(BaseModel):
    """What sending one line to a destination came to."""

    __test__ = False

    name: str
    sent: str = Field(description="The line sent, to look for at the other end")


def _info(drain: Drain, manager: LogDrains) -> DrainInfo:
    """
    Describe a destination for the API.

    Args:
        drain: The destination.
        manager: The manager, which knows whether it has a secret.

    Returns:
        Its description, never its secret.
    """
    settings = drain.settings
    plaintext = (
        str(settings.get("url", "")).startswith("http://")
        if drain.kind != "syslog"
        else settings.get("transport") != "tls"
    )
    return DrainInfo(
        name=drain.name,
        kind=drain.kind,
        settings=settings,
        apps=list(drain.apps),
        enabled=drain.enabled,
        has_secret=manager.has_secret(drain.name),
        last_ok_at=drain.last_ok_at,
        last_error=drain.last_error,
        last_error_at=drain.last_error_at,
        shipped=drain.shipped,
        plaintext=plaintext,
    )


@router.get("", response_model=DrainList)
def list_drains(session: Annotated[dict, Depends(get_current_session)]) -> DrainList:
    """
    List the destinations of logs. Changes nothing.

    Args:
        session: The authenticated session.

    Returns:
        The destinations and the state of the unit that ships to them.
    """
    manager = LogDrains()
    found = [_info(drain, manager) for drain in manager.list_drains()]
    return DrainList(drains=found, total=len(found), service=log_shipping.service_state())


@router.put("/{name}", response_model=DrainInfo)
def save_drain(
    name: str, body: SaveDrainRequest, session: Annotated[dict, Depends(require_elevated)]
) -> DrainInfo:
    """
    Make a destination, or change one. The shipper starts if it is not running.

    Args:
        name: The destination's name.
        body: What it is.
        session: The authenticated, elevated session.

    Returns:
        The destination.

    Raises:
        ValidationError: A setting a destination cannot have, naming the field (400).
        ServiceError: The shipper's unit could not be started.
    """
    manager = LogDrains()
    saved = manager.save(
        name,
        body.kind,
        body.settings,
        apps=body.apps,
        secret=body.secret,
        keep_secret=body.keep_secret,
        enabled=body.enabled,
        actor=actor_label(session),
    )
    if saved.enabled:
        log_shipping.ensure_service()
    return _info(saved, manager)


@router.delete("/{name}", response_model=DrainList)
def delete_drain(name: str, session: Annotated[dict, Depends(require_elevated)]) -> DrainList:
    """
    Remove a destination and its token.

    Args:
        name: The destination's name.
        session: The authenticated, elevated session.

    Returns:
        The destinations that remain.

    Raises:
        ValidationError: There is none of that name (400).
    """
    manager = LogDrains()
    manager.remove(name, actor=actor_label(session))
    found = [_info(drain, manager) for drain in manager.list_drains()]
    return DrainList(drains=found, total=len(found), service=log_shipping.service_state())


@router.post("/{name}/test", response_model=TestDrainResponse)
def test_drain(name: str, session: Annotated[dict, Depends(require_elevated)]) -> TestDrainResponse:
    """
    Send one line to a destination, and say what it answered.

    Args:
        name: The destination's name.
        session: The authenticated, elevated session.

    Returns:
        The line that was sent.

    Raises:
        ValidationError: There is none of that name (400).
        DeliveryError: It did not accept the line; its own words are in the answer.
    """
    sent = LogDrains().test(name, actor=actor_label(session))
    return TestDrainResponse(name=name, sent=sent)
