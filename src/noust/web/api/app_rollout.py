# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's delivery settings: its history, its stop and its pause.

A client of :mod:`noust.core.app_rollout_store` and
:func:`noust.deployers.lifecycle.set_stop_policy`, like ``noust deploy``:

- ``GET /api/apps/{domain}/rollout`` says how many deployments it keeps, how its
  unit is asked to stop, whether going back pauses what deploys it by itself, and
  whether that is paused now (and why), and which freeze holds it if one does;
- ``PUT`` changes the settings it is given and leaves the others (a ``null`` goes
  back to the default: the server's number, systemd's stop);
- ``POST .../pause`` and ``POST .../resume`` hold and release what deploys the
  application by itself: its pushes, its tags and its schedules. An update a
  person starts is never held by it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import app_rollout_store, delivery, deploy_freeze, deploy_guard
from noust.core import webhook_deliveries as deliveries_log
from noust.core.exceptions import ValidationError
from noust.core.store import AppType
from noust.core.teams import scoped_store
from noust.deployers.lifecycle import PHP_FPM, set_stop_policy
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.auth import actor_label
from noust.web.pydantic_compat import dump_set_fields

router = APIRouter(route_class=NoustErrorRoute)


class PauseOut(BaseModel):
    """What deploys an application by itself is held."""

    at: str = Field(description="When it was paused (UTC)")
    by: str | None = Field(description="Who paused it; 'rollback' when going back did")
    reason: str | None = Field(description="Why")


class FreezeOut(BaseModel):
    """A freeze of deploys."""

    id: int
    scope: str = Field(description="'server' or the application's domain")
    domain: str | None
    reason: str
    starts_at: str
    ends_at: str | None = Field(description="None lasts until somebody lifts it")
    created_by: str | None
    created_at: str
    lifted_at: str | None
    lifted_by: str | None
    state: str = Field(description="active, scheduled, ended or lifted")


class RolloutResponse(BaseModel):
    """An application's delivery settings."""

    domain: str
    history_keep: int | None = Field(
        description="Deployments of its history kept; null follows the server"
    )
    history_keep_server: int = Field(description="The server's number (deploy.history_keep)")
    history_keep_effective: int = Field(description="The number in force")
    stop_timeout: int | None = Field(
        description="TimeoutStopSec in seconds; null is systemd's (90)"
    )
    stop_signal: str | None = Field(description="KillSignal; null is SIGTERM")
    stop_supported: bool = Field(
        description="Whether its unit can be given a stop policy (a Compose stack's "
        "containers, a container and a static site have none)"
    )
    pause_on_rollback: bool = Field(
        description="Going back pauses what deploys it by itself, so the next push does not undo it"
    )
    paused: PauseOut | None = Field(description="Set while what deploys it by itself is held")
    held: int = Field(
        description="Pushes and tags that arrived and were not deployed since it was paused"
    )
    automatic: list[str] = Field(
        description="What can deploy it without anybody asking: webhook, github, tags"
    )
    freeze: FreezeOut | None = Field(description="The freeze that holds its deploys now, if any")


class RolloutRequest(BaseModel):
    """Delivery settings to change. A field left out keeps its value; null goes back to the default."""

    history_keep: int | None = None
    stop_timeout: int | None = None
    stop_signal: str | None = None
    pause_on_rollback: bool | None = None


class PauseRequest(BaseModel):
    """Why deploys by themselves are paused."""

    reason: str | None = Field(default=None, max_length=app_rollout_store.MAX_REASON)


class ResumeResponse(BaseModel):
    """What resuming did."""

    domain: str
    was_paused: bool
    held: int = Field(description="Pushes and tags that were not deployed while it was paused")


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def stop_supported(app_type: str | None, is_static: bool) -> bool:
    """
    Args:
        app_type: The application's type.
        is_static: Whether nothing runs for it.

    Returns:
        Whether its unit can be given a stop policy.
    """
    return not is_static and app_type not in (
        AppType.DOCKER_COMPOSE.value,
        AppType.CONTAINER.value,
        PHP_FPM,
    )


def _held(domain: str, since: str | None) -> int:
    """
    Args:
        domain: The application.
        since: When the pause began.

    Returns:
        How many deliveries it has held back since.
    """
    app = scoped_store().get_app(domain)
    if since is None or app is None or app.id is None:
        return 0
    return deliveries_log.held_since(app.id, since)


