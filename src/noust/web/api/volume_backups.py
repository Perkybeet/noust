# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/volume-backups``: an application's data, backed up on its own.

A client of one class,
:class:`~noust.managers.volume_backups.VolumeBackupManager`, which ``noust
backup volumes`` calls too. What an application backup is for the whole
application, this is for the part of it that changes every minute: its
persistent directories and the Docker volumes of its stack, each archived on
its own, on its own schedule, kept by its own retention and sent to the same
destinations.

Taking, checking, sending and restoring are jobs, never a request: an archive
is as big as the data it protects, and a central's proxy cuts a request at 300
seconds. A policy is a root timer whose retention decides what is thrown away,
a restore replaces data and a deletion removes it, so each needs sudo mode.

Literal paths (``policies``, ``targets``, ``remote``, ``restore-remote``) come
before the parametrised ``/{backup_id}``.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.audit import Actor
from noust.core.exceptions import BackupError
from noust.deployers.recorder import CapturingLogger
from noust.managers.backup_scheduler import validate_calendar
from noust.managers.volume_backups import (
    VolumeBackupManager,
    VolumeBackupMetadata,
    VolumeBackupNotFound,
    VolumePolicyView,
)
from noust.validators.domain import validate_domain
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import ActionResponse, queue, session_actor
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType
from noust.web.pydantic_compat import dump_model, field_validator, iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)


# ------------------------------------------------------------------- models


class TargetResponse(BaseModel):
    """
    One piece of an application's data.

    Attributes:
        key: What a policy names it by (``path:uploads``, ``volume:shop_pgdata``).
        kind: ``path`` or ``docker``.
        source: The relative path under the data root, or the volume's name.
        path: Where a path target is on the server.
        exists: Whether there is something to back up now.
        declared_by: Where it comes from.
        slug: The target as a file name.
    """

    key: str
    kind: str
    source: str
    path: str | None = None
    exists: bool
    declared_by: str
    slug: str


class VolumeBackupResponse(BaseModel):
    """
    One data backup.

    Attributes:
        id: ``<application>.<target slug>_<YYYYMMDD>_<HHMMSS>``.
        domain: The application.
        target: The target's key.
        kind: ``path`` or ``docker``.
        created_at: When, with its UTC offset.
        size_bytes: The archive's size.
        checksum: Its SHA-256.
        consistency: How still the data was kept.
        tags: ``scheduled`` for a schedule's, ``safety`` for a restore's.
        description: A note.
        sqlite_files: SQLite databases snapshotted whole.
        skipped: What was left out and why.
        remote_copies: Where it was sent.
        last_verified_at: When it was last checked.
        verified_ok: That check's verdict; null before one.
    """

    id: str
    domain: str
    target: str
    kind: str
    created_at: str
    size_bytes: int
    checksum: str | None = None
    consistency: str = "live"
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    sqlite_files: list[str] = Field(default_factory=list)
    skipped: list[dict[str, str]] = Field(default_factory=list)
    remote_copies: list[dict[str, Any]] = Field(default_factory=list)
    last_verified_at: str | None = None
    verified_ok: bool | None = None

    _iso_timestamps = iso_offset_validator("created_at", "last_verified_at")


class VolumeBackupListResponse(BaseModel):
    """The data backups on this server, newest first."""

    backups: list[VolumeBackupResponse]
    total: int


class PolicyDestination(BaseModel):
    """
    One destination a policy sends its backups to.

    Attributes:
        name: A destination created under ``/api/backup-destinations``.
        retention_count: This server's scheduled backups of a target to keep
            there; null for no limit.
        retention_days: Maximum age in days of those; null for no limit.
        retention_gb: The most those may weigh together there, in gigabytes; null
            for no limit. The newest always stays.
    """

    name: str
    retention_count: int | None = Field(default=None, ge=1, le=365)
    retention_days: int | None = Field(default=None, ge=1, le=3650)
    retention_gb: float | None = Field(default=None, gt=0, le=1_000_000)


