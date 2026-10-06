# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/self-backup``: Noust's own state, in one encrypted file.

A client of :class:`~noust.managers.self_backup.SelfBackupManager`, which ``noust
self-backup`` calls too. What the console can do with a bundle is look after
it: see which there are, take one, prove one restorable, send one away, keep a
schedule. What it cannot do is restore one: a restore stops the console (and
the monitor, and the timers) while the store it is running on is replaced, so
it is ``noust self-backup restore`` at a terminal, which survives that. Nor does
it download one: a bundle holds every secret the server has, so it leaves by a
destination that is encrypted, or by the hand of someone with a shell.

The passphrase a bundle is sealed under travels in a request body only when an
operator sets or changes it (``PUT /schedule``), and is kept in the secret
store. Taking a bundle by hand and proving one use the stored one unless the
request brings its own.

Taking, proving and sending are jobs. A schedule is a root timer that carries
every secret of the server away, and a passphrase kept where the timer can read
it, so setting or removing one needs ``root_equivalent`` and sudo mode.

Literal paths (``schedule``, ``run``) come before the parametrised ``/{name}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.core.exceptions import BackupError, ValidationError
from noust.deployers.recorder import CapturingLogger
from noust.managers.backup_scheduler import validate_calendar
from noust.managers.self_backup import SelfBackupManager, SelfBackupNotFound
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import ActionResponse, queue, session_actor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType
from noust.web.pydantic_compat import dump_model, field_validator, iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)


# ------------------------------------------------------------------- models


class BundleResponse(BaseModel):
    """
    One bundle of Noust's own state on this server.

    Attributes:
        name: The file's name, which every other call names it by.
        size: Bytes.
        created_at: When it was taken, with its UTC offset.
        host: The server that took it.
        kind: ``manual``, ``scheduled`` or ``pre-restore``.
        scheduled: A schedule took it: the only kind its retention deletes.
        sha256: Its digest, from its sidecar.
        remote_copies: Where it was sent.
        sidecar_mac: What the sidecar's MAC says.
    """

    name: str
    size: int
    created_at: str | None = None
    host: str | None = None
    kind: str = "manual"
    scheduled: bool = False
    sha256: str | None = None
    remote_copies: list[dict[str, Any]] = Field(default_factory=list)
    sidecar_mac: str = "unsigned"

    _iso_timestamps = iso_offset_validator("created_at")


class ScheduleDestination(BaseModel):
    """
    One destination bundles are sent to.

    Attributes:
        name: A destination created under ``/api/backup-destinations``.
        retention_count: This server's scheduled bundles to keep there.
        retention_days: Maximum age in days of those.
    """

    name: str
    retention_count: int | None = Field(default=None, ge=1, le=365)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class ScheduleResponse(BaseModel):
    """
    The schedule of Noust's own state backup, and how it is going.

    Attributes:
        configured: Whether there is one; when there is not the rest is empty.
        schedule: The calendar expression.
        retention_count: Scheduled bundles kept locally.
        retention_days: Days a scheduled bundle is kept locally.
        destinations: Where each bundle is sent, and whether each still exists
            and is encrypted.
        enabled: Whether the timer exists.
        passphrase_stored: Whether a passphrase is kept for the timer; null
            when the secret store is locked and cannot say.
        timer: What systemd says of the timer.
        last_run_at: When it last ran.
        last_status: ``ok`` or ``failed``; null before the first run.
        last_error: What the last failed run said, verbatim.
        last_file: The last bundle it took.
        last_success_at: When a run last went everywhere it was meant to.
        last_verified_at: When a bundle was last proven restorable.
        last_verify_status: ``ok`` or ``failed`` for that.
    """

    configured: bool
    schedule: str | None = None
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[dict[str, Any]] = Field(default_factory=list)
    enabled: bool = False
    passphrase_stored: bool | None = None
    timer: dict[str, Any] = Field(default_factory=dict)
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_file: str | None = None
    last_success_at: str | None = None
    last_verified_at: str | None = None
    last_verify_status: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    _iso_timestamps = iso_offset_validator(
        "last_run_at", "last_success_at", "last_verified_at", "created_at", "updated_at"
    )


class OverviewResponse(BaseModel):
    """
    What there is, and how it is kept up.

    Attributes:
        bundles: The bundles on this server, newest first.
        schedule: The schedule.
    """

    bundles: list[BundleResponse]
    schedule: ScheduleResponse


