# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Backup schedules API endpoints.

A thin client of :class:`~wasm.managers.backup_scheduler.BackupScheduler`,
which owns the timer/service unit pair, the systemctl calls and every rule
about what may be written into a root-owned unit file. Three decisions live
here rather than in the handlers' bodies:

- **The calendar is validated in the request model**, through the scheduler's
  own :func:`~wasm.managers.backup_scheduler.validate_calendar`, so a bad
  expression answers ``422`` with the scheduler's exact refusal instead of
  becoming a half-written schedule. There is no second definition of a valid
  expression for the two to disagree over.
- **Retention and destinations are stored, not just forwarded.** Since
  schema v10 the schedule lives in the store
  (:class:`~wasm.core.store.BackupScheduleRecord`); ``wasm backup
  run-schedule`` reads it back, so what is created here is what actually
  runs, including the remote destinations a schedule pushes to.
- **Every mutation is audited** to ``wasm.audit`` with the session that asked
  for it, like every other mutation the panel can perform.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from wasm.core.exceptions import BackupError
from wasm.core.store import get_store
from wasm.core.utils import domain_to_app_name
from wasm.managers.backup_scheduler import (
    SCHEDULE_ALIASES,
    BackupSchedule,
    BackupScheduler,
    validate_calendar,
)
from wasm.web.api.auth import get_current_session
from wasm.web.api.deps import WASMErrorRoute, require_elevated, strict_domain
from wasm.web.auth import actor_label
from wasm.web.pydantic_compat import dump_model, field_validator

router = APIRouter(route_class=WASMErrorRoute)

audit_log = logging.getLogger("wasm.audit")

#: The alias each expansion came from, so a listing can say "daily" instead of
#: making an operator parse ``*-*-* 02:00:00``.
_ALIAS_BY_CALENDAR = {calendar: alias for alias, calendar in SCHEDULE_ALIASES.items()}


class ScheduleDestination(BaseModel):
    """One remote destination a schedule pushes its backups to."""

    name: str = Field(
        ..., description="A backup destination created under /api/backup-destinations"
    )
    retention_count: int | None = Field(default=None, ge=1, le=365)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class BackupScheduleInfo(BaseModel):
    """
    One backup schedule, merging what systemd reports with the store row.

    Attributes:
        domain: Domain the schedule backs up.
        app_name: Application name the unit names are built from.
        timer: Timer unit name, without its ``.timer`` suffix.
        schedule: The schedule in words: an alias such as ``daily`` when the
            expression matches one, ``custom`` otherwise.
        on_calendar: The systemd ``OnCalendar`` expression, verbatim.
        next_run: When the timer fires next, as systemd prints it, or
            ``pending`` when it cannot say.
        last_run: When the timer last fired, or ``never``.
        retention_count: Local backups to keep, from the store row; None for
            the default.
        retention_days: Maximum local backup age in days, from the store row;
            None for no limit.
        destinations: Remote destinations this schedule pushes to.
    """

    domain: str
    app_name: str
    timer: str
    schedule: str
    on_calendar: str
    next_run: str
    last_run: str
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[ScheduleDestination] = Field(default_factory=list)


class ScheduleListResponse(BaseModel):
    """Response for listing backup schedules."""

    schedules: list[BackupScheduleInfo]
    total: int


class CreateScheduleRequest(BaseModel):
    """Request to schedule automatic backups for an application."""

    domain: str = Field(..., description="Domain of the app to back up")
    schedule: str = Field(
        default="daily",
        description="hourly, daily, weekly, monthly or a systemd OnCalendar expression",
    )
    retention_count: int = Field(default=7, ge=1, le=365, description="Backups to keep")
    retention_days: int = Field(default=30, ge=1, le=3650, description="Max age in days")
    include_databases: bool = Field(default=True, description="Dump databases too")
    destinations: list[ScheduleDestination] = Field(
        default_factory=list, description="Remote destinations to push each backup to"
    )

    @field_validator("schedule")
    @classmethod
    def _schedule_the_scheduler_accepts(cls, value: str) -> str:
        """
        Refuse here what the scheduler would refuse, in the scheduler's words.

        Args:
            value: The alias or calendar expression as it arrived.

        Returns:
            The value unchanged; the scheduler expands the alias itself.

        Raises:
            ValueError: When the scheduler would not write this into a unit
                file. FastAPI answers it as a 422 with the message as detail.
        """
        try:
            validate_calendar(value)
        except BackupError as exc:
            raise ValueError(f"{exc}. {exc.details}".strip()) from exc
        return value


class ScheduleActionResponse(BaseModel):
    """Response for a schedule action that completed immediately."""

    success: bool
    message: str
    schedule: BackupScheduleInfo | None = None


def _to_info(entry: dict[str, str]) -> BackupScheduleInfo:
    """
    Convert one of the scheduler's listing entries into the API model.

    Args:
        entry: A dictionary as :meth:`BackupScheduler.list_schedules` builds
            it - retention and destinations come from the store row it merges
            in, when one exists.

    Returns:
        The API representation.
    """
    on_calendar = entry.get("on_calendar", "")
    domain = entry.get("domain") or entry.get("app_name", "")
    retention_count = entry.get("retention_count") or ""
    retention_days = entry.get("retention_days") or ""

    record = get_store().get_backup_schedule(domain)
    destinations = (
        [ScheduleDestination(**dest) for dest in record.destinations if "name" in dest]
        if record is not None
        else []
    )

    return BackupScheduleInfo(
        domain=domain,
        app_name=entry.get("app_name", ""),
        timer=entry.get("timer", ""),
        schedule=_ALIAS_BY_CALENDAR.get(on_calendar, "custom" if on_calendar else "unknown"),
        on_calendar=on_calendar,
        next_run=entry.get("next_run", "pending"),
        last_run=entry.get("last_run", "never"),
        retention_count=int(retention_count) if retention_count else None,
        retention_days=int(retention_days) if retention_days else None,
        destinations=destinations,
    )