class PolicyDestinationState(PolicyDestination):
    """
    A destination a policy names, and what it is now.

    Attributes:
        exists: Whether the destination is still configured.
        encrypted: Whether it wraps what it stores in rclone's crypt.
    """

    exists: bool = True
    encrypted: bool = False


class VolumePolicyResponse(BaseModel):
    """
    An application's data backup policy and how it is going.

    Attributes:
        domain: The application.
        configured: Whether it has a policy; when it does not, the rest is empty.
        schedule: The calendar expression.
        targets: What is backed up; empty is everything the application declares.
        extra_paths: Directories added to what it declares.
        retention_count: Scheduled backups kept locally per target.
        retention_days: Days a scheduled backup is kept locally.
        retention_gb: The most the scheduled backups kept locally per target may
            weigh together, in gigabytes; null for no limit.
        remote_only: Whether the server keeps nothing: each scheduled backup is deleted here
            once every destination holds it and its upload was checked.
        destinations: Where each backup is sent.
        consistency: ``live``, ``pause`` or ``stop``.
        enabled: Whether the timer exists.
        timer: What systemd says of the timer.
        last_run_at: When the policy last ran.
        last_status: ``ok`` or ``failed``; null before the first run.
        last_error: What the last failed run said, verbatim.
        last_backup: The last backup it took.
        last_success_at: When a run last went everywhere it was meant to.
    """

    domain: str
    configured: bool
    schedule: str | None = None
    targets: list[str] = Field(default_factory=list)
    extra_paths: list[str] = Field(default_factory=list)
    retention_count: int | None = None
    retention_days: int | None = None
    retention_gb: float | None = None
    remote_only: bool = False
    destinations: list[PolicyDestinationState] = Field(default_factory=list)
    consistency: str = "live"
    enabled: bool = False
    timer: dict[str, Any] = Field(default_factory=dict)
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_backup: str | None = None
    last_success_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    _iso_timestamps = iso_offset_validator(
        "last_run_at", "last_success_at", "created_at", "updated_at"
    )


class VolumePolicyListResponse(BaseModel):
    """Every application's data backup policy."""

    policies: list[VolumePolicyResponse]
    total: int


class SetVolumePolicyRequest(BaseModel):
    """
    Request to create or replace an application's data backup policy.

    Attributes:
        schedule: ``hourly``, ``daily``, ``weekly``, ``monthly`` or a systemd
            ``OnCalendar`` expression.
        targets: Keys to back up; empty is every target the application declares.
        extra_paths: Data directories to add, relative to where its data lives.
        retention_count: Scheduled backups to keep locally per target.
        retention_days: Days to keep a scheduled backup locally.
        retention_gb: The most the scheduled backups kept locally per target may
            weigh together, in gigabytes; the newest always stays.
        remote_only: Delete each scheduled backup from this server once every destination
            holds it and its upload was checked (a small disk). Needs a destination.
        destinations: Remote copies, each with its own retention.
        consistency: ``live``, ``pause`` (freeze it for the copy) or ``stop``.
        enabled: Whether the timer runs; a disabled policy keeps its settings.
    """

    schedule: str = Field(default="daily")
    targets: list[str] = Field(default_factory=list)
    extra_paths: list[str] = Field(default_factory=list)
    retention_count: int | None = Field(default=7, ge=1, le=365)
    retention_days: int | None = Field(default=30, ge=1, le=3650)
    retention_gb: float | None = Field(default=None, gt=0, le=1_000_000)
    remote_only: bool = False
    destinations: list[PolicyDestination] = Field(default_factory=list)
    consistency: str = "live"
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


class CreateVolumeBackupRequest(BaseModel):
    """
    Request to back an application's data up now.

    Attributes:
        domain: The application.
        targets: Keys to back up; empty is every target it declares.
        extra_paths: Data directories to add.
        consistency: ``live``, ``pause`` or ``stop``.
        description: A note kept with each backup.
    """

    domain: str
    targets: list[str] = Field(default_factory=list)
    extra_paths: list[str] = Field(default_factory=list)
    consistency: str = "live"
    description: str = Field(default="", max_length=500)