class SetScheduleRequest(BaseModel):
    """
    Request to create or replace the schedule.

    Attributes:
        schedule: ``hourly``, ``daily``, ``weekly``, ``monthly`` or a systemd
            ``OnCalendar`` expression.
        retention_count: Scheduled bundles to keep locally.
        retention_days: Days to keep a scheduled bundle locally.
        destinations: Remote copies, each with its own retention.
        passphrase: What bundles are sealed under; kept in the secret store
            for the timer. Required while none is stored, optional after.
        enabled: Whether the timer runs.
    """

    schedule: str = "daily"
    retention_count: int | None = Field(default=7, ge=1, le=365)
    retention_days: int | None = Field(default=30, ge=1, le=3650)
    destinations: list[ScheduleDestination] = Field(default_factory=list)
    passphrase: str | None = Field(default=None, min_length=12, max_length=1024)
    enabled: bool = True

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
            ValueError: When the scheduler would not write this into a unit file.
        """
        try:
            validate_calendar(value)
        except BackupError as exc:
            raise ValueError(f"{exc}. {exc.details}".strip()) from exc
        return value


class PassphraseRequest(BaseModel):
    """
    A request that may bring its own passphrase.

    Attributes:
        passphrase: What the bundle is sealed under; the stored one when absent.
    """

    passphrase: str | None = Field(default=None, min_length=1, max_length=1024)


class PushRequest(BaseModel):
    """
    Request to send a bundle to a destination.

    Attributes:
        destination: A destination created under ``/api/backup-destinations``.
    """

    destination: str


# ----------------------------------------------------------------- helpers


def _manager(
    session: dict[str, Any] | None = None,
    job_context: JobContext | None = None,
    actor: str | None = None,
    step: str = "",
) -> SelfBackupManager:
    """
    Build the manager a request or a job uses.

    Args:
        session: The authenticated session of a request.
        job_context: The running job; its log receives what the manager reports.
        actor: The label of who queued a job.
        step: The job's first step.

    Returns:
        The manager.
    """
    logger = CapturingLogger(verbose=False)
    who: Actor | None = session_actor(session) if session is not None else None
    if job_context is not None:
        job_context.update(step or "Working", 10)
        logger.attach_sink(job_context.log)
        who = Actor.from_label(actor) if actor else None
    return SelfBackupManager(logger=logger, actor=who)


@contextmanager
def _answering_404() -> Iterator[None]:
    """
    Answer a missing bundle as a 404, not as a failed operation.

    Yields:
        Nothing.

    Raises:
        HTTPException: 404 when the block finds no such bundle.
    """
    try:
        yield
    except SelfBackupNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


def _done(job_context: JobContext | None) -> None:
    """
    Mark a job's progress complete.

    Args:
        job_context: The running job, when there is one.
    """
    if job_context is not None:
        job_context.update("Done", 100)


def _passphrase(manager: SelfBackupManager, given: str | None) -> str:
    """
    Pick the passphrase a request or a job seals or opens a bundle with.

    Args:
        manager: The manager, whose secret store holds the stored one.
        given: What the request brought, if anything.

    Returns:
        The passphrase.

    Raises:
        ValidationError: When none is given and none is stored.
    """
    chosen = given or manager.stored_passphrase()
    if not chosen:
        raise ValidationError(
            "No passphrase was given and none is stored",
            details="Give one with the request, or set the schedule with a passphrase.",
            field="passphrase",
        )
    return chosen


# ---------------------------------------------------------------- overview


@router.get("", response_model=OverviewResponse)
def overview(session: Annotated[dict, Depends(get_current_session)]) -> OverviewResponse:
    """
    List the bundles on this server and show the schedule.

    Args:
        session: The authenticated session.

    Returns:
        The bundles, newest first, and the schedule with its timer.
    """
    manager = _manager(session)
    bundles = [BundleResponse(**_entry(entry.to_dict())) for entry in manager.entries()]
    return OverviewResponse(bundles=bundles, schedule=ScheduleResponse(**manager.get_schedule()))


def _entry(data: dict[str, Any]) -> dict[str, Any]:
    """
    Args:
        data: A bundle entry as plain data.

    Returns:
        What the API shows of it: not where it is on disk.
    """
    data.pop("path", None)
    return data


# ---------------------------------------------------------------- schedule


@router.put("/schedule", response_model=ScheduleResponse)
def set_schedule(
    request: SetScheduleRequest, session: Annotated[dict, Depends(require_elevated)]
) -> ScheduleResponse:
    """
    Create or replace the schedule, and its timer, with sudo mode.

    Args:
        request: The schedule.
        session: The authenticated, elevated session.

    Returns:
        The schedule as stored; the passphrase is not in it.

    Raises:
        ValidationError: 400 for an unknown destination, an unusable value, or
            no passphrase while none is stored.
    """
    view = _manager(session).set_schedule(
        schedule=request.schedule,
        retention_count=request.retention_count,
        retention_days=request.retention_days,
        destinations=[dump_model(entry) for entry in request.destinations],
        passphrase=request.passphrase,
        enabled=request.enabled,
    )
    return ScheduleResponse(**view)


@router.delete("/schedule", response_model=ActionResponse)
def remove_schedule(session: Annotated[dict, Depends(require_elevated)]) -> ActionResponse:
    """
    Remove the schedule and its timer. The bundles and the stored passphrase stay.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The outcome; ``success`` is false when there was no schedule.
    """
    removed = _manager(session).remove_schedule()
    return ActionResponse(
        success=removed, message="Schedule removed" if removed else "There was no schedule"
    )


# -------------------------------------------------------------------- jobs


def create_job(
    passphrase: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Take a bundle of Noust's own state.

    Args:
        passphrase: What to seal it under; the stored one when None.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The bundle, without where it is on disk.
    """
    manager = _manager(None, job_context, actor, "Backing up Noust's own state")
    bundle = manager.create(_passphrase(manager, passphrase))
    _done(job_context)
    return _entry(bundle.to_dict())


