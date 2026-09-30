# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Database backups: policies, verification, offsite copies, downloads, restores.

The endpoints of ``/api/databases`` that turn a dump into a backup. They build
on :mod:`.dumps` (listing, taking and restoring a local dump, which now also
report what verification found) and are, like it, a client of one class,
:class:`~noust.managers.database.backups.DatabaseBackups`, the same one
``noust db backup-*`` calls:

- ``/backup-policies``: one policy per database (schedule, retention,
  destinations, format, restore test). Setting or removing one is a root timer
  and decides which dumps are thrown away, so it needs sudo mode, like an
  application's schedule.
- ``/backups/{name}/verify``, ``/push``: jobs, with the tools' own output.
- ``/backups/{name}/download``: the dump itself, streamed, with sudo mode. A
  dump is the whole database, so reading one is audited as a sensitive read.
- ``/backups/remote``: what a destination holds, for restoring on a server that
  never made the dump; ``/backups/restore-remote`` downloads, verifies and
  restores it, as a job, with sudo mode.

Literal paths (``remote``, ``suggest-name``, ``restore-remote``) never share a
prefix with a parametrised one for the same method.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.exceptions import BackupError, DatabaseBackupError, ValidationError
from noust.managers.backup_scheduler import SCHEDULE_ALIASES, validate_calendar
from noust.managers.database.backup_records import STATUS_FAILED
from noust.managers.database.backups import DatabaseBackups, PolicyView
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import ActionResponse, queue, service, session_actor
from noust.web.api.databases.jobs import _done, _service
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType
from noust.web.pydantic_compat import dump_model, field_validator, iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)

#: The alias each expansion came from, so a policy says "daily" and not
#: ``*-*-* 02:00:00``.
_ALIAS_BY_CALENDAR = {calendar: alias for alias, calendar in SCHEDULE_ALIASES.items()}


# ------------------------------------------------------------------- models


class PolicyDestination(BaseModel):
    """
    One remote destination a policy sends its dumps to.

    Attributes:
        name: A destination created under ``/api/backup-destinations``, which
            keeps its own encryption.
        retention_count: This server's scheduled dumps of the database to keep
            there; null for no limit.
        retention_days: Maximum age in days of those; null for no limit.
    """

    name: str
    retention_count: int | None = Field(default=None, ge=1, le=365)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class PolicyDestinationState(PolicyDestination):
    """
    A destination a policy names, and what it is now.

    Attributes:
        exists: Whether the destination is still configured. False means it was
            removed by hand after the policy was saved and every upload fails
            until the policy names another.
        encrypted: Whether it wraps what it stores in rclone's crypt.
    """

    exists: bool = True
    encrypted: bool = False


class PolicyTimer(BaseModel):
    """
    What systemd says of a policy's timer.

    Attributes:
        installed: The unit is loaded. False for an enabled policy means the
            timer was removed by hand: saving the policy again recreates it.
        next_run: When it fires next, as systemd prints it.
        last_run: When it last fired.
    """

    installed: bool = False
    next_run: str | None = None
    last_run: str | None = None


class BackupPolicyResponse(BaseModel):
    """
    A database's backup policy and how it is going.

    Attributes:
        configured: Whether the database has a policy. When it does not, every
            other field is empty and ``timer`` says nothing.
        schedule: The calendar expression.
        schedule_alias: ``hourly``, ``daily``, ``weekly``, ``monthly`` or
            ``custom``.
        retention_count: Scheduled dumps kept locally; null for no limit.
        retention_days: Days a scheduled dump is kept locally; null for none.
        destinations: Where each dump is sent.
        dump_format: PostgreSQL's format; null for the engine's default.
        verify_restore: Each dump is loaded into a temporary database.
        enabled: Whether the timer exists.
        last_run_at: When the policy last ran.
        last_status: ``ok`` or ``failed``; null before the first run.
        last_error: What the last failed run said, verbatim.
        last_dump: The dump the last run took.
        last_success_at: When a run last went everywhere it was meant to.
    """

    engine: str
    database: str
    configured: bool
    schedule: str | None = None
    schedule_alias: str | None = None
    retention_count: int | None = None
    retention_days: int | None = None
    destinations: list[PolicyDestinationState] = Field(default_factory=list)
    dump_format: str | None = None
    verify_restore: bool = False
    enabled: bool = False
    timer: PolicyTimer = Field(default_factory=PolicyTimer)
    last_run_at: str | None = None
    last_status: str | None = None
    last_error: str | None = None
    last_dump: str | None = None
    last_success_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    _iso_timestamps = iso_offset_validator(
        "last_run_at", "last_success_at", "created_at", "updated_at"
    )