class RestoreVolumeBackupRequest(BaseModel):
    """
    Request to put an application's data back as a backup held it.

    Attributes:
        safety_backup: Archive what is there first. Turning it off leaves
            nothing to go back to when the restore fails.
    """

    safety_backup: bool = True


class PushVolumeBackupRequest(BaseModel):
    """
    Request to send a backup to a destination.

    Attributes:
        destination: A destination created under ``/api/backup-destinations``.
    """

    destination: str


class RestoreRemoteRequest(BaseModel):
    """
    Request to restore data from a backup that lives on a destination.

    Attributes:
        destination: The destination.
        domain: The application.
        slug: The target's folder there (see ``/remote/{domain}``).
        file_name: The archive's name there.
        safety_backup: Archive what is there first.
    """

    destination: str
    domain: str
    slug: str
    file_name: str
    safety_backup: bool = True


# ----------------------------------------------------------------- helpers


def _manager(
    session: dict[str, Any] | None = None,
    job_context: JobContext | None = None,
    actor: str | None = None,
    step: str = "",
) -> VolumeBackupManager:
    """
    Build the manager a request or a job uses.

    Args:
        session: The authenticated session of a request, whose actor the audit
            trail names.
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
    return VolumeBackupManager(logger=logger, actor=who)


#: What a backup's sidecar shows the API: not the owner's ids, the MAC or the source path.
_BACKUP_KEYS = (
    "id",
    "domain",
    "target",
    "kind",
    "created_at",
    "size_bytes",
    "checksum",
    "consistency",
    "tags",
    "description",
    "sqlite_files",
    "skipped",
    "remote_copies",
    "last_verified_at",
    "verified_ok",
)


def _backup_response(entry: VolumeBackupMetadata) -> VolumeBackupResponse:
    """
    Args:
        entry: A backup.

    Returns:
        The response model.
    """
    data = entry.to_dict()
    return VolumeBackupResponse(**{key: data[key] for key in _BACKUP_KEYS})


@contextmanager
def _answering_404() -> Iterator[None]:
    """
    Answer a missing application or backup as a 404, not as a failed operation.

    Yields:
        Nothing.

    Raises:
        HTTPException: 404 when the block finds nothing to act on.
    """
    try:
        yield
    except VolumeBackupNotFound as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc


def _policy_response(view: VolumePolicyView) -> VolumePolicyResponse:
    """
    Args:
        view: A policy and its timer.

    Returns:
        The response model.
    """
    return VolumePolicyResponse(**view.to_dict())


def _done(job_context: JobContext | None) -> None:
    """
    Mark a job's progress complete.

    Args:
        job_context: The running job, when there is one.
    """
    if job_context is not None:
        job_context.update("Done", 100)


# ---------------------------------------------------------------- policies


@router.get("/policies", response_model=VolumePolicyListResponse)
def list_policies(
    session: Annotated[dict, Depends(get_current_session)],
) -> VolumePolicyListResponse:
    """
    List every application's data backup policy.

    Args:
        session: The authenticated session.

    Returns:
        The policies with their timers' state.
    """
    views = _manager(session).list_policies()
    return VolumePolicyListResponse(
        policies=[_policy_response(view) for view in views], total=len(views)
    )


@router.get("/policies/{domain}", response_model=VolumePolicyResponse)
def get_policy(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> VolumePolicyResponse:
    """
    Read one application's data backup policy.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The policy; ``configured`` is false when it has none.
    """
    return _policy_response(_manager(session).get_policy(domain))


@router.put("/policies/{domain}", response_model=VolumePolicyResponse)
def set_policy(
    domain: str,
    request: SetVolumePolicyRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> VolumePolicyResponse:
    """
    Create or replace an application's data backup policy, and its timer.

    Sudo mode: the policy is a root timer, and its retention decides which
    backups are thrown away.

    Args:
        domain: The application.
        request: The policy.
        session: The authenticated, elevated session.

    Returns:
        The policy as stored.

    Raises:
        ValidationError: 400 for an unknown destination, target or an unusable value.
    """
    view = _manager(session).set_policy(
        domain,
        schedule=request.schedule,
        targets=request.targets,
        extra_paths=request.extra_paths,
        retention_count=request.retention_count,
        retention_days=request.retention_days,
        retention_gb=request.retention_gb,
        remote_only=request.remote_only,
        destinations=[dump_model(entry) for entry in request.destinations],
        consistency=request.consistency,
        enabled=request.enabled,
    )
    return _policy_response(view)


@router.delete("/policies/{domain}", response_model=ActionResponse)
def remove_policy(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Remove an application's data backup policy and its timer. Its backups stay.

    Args:
        domain: The application.
        session: The authenticated, elevated session.

    Returns:
        The outcome; ``success`` is false when there was no policy.
    """
    removed = _manager(session).remove_policy(domain)
    return ActionResponse(
        success=removed,
        message=f"Data backup policy removed for {domain}" if removed else "There was no policy",
    )


