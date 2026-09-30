# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/backups``: dumps on disk, taking one, restoring one.

Taking and restoring a dump are jobs, never a request: a central's proxy cuts
a request at 300 seconds, and a dump that outlived it used to "fail" in the
console while it kept running on the node.

A restore never destroys without a safety copy: the target is dumped first,
and put back when the restore fails. It can also go into a new database
beside the original (``new_name``), which touches nothing that exists.

Policies, verification, pushes, downloads and deletions of dumps build on
this in a ``backups`` module of this package, added to its ``AREAS``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.managers.database.backups import DatabaseBackups
from noust.web.api.auth import get_current_session
from noust.web.api.databases import jobs
from noust.web.api.databases.common import queue, service
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)


class BackupCopy(BaseModel):
    """
    A copy of a dump on a destination.

    Attributes:
        destination: The destination's name.
        folder: Where in the destination it is.
        pushed_at: When it was sent.
        verified_by: What the upload was checked by: a hash name, or ``size``
            when the destination reports no hash (an encrypted one never does).
    """

    destination: str
    folder: str | None = None
    pushed_at: str | None = None
    verified_by: str | None = None

    _iso_timestamps = iso_offset_validator("pushed_at")


class BackupInfoResponse(BaseModel):
    """
    One database dump.

    Attributes:
        name: The file name, which restore and the other dump actions take.
        format: ``custom`` (pg_dump -Fc), ``plain`` (SQL), ``tar``, ``rdb``,
            ``aof`` or ``archive`` (a mongodump tarball).
        kind: Who made it: ``manual``, ``scheduled`` (a policy: the only kind
            retention deletes), ``safety`` (the copy a restore took of what it
            overwrote) or ``unknown`` (taken before 3.1).
        sha256: The digest recorded when it was taken.
        verify_status: ``ok`` or ``failed`` for its last check, ``unverified``
            when it has none.
        verify_detail: The check's own words, verbatim.
        restore_test_status: ``ok`` or ``failed`` for its last test restore.
        restore_test_detail: The test's evidence, verbatim.
        destinations: Where a copy was sent, from what Noust recorded (the live
            contents of a destination are ``/backups/remote``).
        age_seconds: How old the file is.
    """

    path: str
    name: str = ""
    database: str
    engine: str
    size: int
    size_human: str
    created: str
    compressed: bool
    format: str = "unknown"
    kind: str = "unknown"
    sha256: str | None = None
    verified_at: str | None = None
    verify_status: str = "unverified"
    verify_method: str | None = None
    verify_detail: str | None = None
    restore_tested_at: str | None = None
    restore_test_status: str | None = None
    restore_test_detail: str | None = None
    destinations: list[BackupCopy] = Field(default_factory=list)
    age_seconds: int = 0

    _iso_timestamps = iso_offset_validator("created", "verified_at", "restore_tested_at")


class BackupListResponse(BaseModel):
    """Response for listing database backups."""

    backups: list[BackupInfoResponse]
    total: int


class CreateBackupRequest(BaseModel):
    """
    Request to dump a database.

    Attributes:
        format: PostgreSQL's dump format: ``custom`` (the default since 3.1),
            ``plain`` or ``tar``. Ignored by the other engines.
    """

    database: str = Field(..., description="Database name")
    engine: str = Field(..., description="Database engine")
    compress: bool = Field(default=True, description="Compress the dump")
    format: str | None = Field(default=None, description="PostgreSQL dump format")


class RestoreBackupRequest(BaseModel):
    """
    Request to restore a database.

    Attributes:
        database: The database the dump is of, and the target unless
            ``new_name`` is given.
        backup_name: File name of the dump, which must be one of the engine's
            own backups. A full path is not accepted: it would let the panel
            read any file on the host as the database superuser.
        drop_existing: Drop and recreate the database before loading. A
            safety copy is taken first whatever ``safety_backup`` says.
        safety_backup: Dump the target before loading over it.
        new_name: Restore into a new database of this name instead, leaving
            the original untouched.
    """

    database: str = Field(..., description="Database name")
    engine: str = Field(..., description="Database engine")
    backup_name: str = Field(..., description="File name of the dump to restore")
    drop_existing: bool = Field(default=False, description="Drop the database first")
    safety_backup: bool = Field(default=True, description="Dump the database first")
    new_name: str | None = Field(default=None, description="Restore into a new database")


@router.get("/backups", response_model=BackupListResponse)
def list_backups(
    session: Annotated[dict, Depends(get_current_session)],
    engine: Annotated[str | None, Query(description="Restrict to one engine")] = None,
    database: Annotated[str | None, Query(description="Restrict to one database")] = None,
) -> BackupListResponse:
    """
    List database dumps.

    Args:
        engine: Engine to restrict the listing to.
        database: Database to restrict the listing to.
        session: The authenticated session.

    Returns:
        The dumps found on disk.
    """
    dumps = DatabaseBackups(service(session)).list_dumps(engine, database)
    return BackupListResponse(
        backups=[BackupInfoResponse(**dump.to_dict()) for dump in dumps], total=len(dumps)
    )


@router.post("/backups", response_model=JobAcceptedResponse, status_code=202)
def create_backup(
    request: CreateBackupRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobAcceptedResponse:
    """
    Queue a dump of a database.

    Args:
        request: The backup request.
        session: The authenticated session.

    Returns:
        The queued job; its result is the dump, as the listing describes it.
    """
    manager = service(session).running(request.engine)
    database = manager.validate_database_name(request.database)
    return queue(
        session,
        job_type=JobType.BACKUP,
        name=f"Dump {database}",
        description=f"Dumping the {manager.DISPLAY_NAME} database {database}",
        func=jobs.dump_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "database": database,
            "compress": request.compress,
            "dump_format": request.format,
        },
        metadata={"engine": manager.ENGINE_NAME, "database": database, "kind": "database"},
        message=f"Dumping {database}",
    )


@router.post("/backups/restore", response_model=JobAcceptedResponse, status_code=202)
def restore_backup(
    request: RestoreBackupRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue restoring one of the engine's own dumps, with sudo mode.

    The dump is named, not pathed, and must exist before the job is queued.

    Args:
        request: The restore request.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        DatabaseBackupError: 404 when the named dump does not exist.
    """
    databases = service(session)
    manager = databases.running(request.engine)
    database = manager.validate_database_name(request.database)
    source = databases.dump_path(manager.ENGINE_NAME, request.backup_name)
    target = request.new_name or database
    return queue(
        session,
        job_type=JobType.RESTORE,
        name=f"Restore {target}",
        description=f"Restoring the {manager.DISPLAY_NAME} database {target} from {source.name}",
        func=jobs.restore_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "database": database,
            "backup_name": source.name,
            "drop_existing": request.drop_existing,
            "safety_backup": request.safety_backup,
            "new_name": request.new_name,
        },
        metadata={
            "engine": manager.ENGINE_NAME,
            "database": target,
            "backup_id": source.name,
            "kind": "database",
        },
        message=f"Restoring {target} from {source.name}",
    )