class UnprotectedDatabase(BaseModel):
    """A database no enabled policy covers."""

    engine: str
    database: str


class BackupPolicyListResponse(BaseModel):
    """
    Every policy, and the databases without one.

    Attributes:
        unprotected: Databases of running engines with no enabled policy,
            which is what the databases page warns about. A Redis instance is
            one entry.
    """

    policies: list[BackupPolicyResponse]
    total: int
    unprotected: list[UnprotectedDatabase] = Field(default_factory=list)


class SetPolicyRequest(BaseModel):
    """
    Request to create or replace a database's backup policy.

    Attributes:
        schedule: ``hourly``, ``daily``, ``weekly``, ``monthly`` or a systemd
            ``OnCalendar`` expression.
        retention_count: Scheduled dumps to keep locally. Dumps taken by hand
            and the safety copies of restores are never deleted by retention.
        retention_days: Days to keep a scheduled dump locally.
        destinations: Remote copies, each with its own retention.
        dump_format: PostgreSQL's ``custom`` (default), ``plain`` or ``tar``.
        verify_restore: Load each dump into a temporary database and drop it
            as proof it restores. Not available for Redis.
        enabled: Whether the timer runs; a disabled policy keeps its settings.
    """

    schedule: str = Field(
        default="daily",
        description="hourly, daily, weekly, monthly or a systemd OnCalendar expression",
    )
    retention_count: int | None = Field(default=7, ge=1, le=365)
    retention_days: int | None = Field(default=30, ge=1, le=3650)
    destinations: list[PolicyDestination] = Field(default_factory=list)
    dump_format: str | None = None
    verify_restore: bool = False
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
            ValueError: When the scheduler would not write this into a unit
                file. FastAPI answers it as a 422 with the message as detail.
        """
        try:
            validate_calendar(value)
        except BackupError as exc:
            raise ValueError(f"{exc}. {exc.details}".strip()) from exc
        return value


class VerifyRequest(BaseModel):
    """
    Request to check a dump again.

    Attributes:
        engine: The engine the dump belongs to.
        restore_test: Also load it into a temporary database, dropped
            afterwards. Not available for Redis.
    """

    engine: str
    restore_test: bool = False


class PushRequest(BaseModel):
    """
    Request to send a dump to a destination.

    Attributes:
        engine: The engine the dump belongs to.
        destination: A destination created under ``/api/backup-destinations``.
    """

    engine: str
    destination: str


class RestoreRemoteRequest(BaseModel):
    """
    Request to restore a dump that lives on a destination.

    Attributes:
        engine: The engine.
        database: The database the dump is of, which names its folder on the
            destination, and the target unless ``new_name`` is given.
        destination: The destination it is on.
        backup_name: The dump's file name there.
        drop_existing: Drop and recreate the target before loading. A safety
            copy is taken first whatever ``safety_backup`` says.
        safety_backup: Dump the target before loading over it.
        new_name: Restore into a new database of this name instead, leaving
            the original untouched.
    """

    engine: str
    database: str
    destination: str
    backup_name: str
    drop_existing: bool = False
    safety_backup: bool = True
    new_name: str | None = None


class RemoteDumpResponse(BaseModel):
    """
    A dump on a destination.

    Attributes:
        name: The file name, which a remote restore takes.
        own: This server sent it; retention only ever deletes these.
        scheduled: A policy sent it, so retention may delete it.
        sha256: The digest recorded when it was sent; null when there is no
            sidecar (something else put the file there).
        local: The file is also on this server.
    """

    name: str
    size: int | None = None
    size_human: str | None = None
    modified: str | None = None
    own: bool = False
    scheduled: bool = False
    sha256: str | None = None
    format: str = "unknown"
    created: str | None = None
    database: str
    local: bool = False
    destination: str


class RemoteDumpListResponse(BaseModel):
    """The dumps a destination holds for one database."""

    destination: str
    engine: str
    database: str
    dumps: list[RemoteDumpResponse]
    total: int


class RemoteDatabasesResponse(BaseModel):
    """
    The databases a destination holds dumps of, for one engine.

    Attributes:
        databases: The folder names; ``instance`` for Redis.
    """

    destination: str
    engine: str
    databases: list[str]


class SuggestedNameResponse(BaseModel):
    """A free name for restoring as a new database."""

    name: str


# ----------------------------------------------------------------- helpers


def _policy_response(view: PolicyView) -> BackupPolicyResponse:
    """
    Turn a policy view into the response model.

    Args:
        view: The policy and its timer.

    Returns:
        The response.
    """
    data = view.to_dict()
    schedule = data.get("schedule")
    data["schedule_alias"] = (
        _ALIAS_BY_CALENDAR.get(schedule, "custom") if isinstance(schedule, str) else None
    )
    return BackupPolicyResponse(**data)


def _backups(session: dict[str, Any]) -> DatabaseBackups:
    """
    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The backups class over the process-wide store and registry.
    """
    return DatabaseBackups(service(session))


# ---------------------------------------------------------------- policies


@router.get("/backup-policies", response_model=BackupPolicyListResponse)
def list_policies(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[str | None, Query(description="Restrict to one engine")] = None,
) -> BackupPolicyListResponse:
    """
    List every backup policy, and the databases that have none.

    Args:
        engine: Engine to restrict the listing to.
        session: The authenticated session.

    Returns:
        The policies with their timers' state, and the unprotected databases.
    """
    backups = _backups(session)
    views = backups.list_policies(engine)
    return BackupPolicyListResponse(
        policies=[_policy_response(view) for view in views],
        total=len(views),
        unprotected=[UnprotectedDatabase(**entry) for entry in backups.unprotected(engine)],
    )


@router.get("/backup-policies/{engine}/{database}", response_model=BackupPolicyResponse)
def get_policy(
    engine: str, database: str, session: Annotated[dict, Depends(get_current_session)]
) -> BackupPolicyResponse:
    """
    Read one database's backup policy.

    Args:
        engine: The engine.
        database: The database.
        session: The authenticated session.

    Returns:
        The policy; ``configured`` is false when the database has none.
    """
    return _policy_response(_backups(session).get_policy(engine, database))


@router.put("/backup-policies/{engine}/{database}", response_model=BackupPolicyResponse)
def set_policy(
    engine: str,
    database: str,
    request: SetPolicyRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> BackupPolicyResponse:
    """
    Create or replace a database's backup policy, and its timer.

    Sudo mode: the policy is a root timer, and its retention decides which
    dumps are thrown away.

    Args:
        engine: The engine.
        database: The database, which must exist.
        request: The policy.
        session: The authenticated, elevated session.

    Returns:
        The policy as stored.

    Raises:
        DatabaseNotFoundError: 404 when the database does not exist.
        ValidationError: 400 for an unknown destination or an unusable value.
    """
    view = _backups(session).set_policy(
        engine,
        database,
        schedule=request.schedule,
        retention_count=request.retention_count,
        retention_days=request.retention_days,
        destinations=[dump_model(entry) for entry in request.destinations],
        dump_format=request.dump_format,
        verify_restore=request.verify_restore,
        enabled=request.enabled,
    )
    return _policy_response(view)


@router.delete("/backup-policies/{engine}/{database}", response_model=ActionResponse)
def remove_policy(
    engine: str, database: str, session: Annotated[dict, Depends(require_elevated)]
) -> ActionResponse:
    """
    Remove a database's backup policy and its timer. Its dumps stay.

    Args:
        engine: The engine.
        database: The database.
        session: The authenticated, elevated session.

    Returns:
        The outcome; ``success`` is false when there was no policy.
    """
    removed = _backups(session).remove_policy(engine, database)
    return ActionResponse(
        success=removed,
        message=f"Backup policy removed for {database}" if removed else "There was no policy",
    )


def policy_run_job(
    engine: str,
    database: str,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Run a database's policy: dump, check, send, prune.

    Args:
        engine: The engine.
        database: The database.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What :meth:`~noust.managers.database.backups.DatabaseBackups.run_policy`
        reports.
    """
    steps = {"count": 0}

    def progress(text: str) -> None:
        steps["count"] += 1
        if job_context is not None:
            job_context.update(text, min(90, 10 + 20 * steps["count"]))

    backups = DatabaseBackups(_service(job_context, f"Running the policy of {database}", actor))
    # The job manager announces a failed job itself; announcing here too would say it twice.
    result = backups.run_policy(engine, database, notify=False, on_step=progress)
    _done(job_context)
    return result