def policy_run_job(
    domain: str, actor: str | None = None, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Run an application's policy: back up, check, send, prune.

    Args:
        domain: The application.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What :meth:`~noust.managers.volume_backups.VolumeBackupManager.run_policy` reports.
    """
    steps = {"count": 0}

    def progress(text: str) -> None:
        steps["count"] += 1
        if job_context is not None:
            job_context.update(text, min(90, 10 + 20 * steps["count"]))

    manager = _manager(None, job_context, actor, f"Running the data backup policy of {domain}")
    # The job manager announces a failed job itself; announcing here too would say it twice.
    result = manager.run_policy(domain, notify=False, on_step=progress)
    _done(job_context)
    return result


@router.post("/policies/{domain}/run", response_model=JobAcceptedResponse, status_code=202)
def run_policy(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue a run of an application's policy: the backup, its check, the sends and retention.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The queued job; its result names the backups and what each destination said.
    """
    domain = validate_domain(domain)
    with _answering_404():
        _manager(session).require_app(domain)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Back up the data of {domain}",
        description=f"Running the data backup policy of {domain}",
        func=policy_run_job,
        kwargs={"domain": domain},
        metadata={"domain": domain, "kind": "volumes"},
        message=f"Backing up the data of {domain}",
    )


# ----------------------------------------------------------------- targets


@router.get("/targets/{domain}", response_model=list[TargetResponse])
def list_targets(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    path: Annotated[list[str] | None, Query(description="Also consider this directory")] = None,
) -> list[TargetResponse]:
    """
    List the data an application declares: its persistent paths and Compose volumes.

    Args:
        domain: The application.
        session: The authenticated session.
        path: Directories to consider besides what it declares.

    Returns:
        The targets, with whether each exists yet.
    """
    with _answering_404():
        found = _manager(session).targets(domain, extra_paths=path or [])
    return [TargetResponse(**target.to_dict()) for target in found]


# ----------------------------------------------------------------- backups


@router.get("", response_model=VolumeBackupListResponse)
def list_backups(
    session: Annotated[dict, Depends(get_current_session)],
    domain: Annotated[str | None, Query(description="Only this application's")] = None,
    target: Annotated[str | None, Query(description="Only this target's key")] = None,
) -> VolumeBackupListResponse:
    """
    List the data backups on this server, newest first.

    Args:
        session: The authenticated session.
        domain: Only this application's.
        target: Only this target's.

    Returns:
        The backups whose archive is present.
    """
    found = _manager(session).list_backups(domain, target)
    return VolumeBackupListResponse(backups=[_backup_response(e) for e in found], total=len(found))


