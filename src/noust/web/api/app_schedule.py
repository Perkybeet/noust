# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
When an application updates itself (checklist B-23).

A client of :mod:`noust.managers.update_scheduler`, like ``noust deploy schedule``:
``GET /api/apps/{domain}/update-schedule`` says when the application updates itself and how
the last run ended, ``PUT`` makes or changes the schedule (and writes its timer), and
``DELETE`` stops it. A run is the application's own update with a backup first, held by a
pause or a freeze of deploys; there is no endpoint to run one, because ``POST
/api/jobs/update`` is the update.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import update_schedule_store
from noust.core.teams import scoped_store
from noust.managers.update_scheduler import ALIASES, UpdateScheduler
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class UpdateScheduleOut(BaseModel):
    """An application's scheduled update."""

    domain: str
    scheduled: bool = Field(description="Whether the application updates itself at all")
    calendar: str | None = Field(default=None, description="The systemd OnCalendar expression")
    enabled: bool = True
    backup_first: bool = Field(
        default=True, description="The update is refused when its backup does not happen"
    )
    only_if_new: bool = Field(
        default=True, description="A run with nothing new to deploy does nothing"
    )
    next_run: str | None = Field(default=None, description="When the timer fires next")
    last_run_at: str | None = None
    last_outcome: str | None = Field(
        default=None,
        description="updated, nothing_new, held, backup_failed or failed",
    )
    last_detail: str | None = None
    last_deployment_id: int | None = None
    aliases: dict[str, str] = Field(
        default_factory=lambda: dict(ALIASES), description="Shorthands the calendar accepts"
    )


class UpdateScheduleRequest(BaseModel):
    """What to schedule."""

    calendar: str = Field(
        min_length=1,
        max_length=128,
        description="daily, weekly or monthly, or a systemd calendar expression",
    )
    enabled: bool = True
    backup_first: bool = True
    only_if_new: bool = True


def _known(domain: str) -> str:
    """
    Args:
        domain: From the path.

    Returns:
        The validated domain of an application the caller sees.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _out(domain: str) -> UpdateScheduleOut:
    """
    Args:
        domain: The application.

    Returns:
        Its schedule and its timer, as the API shows them.
    """
    saved = update_schedule_store.get(domain)
    if saved is None:
        return UpdateScheduleOut(domain=domain, scheduled=False)
    timer = UpdateScheduler().state(domain)
    return UpdateScheduleOut(
        domain=domain,
        scheduled=True,
        calendar=saved.calendar,
        enabled=saved.enabled,
        backup_first=saved.backup_first,
        only_if_new=saved.only_if_new,
        next_run=timer.get("next_run"),
        last_run_at=saved.last_run_at,
        last_outcome=saved.last_outcome,
        last_detail=saved.last_detail,
        last_deployment_id=saved.last_deployment_id,
    )


@router.get("/{domain}/update-schedule", response_model=UpdateScheduleOut)
def get_update_schedule(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> UpdateScheduleOut:
    """
    Show when an application updates itself, and how the last run ended.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The schedule; ``scheduled`` is false when the application does not update itself.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _out(_known(domain))


@router.put("/{domain}/update-schedule", response_model=UpdateScheduleOut)
def put_update_schedule(
    domain: str,
    body: UpdateScheduleRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> UpdateScheduleOut:
    """
    Make an application update itself on a schedule, or change the schedule.

    Writes ``noust-update-<app>.timer``. Each run takes the backup an update needs and
    refuses to update without it (unless asked not to), does nothing when there is nothing
    new, is held by a pause or a freeze of deploys, and passes the health gate.

    Args:
        domain: The application.
        body: When, and how careful.
        session: The authenticated session.

    Returns:
        The schedule now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The calendar expression is not acceptable, or the application is
            a preview (400).
        BackupError: The timer could not be written.
    """
    validated = _known(domain)
    UpdateScheduler().set(
        validated,
        body.calendar,
        enabled=body.enabled,
        backup_first=body.backup_first,
        only_if_new=body.only_if_new,
        by=actor_label(session),
    )
    return _out(validated)


@router.delete("/{domain}/update-schedule", response_model=UpdateScheduleOut)
def delete_update_schedule(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> UpdateScheduleOut:
    """
    Stop an application updating itself: the timer and the schedule go.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The schedule, which now says the application does not update itself.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    UpdateScheduler().remove(validated)
    return _out(validated)