@router.post(
    "/backup-policies/{engine}/{database}/run", response_model=JobAcceptedResponse, status_code=202
)
def run_policy(
    engine: str, database: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue a run of a database's policy: a dump, its check, and the sends.

    Unlike ``POST /backups``, which only takes a dump, this does everything
    the timer does, retention included. A database with no policy still gets a
    dump, checked, with nothing sent.

    Args:
        engine: The engine.
        database: The database.
        session: The authenticated session.

    Returns:
        The queued job; its result names the dump and what each destination said.
    """
    manager = service(session).running(engine)
    name = manager.validate_database_name(database)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Back up {name}",
        description=f"Running the backup policy of the {manager.DISPLAY_NAME} database {name}",
        func=policy_run_job,
        kwargs={"engine": manager.ENGINE_NAME, "database": name},
        metadata={"engine": manager.ENGINE_NAME, "database": name, "kind": "database"},
        message=f"Backing up {name}",
    )


# ------------------------------------------------------------ verify, push


def verify_job(
    engine: str,
    backup_name: str,
    restore_test: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Check a dump, and optionally test-restore it.

    A dump that fails is a failed job, carrying the check's own words, and the
    evidence is kept with the dump either way.

    Args:
        engine: The engine.
        backup_name: The dump's file name.
        restore_test: Also load it into a temporary database.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The dump as the backups listing describes it.

    Raises:
        DatabaseBackupError: When the dump fails its check or its test.
    """
    backups = DatabaseBackups(_service(job_context, f"Checking {backup_name}", actor))
    view = backups.verify(engine, backup_name, restore=restore_test)
    record = view.record
    if record is not None and record.verify_status == STATUS_FAILED:
        raise DatabaseBackupError(
            f"{backup_name} failed its check", details=record.verify_detail or ""
        )
    if record is not None and record.restore_test_status == STATUS_FAILED:
        raise DatabaseBackupError(
            f"{backup_name} did not restore into a temporary database",
            details=record.restore_test_detail or "",
        )
    _done(job_context)
    return view.to_dict()


@router.post("/backups/{name}/verify", response_model=JobAcceptedResponse, status_code=202)
def verify_backup(
    name: str, request: VerifyRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue checking a dump: size, digest, and the engine's own reading of it.

    Args:
        name: The dump's file name.
        request: The engine, and whether to also test-restore it.
        session: The authenticated session.

    Returns:
        The queued job; its result is the dump with its evidence.

    Raises:
        DatabaseNotFoundError: 404 when the dump does not exist.
    """
    databases = service(session)
    manager = databases.manager(request.engine)
    path = databases.dump_path(manager.ENGINE_NAME, name)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Check {path.name}",
        description=f"Checking the {manager.DISPLAY_NAME} dump {path.name}"
        + (" by restoring it into a temporary database" if request.restore_test else ""),
        func=verify_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "backup_name": path.name,
            "restore_test": request.restore_test,
        },
        metadata={"engine": manager.ENGINE_NAME, "backup_id": path.name, "kind": "database"},
        message=f"Checking {path.name}",
    )


def push_job(
    engine: str,
    backup_name: str,
    destination: str,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Send a dump to a destination.

    Args:
        engine: The engine.
        backup_name: The dump's file name.
        destination: The destination.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What the destination manager reports.
    """
    backups = DatabaseBackups(_service(job_context, f"Sending {backup_name}", actor))
    summary = backups.push(engine, backup_name, destination)
    _done(job_context)
    return summary


@router.post("/backups/{name}/push", response_model=JobAcceptedResponse, status_code=202)
def push_backup(
    name: str, request: PushRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue sending a dump to a destination, after checking it.

    A dump that fails its check is not sent. The destination keeps its own
    encryption.

    Args:
        name: The dump's file name.
        request: The engine and the destination.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        DatabaseNotFoundError: 404 when the dump does not exist.
        ValidationError: 400 when the destination does not exist.
    """
    databases = service(session)
    manager = databases.manager(request.engine)
    path = databases.dump_path(manager.ENGINE_NAME, name)
    return queue(
        session,
        job_type=JobType.PUSH,
        name=f"Send {path.name}",
        description=f"Sending the {manager.DISPLAY_NAME} dump {path.name} to {request.destination}",
        func=push_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "backup_name": path.name,
            "destination": request.destination,
        },
        metadata={
            "engine": manager.ENGINE_NAME,
            "backup_id": path.name,
            "destination": request.destination,
            "kind": "database",
        },
        message=f"Sending {path.name} to {request.destination}",
    )


# --------------------------------------------------- download, delete, names


@router.get("/backups/remote", response_model=RemoteDumpListResponse)
def list_remote_backups(
    engine: Annotated[str, Query(description="The engine")],
    database: Annotated[str, Query(description="The database")],
    destination: Annotated[str, Query(description="A backup destination")],
    session: Annotated[dict, Depends(get_current_session)],
) -> RemoteDumpListResponse:
    """
    List a database's dumps on a destination, newest first.

    A live read of the destination (one rclone call, or two), so the answer is
    what is there now, whoever put it there.

    Args:
        engine: The engine.
        database: The database.
        destination: The destination.
        session: The authenticated session.

    Returns:
        The dumps, each with whether this server sent it and whether it is
        also here.

    Raises:
        BackupError: When the destination cannot be listed.
    """
    manager = service(session).manager(engine)
    dumps = _backups(session).remote_dumps(manager.ENGINE_NAME, database, destination)
    return RemoteDumpListResponse(
        destination=destination,
        engine=manager.ENGINE_NAME,
        database=database,
        dumps=[RemoteDumpResponse(**dump) for dump in dumps],
        total=len(dumps),
    )


@router.get("/backups/remote/databases", response_model=RemoteDatabasesResponse)
def list_remote_databases(
    engine: Annotated[str, Query(description="The engine")],
    destination: Annotated[str, Query(description="A backup destination")],
    session: Annotated[dict, Depends(get_current_session)],
) -> RemoteDatabasesResponse:
    """
    List the databases a destination holds dumps of, for one engine.

    What a new server browses to restore a database it never dumped.

    Args:
        engine: The engine.
        destination: The destination.
        session: The authenticated session.

    Returns:
        The database names, sorted.

    Raises:
        BackupError: When the destination cannot be listed.
    """
    manager = service(session).manager(engine)
    return RemoteDatabasesResponse(
        destination=destination,
        engine=manager.ENGINE_NAME,
        databases=_backups(session).remote_databases(manager.ENGINE_NAME, destination),
    )


@router.get("/backups/suggest-name", response_model=SuggestedNameResponse)
def suggest_restore_name(
    engine: Annotated[str, Query(description="The engine")],
    database: Annotated[str, Query(description="The database the dump is of")],
    session: Annotated[dict, Depends(get_current_session)],
    backup_name: Annotated[str | None, Query(description="The dump, which dates the name")] = None,
) -> SuggestedNameResponse:
    """
    Suggest a free name for restoring a dump as a new database.

    Args:
        engine: The engine.
        database: The database the dump is of.
        backup_name: The dump, whose timestamp dates the name.
        session: The authenticated session.

    Returns:
        A name nothing uses yet, such as ``shop_restored_20260928``.
    """
    return SuggestedNameResponse(name=_backups(session).suggest_name(engine, database, backup_name))


class DumpDownload(FileResponse):
    """A dump as a file: bytes the client saves, never JSON."""

    media_type = "application/octet-stream"


@router.get(
    "/backups/{name}/download",
    response_class=DumpDownload,
    responses={
        200: {
            "description": "The dump, as an attachment.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def download_backup(
    name: str,
    engine: Annotated[str, Query(description="The engine")],
    session: Annotated[dict, Depends(require_elevated)],
) -> DumpDownload:
    """
    Download a dump, streamed from disk, with sudo mode.

    A dump is the whole database: reading one is recorded as a sensitive read,
    with the file's name and size. Only a dump in the engine's own backup
    directory can be named; a path is never accepted.

    Args:
        name: The dump's file name.
        engine: The engine.
        session: The authenticated, elevated session.

    Returns:
        The file, as an attachment.

    Raises:
        DatabaseNotFoundError: 404 when the dump does not exist.
    """
    databases = service(session)
    manager = databases.manager(engine)
    path = databases.dump_path(manager.ENGINE_NAME, name)
    audit.record(
        "db.backup.download",
        actor=session_actor(session),
        target=f"db:{manager.ENGINE_NAME}",
        details={"file": path.name, "size": path.stat().st_size},
    )
    return DumpDownload(path, filename=path.name)


@router.delete("/backups/{name}", response_model=ActionResponse)
def delete_backup(
    name: str,
    engine: Annotated[str, Query(description="The engine")],
    session: Annotated[dict, Depends(require_elevated)],
    destination: Annotated[
        str | None, Query(description="Delete the copy on this destination, not the local file")
    ] = None,
    database: Annotated[
        str | None, Query(description="The database the dump is of; needed with destination")
    ] = None,
) -> ActionResponse:
    """
    Delete a dump from this server, or its copy from a destination.

    Sudo mode. With ``destination`` only that copy goes (its folder is the
    database's, so ``database`` names it); without, only the local file goes
    and copies on destinations stay.

    Args:
        name: The dump's file name.
        engine: The engine.
        destination: The destination to delete from, when it is the copy.
        database: The database, when deleting a copy.
        session: The authenticated, elevated session.

    Returns:
        The outcome.

    Raises:
        DatabaseNotFoundError: 404 when the local dump does not exist.
        ValidationError: 400 when a remote delete names no database.
    """
    backups = _backups(session)
    if destination is not None:
        if not database:
            raise ValidationError(
                "A database is needed to delete a copy from a destination",
                details="Name the database the dump is of: its folder there is the database's.",
                field="database",
            )
        backups.delete_remote(engine, database, name, destination)
        return ActionResponse(success=True, message=f"{name} deleted from {destination}")
    backups.delete_dump(engine, name)
    return ActionResponse(success=True, message=f"{name} deleted")


# ------------------------------------------------------------ remote restore


def restore_remote_job(
    engine: str,
    database: str,
    destination: str,
    backup_name: str,
    drop_existing: bool = False,
    safety_backup: bool = True,
    new_name: str | None = None,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Download a dump from a destination, verify it and restore it.

    Args:
        engine: The engine.
        database: The database the dump is of.
        destination: The destination.
        backup_name: The dump's file name there.
        drop_existing: Drop and recreate the target first.
        safety_backup: Dump the target first when nothing is dropped.
        new_name: Restore into a new database of this name instead.
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        The target, the dump and the safety copy's file name.
    """
    backups = DatabaseBackups(
        _service(job_context, f"Restoring {new_name or database} from {destination}", actor)
    )
    outcome = backups.restore_remote(
        engine,
        database,
        destination,
        backup_name,
        drop_existing=drop_existing,
        safety_backup=safety_backup,
        new_name=new_name,
    )
    _done(job_context)
    return {
        "engine": engine,
        "database": outcome.database,
        "source": backup_name,
        "destination": destination,
        "safety_copy": outcome.safety_copy.name if outcome.safety_copy else None,
        "replaced": outcome.replaced,
    }


@router.post("/backups/restore-remote", response_model=JobAcceptedResponse, status_code=202)
def restore_remote_backup(
    request: RestoreRemoteRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue restoring a dump from a destination, with sudo mode.

    The dump is downloaded, checked against the digest recorded when it was
    sent and verified before anything is touched; replacing a database takes a
    safety copy first and puts it back when the restore fails.

    Args:
        request: The restore request.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    manager = service(session).running(request.engine)
    database = manager.validate_database_name(request.database)
    target = request.new_name or database
    return queue(
        session,
        job_type=JobType.RESTORE,
        name=f"Restore {target}",
        description=(
            f"Restoring the {manager.DISPLAY_NAME} database {target} from "
            f"{request.backup_name} on {request.destination}"
        ),
        func=restore_remote_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "database": database,
            "destination": request.destination,
            "backup_name": request.backup_name,
            "drop_existing": request.drop_existing,
            "safety_backup": request.safety_backup,
            "new_name": request.new_name,
        },
        metadata={
            "engine": manager.ENGINE_NAME,
            "database": target,
            "backup_id": request.backup_name,
            "destination": request.destination,
            "kind": "database",
        },
        message=f"Restoring {target} from {request.destination}",
    )