def create_job(
    domain: str,
    targets: list[str],
    extra_paths: list[str],
    consistency: str,
    description: str,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Back an application's data up.

    Args:
        domain: The application.
        targets: Keys to back up.
        extra_paths: Directories to add.
        consistency: ``live``, ``pause`` or ``stop``.
        description: A note.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The backups taken.
    """
    manager = _manager(None, job_context, actor, f"Backing up the data of {domain}")
    taken = manager.create(
        domain,
        targets=targets,
        extra_paths=extra_paths,
        consistency=consistency,
        description=description,
    )
    _done(job_context)
    return {"backups": [entry.to_dict() for entry in taken]}


@router.post("", response_model=JobAcceptedResponse, status_code=202)
def create_backup(
    request: CreateVolumeBackupRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue backing an application's data up now.

    Args:
        request: The application, the targets and how still to keep the data.
        session: The authenticated session.

    Returns:
        The queued job; its result lists the backups.

    Raises:
        ValidationError: 400 for an unknown target or consistency mode.
    """
    domain = validate_domain(request.domain)
    with _answering_404():
        _manager(session).plan(
            domain,
            targets=request.targets,
            extra_paths=request.extra_paths,
            consistency=request.consistency,
        )
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Back up the data of {domain}",
        description=f"Backing up the data of {domain}",
        func=create_job,
        kwargs={
            "domain": domain,
            "targets": request.targets,
            "extra_paths": request.extra_paths,
            "consistency": request.consistency,
            "description": request.description,
        },
        metadata={"domain": domain, "kind": "volumes"},
        message=f"Backing up the data of {domain}",
    )


@router.get("/remote/{domain}", response_model=dict[str, list[dict[str, Any]]])
def remote_backups(
    domain: str,
    destination: Annotated[str, Query(description="A destination")],
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, list[dict[str, Any]]]:
    """
    List what a destination holds of an application's data, by target.

    Args:
        domain: The application.
        destination: The destination.
        session: The authenticated session.

    Returns:
        Each target's folder to its archives, newest first.
    """
    return _manager(session).remote_backups(destination, domain)


def restore_remote_job(
    destination: str,
    domain: str,
    slug: str,
    file_name: str,
    safety_backup: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Download a backup from a destination and restore it.

    Args:
        destination: The destination.
        domain: The application.
        slug: The target's folder there.
        file_name: The archive's name there.
        safety_backup: Archive what is there first.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What the restore did.
    """
    manager = _manager(None, job_context, actor, f"Restoring the data of {domain}")
    result = manager.restore_remote(
        destination, domain, slug, file_name, safety_backup=safety_backup
    )
    _done(job_context)
    return result


@router.post("/restore-remote", response_model=JobAcceptedResponse, status_code=202)
def restore_remote(
    request: RestoreRemoteRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue restoring an application's data from a backup on a destination, with sudo mode.

    Args:
        request: The destination, the application and the archive.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    domain = validate_domain(request.domain)
    return queue(
        session,
        job_type=JobType.RESTORE,
        name=f"Restore the data of {domain}",
        description=f"Restoring the data of {domain} from {request.file_name} on "
        f"{request.destination}",
        func=restore_remote_job,
        kwargs={
            "destination": request.destination,
            "domain": domain,
            "slug": request.slug,
            "file_name": request.file_name,
            "safety_backup": request.safety_backup,
        },
        metadata={"domain": domain, "kind": "volumes"},
        message=f"Restoring the data of {domain}",
    )


@router.get("/{backup_id}", response_model=VolumeBackupResponse)
def get_backup(
    backup_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> VolumeBackupResponse:
    """
    Read one data backup.

    Args:
        backup_id: The id.
        session: The authenticated session.

    Returns:
        The backup.

    Raises:
        HTTPException: 404 when there is no such backup.
    """
    entry = _manager(session).get_backup(backup_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Volume backup not found: {backup_id}")
    return _backup_response(entry)


def verify_job(
    backup_id: str,
    deep: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Check a data backup.

    Args:
        backup_id: The id.
        deep: Also extract it as a restore would.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        The verdict.

    Raises:
        BackupError: When it does not verify, with the reasons.
    """
    manager = _manager(None, job_context, actor, f"Checking {backup_id}")
    verdict = manager.verify(backup_id, deep=deep)
    if not verdict["valid"]:
        raise BackupError(f"{backup_id} failed its check", details="; ".join(verdict["errors"]))
    _done(job_context)
    return verdict


@router.post("/{backup_id}/verify", response_model=JobAcceptedResponse, status_code=202)
def verify_backup(
    backup_id: str,
    session: Annotated[dict, Depends(get_current_session)],
    deep: Annotated[bool, Query(description="Also extract it as a restore would")] = False,
) -> JobAcceptedResponse:
    """
    Queue checking a data backup.

    Args:
        backup_id: The id.
        session: The authenticated session.
        deep: Also extract it.

    Returns:
        The queued job; its result is the verdict.
    """
    with _answering_404():
        _manager(session).require(backup_id)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Check {backup_id}",
        description=f"Checking the data backup {backup_id}",
        func=verify_job,
        kwargs={"backup_id": backup_id, "deep": deep},
        metadata={"backup_id": backup_id, "kind": "volumes"},
        message=f"Checking {backup_id}",
    )