@router.get("", response_model=ScheduleListResponse)
def list_schedules(
    session: Annotated[dict, Depends(get_current_session)],
) -> ScheduleListResponse:
    """
    List every scheduled backup on this machine, with its next run.

    Listing is also when a timer that predates schema v10 is adopted into
    the store - see :meth:`BackupScheduler.list_schedules`.

    Args:
        session: The authenticated session.

    Returns:
        The schedules, merging what systemd reports with each one's store row.
    """
    entries = BackupScheduler(verbose=False).list_schedules()
    return ScheduleListResponse(
        schedules=[_to_info(entry) for entry in entries], total=len(entries)
    )


def _upsert_schedule(
    domain: str, data: CreateScheduleRequest, session: dict, *, verb: str
) -> ScheduleActionResponse:
    """
    Create or replace an application's backup schedule.

    ``BackupScheduler.create_schedule`` is itself an upsert - the same call
    creates a schedule that does not exist yet and replaces one that does -
    so ``POST`` and ``PUT`` share this one implementation.

    Args:
        domain: Validated domain.
        data: The schedule request.
        session: The authenticated, elevated session.
        verb: Past-tense verb for the response message ("created", "updated").

    Returns:
        The action outcome, carrying the schedule as it now stands.
    """
    schedule = BackupSchedule(
        domain=domain,
        app_name=domain_to_app_name(domain),
        schedule=data.schedule,
        include_databases=data.include_databases,
        retention_count=data.retention_count,
        retention_days=data.retention_days,
        destinations=[dump_model(dest) for dest in data.destinations],
    )

    audit_log.info(
        "%s_backup_schedule domain=%s schedule=%s session=%s",
        verb,
        domain,
        schedule.on_calendar,
        actor_label(session),
    )
    BackupScheduler(verbose=False).create_schedule(schedule)

    return ScheduleActionResponse(
        success=True,
        message=f"Backup schedule {verb} for {domain}",
        schedule=BackupScheduleInfo(
            domain=domain,
            app_name=schedule.app_name,
            timer=schedule.timer_name,
            schedule=data.schedule if data.schedule in SCHEDULE_ALIASES else "custom",
            on_calendar=schedule.on_calendar,
            next_run="pending",
            last_run="never",
            retention_count=data.retention_count,
            retention_days=data.retention_days,
            destinations=data.destinations,
        ),
    )


@router.post("", response_model=ScheduleActionResponse, status_code=201)
def create_schedule(
    data: CreateScheduleRequest, session: Annotated[dict, Depends(require_elevated)]
) -> ScheduleActionResponse:
    """
    Schedule automatic backups of an application on a systemd timer.

    Scheduling the same domain again rewrites its unit pair, so this is also
    how a schedule is changed - ``PUT /{domain}`` calls the same
    implementation. Sudo mode, like deleting one: a schedule is a root timer,
    and its retention decides which backups are thrown away.

    Args:
        data: The schedule request. Its calendar expression was already
            checked against the scheduler's own rules by the request model.
        session: The authenticated session, elevated.

    Returns:
        The action outcome, carrying the schedule as created.

    Raises:
        BackupError: When a unit cannot be written or the timer cannot be
            enabled.
    """
    return _upsert_schedule(strict_domain(data.domain), data, session, verb="created")


@router.put("/{domain}", response_model=ScheduleActionResponse)
def update_schedule(
    domain: str, data: CreateScheduleRequest, session: Annotated[dict, Depends(require_elevated)]
) -> ScheduleActionResponse:
    """
    Change an application's backup schedule.

    Args:
        domain: Domain whose schedule is changed; must match ``data.domain``.
        data: The new schedule.
        session: The authenticated session, elevated.

    Returns:
        The action outcome, carrying the schedule as it now stands.

    Raises:
        HTTPException: 400 when ``domain`` and ``data.domain`` disagree.
        BackupError: When a unit cannot be written or the timer cannot be
            enabled.
    """
    validated = strict_domain(domain)
    if strict_domain(data.domain) != validated:
        raise HTTPException(
            status_code=400, detail="The domain in the path and in the request body must match."
        )
    return _upsert_schedule(validated, data, session, verb="updated")


@router.delete("/{domain}", response_model=ScheduleActionResponse)
def delete_schedule(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> ScheduleActionResponse:
    """
    Remove an application's backup schedule.

    Removing the units stops future backups from ever running, silently -
    D5's sudo mode list treats it the same as any other destructive delete,
    so a cookie session has to confirm itself first; an admin-scoped Bearer
    credential is exempt, per :func:`wasm.web.api.deps.ensure_elevated`.

    Args:
        domain: Domain whose schedule is removed.
        session: The authenticated, elevated session.

    Returns:
        The action outcome.

    Raises:
        HTTPException: 404 when no schedule exists for the domain, so deleting
            a schedule that was never created does not report success.
    """
    validated = strict_domain(domain)
    scheduler = BackupScheduler(verbose=False)
    if scheduler.get_schedule(validated) is None:
        raise HTTPException(status_code=404, detail=f"No backup schedule for {validated}")

    audit_log.info(
        "delete_backup_schedule domain=%s session=%s",
        validated,
        actor_label(session),
    )
    scheduler.remove_schedule(validated)

    return ScheduleActionResponse(success=True, message=f"Backup schedule removed for {validated}")