def _response(domain: str) -> RolloutResponse:
    """
    Gather an application's delivery settings.

    Args:
        domain: The application, known.

    Returns:
        The API model.
    """
    app = scoped_store().get_app(domain)
    settings = app_rollout_store.get(domain)
    freeze = deploy_freeze.active_for(domain)
    server = delivery.current().history_keep
    return RolloutResponse(
        domain=domain,
        history_keep=settings.history_keep,
        history_keep_server=server,
        history_keep_effective=settings.history_keep
        if settings.history_keep is not None
        else server,
        stop_timeout=settings.stop_timeout,
        stop_signal=settings.stop_signal,
        stop_supported=stop_supported(
            app.app_type if app else None, bool(app.is_static) if app else False
        ),
        pause_on_rollback=settings.pause_on_rollback,
        paused=(
            PauseOut(
                at=settings.paused_at or "", by=settings.paused_by, reason=settings.paused_reason
            )
            if settings.paused
            else None
        ),
        held=_held(domain, settings.paused_at),
        automatic=deploy_guard.automatic_sources(domain),
        freeze=FreezeOut(**freeze.to_dict()) if freeze is not None else None,
    )


@router.get("/{domain}/rollout", response_model=RolloutResponse)
def get_rollout(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> RolloutResponse:
    """
    Show how an application is delivered: its history, its stop and its pause.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The settings in force.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _response(_known(domain))


@router.put("/{domain}/rollout", response_model=RolloutResponse)
def put_rollout(
    domain: str,
    body: RolloutRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> RolloutResponse:
    """
    Change some of an application's delivery settings, keeping the others.

    A stop policy is written into the application's unit now and read by systemd the
    next time it stops; nothing restarts. The history is pruned to its number at the
    next deployment.

    Args:
        domain: Domain of the application.
        body: The settings to change; ``null`` goes back to the default.
        session: The authenticated session.

    Returns:
        The settings now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A value is not acceptable (400, with the field), or nothing
            was given.
        DeploymentError: Its unit cannot be given a stop policy (409).
    """
    validated = _known(domain)
    given: dict[str, Any] = dump_set_fields(body)
    if not given:
        raise ValidationError(
            "No delivery setting was given",
            details="Give at least one of: history_keep, stop_timeout, stop_signal, "
            "pause_on_rollback.",
            field="rollout",
        )
    actor = actor_label(session)
    # Everything is checked before anything is written: a bad number does not leave
    # the others half applied.
    if "history_keep" in given:
        app_rollout_store.check_history_keep(given["history_keep"])
    if "pause_on_rollback" in given and not isinstance(given["pause_on_rollback"], bool):
        raise ValidationError("pause_on_rollback must be true or false", field="pause_on_rollback")
    if "stop_timeout" in given or "stop_signal" in given:
        current = app_rollout_store.get(validated)
        set_stop_policy(
            validated,
            timeout=given["stop_timeout"] if "stop_timeout" in given else current.stop_timeout,
            signal=given["stop_signal"] if "stop_signal" in given else current.stop_signal,
        )
    if "history_keep" in given:
        app_rollout_store.set_history_keep(validated, given["history_keep"], actor=actor)
    if given.get("pause_on_rollback") is not None:
        app_rollout_store.set_pause_on_rollback(
            validated, bool(given["pause_on_rollback"]), actor=actor
        )
    return _response(validated)


@router.post("/{domain}/rollout/pause", response_model=RolloutResponse)
def pause_deploys(
    domain: str,
    body: PauseRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> RolloutResponse:
    """
    Hold what deploys an application by itself: its pushes, its tags and its schedules.

    An update a person starts, from the console or the command line, still runs.

    Args:
        domain: Domain of the application.
        body: Why.
        session: The authenticated session.

    Returns:
        The settings now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    app_rollout_store.pause(validated, reason=body.reason, by=actor_label(session))
    return _response(validated)


@router.post("/{domain}/rollout/resume", response_model=ResumeResponse)
def resume_deploys(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ResumeResponse:
    """
    Let an application's pushes, tags and schedules deploy it again.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Whether it was paused, and how many pushes and tags arrived meanwhile and
        were not deployed: the next push deploys the newest, or an update does now.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    settings = app_rollout_store.get(validated)
    held = _held(validated, settings.paused_at)
    was_paused = app_rollout_store.resume(validated, by=actor_label(session))
    return ResumeResponse(domain=validated, was_paused=was_paused, held=held)