def push_job(
    backup_id: str,
    destination: str,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Send a data backup to a destination.

    Args:
        backup_id: The id.
        destination: The destination.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What the destination reports.
    """
    manager = _manager(None, job_context, actor, f"Sending {backup_id} to {destination}")
    summary = manager.push(backup_id, destination)
    _done(job_context)
    return summary


@router.post("/{backup_id}/push", response_model=JobAcceptedResponse, status_code=202)
def push_backup(
    backup_id: str,
    request: PushVolumeBackupRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> JobAcceptedResponse:
    """
    Queue sending a data backup to a destination.

    Args:
        backup_id: The id.
        request: The destination.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    with _answering_404():
        _manager(session).require(backup_id)
    return queue(
        session,
        job_type=JobType.PUSH,
        name=f"Send {backup_id}",
        description=f"Sending the data backup {backup_id} to {request.destination}",
        func=push_job,
        kwargs={"backup_id": backup_id, "destination": request.destination},
        metadata={"backup_id": backup_id, "kind": "volumes"},
        message=f"Sending {backup_id} to {request.destination}",
    )


def restore_job(
    backup_id: str,
    safety_backup: bool = True,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Put an application's data back as a backup held it.

    Args:
        backup_id: The id.
        safety_backup: Archive what is there first.
        actor: Who queued it.
        job_context: Injected by the job manager.

    Returns:
        What the restore did.
    """
    manager = _manager(None, job_context, actor, f"Restoring {backup_id}")
    result = manager.restore(backup_id, safety_backup=safety_backup)
    _done(job_context)
    return result


@router.post("/{backup_id}/restore", response_model=JobAcceptedResponse, status_code=202)
def restore_backup(
    backup_id: str,
    request: RestoreVolumeBackupRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue restoring an application's data from a backup, with sudo mode.

    The data is replaced, not merged: the application is stopped, a safety copy
    of what is there is taken, and the application is started and must pass its
    health gate; when it does not, the previous data is put back.

    Args:
        backup_id: The id.
        request: Whether to take the safety copy.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    with _answering_404():
        entry = _manager(session).require(backup_id)
    return queue(
        session,
        job_type=JobType.RESTORE,
        name=f"Restore the data of {entry.domain}",
        description=f"Restoring {entry.target} of {entry.domain} from {backup_id}",
        func=restore_job,
        kwargs={"backup_id": backup_id, "safety_backup": request.safety_backup},
        metadata={"domain": entry.domain, "backup_id": backup_id, "kind": "volumes"},
        message=f"Restoring {entry.target} of {entry.domain}",
    )


@router.delete("/{backup_id}", response_model=ActionResponse)
def delete_backup(
    backup_id: str, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Delete a data backup from this server. Copies on destinations stay.

    Args:
        backup_id: The id.
        session: The authenticated, elevated session.

    Returns:
        The outcome.

    Raises:
        HTTPException: 404 when there is no such backup.
    """
    with _answering_404():
        _manager(session).delete(backup_id)
    return ActionResponse(success=True, message=f"Deleted {backup_id}")