@router.post("", response_model=JobAcceptedResponse, status_code=202)
def create_bundle(
    request: PassphraseRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue taking a bundle now, under the given passphrase or the stored one.

    Args:
        request: The passphrase, optionally.
        session: The authenticated session.

    Returns:
        The queued job; its result is the bundle.

    Raises:
        ValidationError: 400 when no passphrase is given and none is stored.
    """
    _passphrase(_manager(session), request.passphrase)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name="Back up Noust's own state",
        description="Backing up Noust's own state into an encrypted file",
        func=create_job,
        kwargs={"passphrase": request.passphrase},
        metadata={"kind": "self-backup"},
        message="Backing up Noust's own state",
    )


def run_job(actor: str | None = None, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Run the schedule: back up, prove the bundle, send, prune.

    Args:
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What :meth:`~noust.managers.self_backup.SelfBackupManager.run_schedule` reports.
    """
    steps = {"count": 0}

    def progress(text: str) -> None:
        steps["count"] += 1
        if job_context is not None:
            job_context.update(text, min(90, 10 + 20 * steps["count"]))

    manager = _manager(None, job_context, actor, "Running the backup of Noust's own state")
    # The job manager announces a failed job itself; announcing here too would say it twice.
    result = manager.run_schedule(notify=False, on_step=progress)
    _done(job_context)
    return result


@router.post("/run", response_model=JobAcceptedResponse, status_code=202)
def run_schedule(session: Annotated[dict, Depends(get_current_session)]) -> JobAcceptedResponse:
    """
    Queue a run of the schedule: a bundle, its proof, the sends and retention.

    Args:
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: 400 when no passphrase is stored.
    """
    _passphrase(_manager(session), None)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name="Back up Noust's own state",
        description="Running the schedule of Noust's own state backup",
        func=run_job,
        kwargs={},
        metadata={"kind": "self-backup"},
        message="Backing up Noust's own state",
    )


def verify_job(
    name: str,
    passphrase: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Prove a bundle restorable without restoring it.

    Args:
        name: The bundle's name.
        passphrase: What it is sealed under; the stored one when None.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The report; a failure is the job's failure, carrying the problems.

    Raises:
        BackupError: When the bundle does not verify.
    """
    manager = _manager(None, job_context, actor, f"Proving {name}")
    report = manager.verify(name, _passphrase(manager, passphrase))
    if not report["ok"]:
        raise BackupError(f"{name} failed its verification", details="; ".join(report["problems"]))
    _done(job_context)
    return report


@router.post("/{name}/verify", response_model=JobAcceptedResponse, status_code=202)
def verify_bundle(
    name: str, request: PassphraseRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue proving a bundle restorable: its digests, its store's integrity, its schema.

    Args:
        name: The bundle's name.
        request: The passphrase, optionally.
        session: The authenticated session.

    Returns:
        The queued job; its result is the report.
    """
    manager = _manager(session)
    with _answering_404():
        manager.resolve(name)
    _passphrase(manager, request.passphrase)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Prove {name}",
        description=f"Proving the bundle {name} restorable",
        func=verify_job,
        kwargs={"name": name, "passphrase": request.passphrase},
        metadata={"kind": "self-backup", "backup_id": name},
        message=f"Proving {name}",
    )


def push_job(
    name: str,
    destination: str,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Send a bundle to a destination.

    Args:
        name: The bundle's name.
        destination: The destination.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What the destination reports.
    """
    manager = _manager(None, job_context, actor, f"Sending {name} to {destination}")
    summary = manager.push(name, destination)
    _done(job_context)
    return summary


@router.post("/{name}/push", response_model=JobAcceptedResponse, status_code=202)
def push_bundle(
    name: str, request: PushRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue sending a bundle to a destination.

    Args:
        name: The bundle's name.
        request: The destination.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    with _answering_404():
        _manager(session).resolve(name)
    return queue(
        session,
        job_type=JobType.PUSH,
        name=f"Send {name}",
        description=f"Sending the bundle {name} to {request.destination}",
        func=push_job,
        kwargs={"name": name, "destination": request.destination},
        metadata={"kind": "self-backup", "backup_id": name},
        message=f"Sending {name} to {request.destination}",
    )


@router.delete("/{name}", response_model=ActionResponse)
def delete_bundle(name: str, session: Annotated[dict, Depends(require_elevated)]) -> ActionResponse:
    """
    Delete a bundle from this server, with sudo mode. Copies on destinations stay.

    Args:
        name: The bundle's name.
        session: The authenticated, elevated session.

    Returns:
        The outcome.
    """
    with _answering_404():
        _manager(session).delete(name)
    return ActionResponse(success=True, message=f"Deleted {name}")
