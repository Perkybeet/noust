# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/imports``: bring a dump from somewhere else into a database.

A client of one class, :class:`~noust.managers.database.imports.DatabaseImports`,
which ``noust db import`` calls too. The API has two ways in:

- **An upload**, in chunks, because a dump is as big as the database it came
  from and a request that carries it whole outlives every proxy. The client
  starts an upload (name and size; the size is capped by
  ``databases.import_max_bytes``, 10 GiB unless raised), sends each chunk with
  the offset the server last reported, asks for the status to resume after a
  dropped connection, and completes it, which checks the size and the digest
  it declared and says what the file is.
- **A path on the server**, held to the ``imports`` folder of the backup
  directory and to the folders ``databases.import_directories`` lists: the API
  must not become a way to point the database superuser at any file on the
  disk. The CLI has no such limit; it is root on the machine.

Loading is a job, with sudo mode, like every restore: the target is dumped
first and put back if the load fails.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.managers.database.imports import DatabaseImports, resolve_server_path
from noust.managers.database.uploads import (
    MAX_CHUNK_BYTES,
    Upload,
    UploadOffsetError,
    UploadStore,
)
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import ActionResponse, queue, service
from noust.web.api.databases.jobs import _done, _service
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)


# ------------------------------------------------------------------- models


class DumpFormatResponse(BaseModel):
    """
    What a file was found to be.

    Attributes:
        kind: ``pg-custom``, ``pg-tar``, ``sql``, ``tar-sql``, ``mongo-archive``,
            ``mongo-tree`` or ``unknown``.
        compression: ``none``, ``gzip``, ``bz2``, ``xz`` or ``zip`` around it.
        description: One sentence to show.
        engines: The engines that can load it.
    """

    kind: str
    compression: str
    description: str
    engines: list[str] = Field(default_factory=list)


class UploadStartRequest(BaseModel):
    """
    Request to start an upload.

    Attributes:
        file_name: The dump's name; it must end in .sql, .gz, .zip, .tar, .bz2,
            .xz, .dump or .archive. Only its extension is read.
        size: Its size in bytes, which the upload may not exceed.
        sha256: The digest the finished file must have, when the client has
            computed one; a mismatch discards the upload.
    """

    file_name: str = Field(..., min_length=1, max_length=255)
    size: int = Field(..., ge=1)
    sha256: str | None = Field(default=None, min_length=64, max_length=64)


class UploadResponse(BaseModel):
    """
    An upload.

    Attributes:
        id: What every later call names it by.
        file_name: The name the client gave.
        size: Bytes the client said the file has.
        received: Bytes the server holds: the offset the next chunk starts at.
        sha256: The digest the client declared, if any.
        created_at: When it was started.
        updated_at: When its last chunk arrived.
        complete: Every byte is in and checked.
        chunk_size: The most one chunk may carry.
        format: What the file is, once it is complete.
    """

    id: str
    file_name: str
    size: int
    received: int
    sha256: str | None = None
    created_at: str
    updated_at: str
    complete: bool = False
    chunk_size: int = MAX_CHUNK_BYTES
    format: DumpFormatResponse | None = None

    _iso_timestamps = iso_offset_validator("created_at", "updated_at")


class SourceRequest(BaseModel):
    """
    Where a dump is: an upload, or a path on the server. Exactly one.

    Attributes:
        upload_id: A finished upload.
        path: An absolute path inside a folder dumps may be imported from.
    """

    upload_id: str | None = None
    path: str | None = None


class InspectRequest(SourceRequest):
    """
    Request to say what a dump is.

    Attributes:
        engine: An engine to judge it against; without one, only what it is.
    """

    engine: str | None = None


class InspectResponse(BaseModel):
    """
    What a dump is, and whether an engine can load it.

    Attributes:
        format: What it is.
        size: Bytes.
        size_human: Bytes, readable.
        loadable: Whether the engine can load it; null without an engine.
        reason: Why not, in a sentence.
    """

    format: DumpFormatResponse
    size: int
    size_human: str
    loadable: bool | None = None
    reason: str | None = None


class ImportRequest(SourceRequest):
    """
    Request to import a dump.

    Attributes:
        engine: The engine.
        database: The database to load into; created when it does not exist.
        replace: Drop and recreate it first. A safety copy is taken before and
            put back when the load fails.
        keep_owners: PostgreSQL: keep the owners and privileges the dump names
            instead of giving everything to the database's owner.
        safety_backup: Dump the database first when it is loaded over rather
            than replaced.
        as_superuser: PostgreSQL: load the dump as the server's superuser
            instead of the database's owner. A dump can run programs on the
            server with that account, so it needs ``confirm_superuser`` too.
        confirm_superuser: The operator accepted what ``as_superuser`` costs.
            Without it the answer is a 400 ``confirmation_required`` carrying
            the warning, and nothing is queued.
    """

    engine: str
    database: str
    replace: bool = False
    keep_owners: bool = False
    safety_backup: bool = True
    as_superuser: bool = False
    confirm_superuser: bool = False


# ----------------------------------------------------------------- helpers


def _source_of(request: SourceRequest) -> None:
    """
    Refuse a request that names no dump, or two.

    Args:
        request: The request.

    Raises:
        ValidationError: When it gives neither an upload nor a path, or both.
    """
    if (request.upload_id is None) == (request.path is None):
        raise ValidationError(
            "Give exactly one of upload_id and path",
            details="The dump is either an upload you finished or a file on the server.",
            field="path",
        )


def _store() -> UploadStore:
    """
    Returns:
        The upload sessions of this server.
    """
    return UploadStore()


def _response(upload: Upload, *, with_format: bool = False) -> UploadResponse:
    """
    Turn a session into the response model.

    Args:
        upload: The session.
        with_format: Also read what the file is (a finished upload).

    Returns:
        The response.
    """
    data: dict[str, Any] = upload.to_dict()
    data.pop("owner", None)
    if with_format and upload.complete:
        from noust.managers.database.imports import detect_format

        data["format"] = detect_format(_store().path(upload.id)).to_dict()
    return UploadResponse(**data)


# --------------------------------------------------------------- the uploads


@router.post("/imports/uploads", response_model=UploadResponse, status_code=201)
def start_upload(
    request: UploadStartRequest, session: Annotated[dict, Depends(get_current_session)]
) -> UploadResponse:
    """
    Start an upload. Nothing is sent yet.

    Args:
        request: The file's name and size, and its digest if known.
        session: The authenticated session, which owns the upload.

    Returns:
        The upload, with the offset to start at and the most a chunk may carry.

    Raises:
        ValidationError: 400 for a name that is not a dump's.
        UploadTooLargeError: 400 for a size past the limit or the disk.
    """
    upload = _store().create(
        request.file_name, request.size, owner=actor_label(session), sha256=request.sha256
    )
    return _response(upload)


@router.post("/imports/inspect", response_model=InspectResponse)
def inspect_dump(
    request: InspectRequest, session: Annotated[dict, Depends(get_current_session)]
) -> InspectResponse:
    """
    Say what a dump is and whether an engine can load it. Changes nothing.

    Args:
        request: An upload or a server path, and optionally an engine.
        session: The authenticated session.

    Returns:
        The format and, with an engine, whether it loads.

    Raises:
        ValidationError: 400 for a path outside the folders dumps are read from.
    """
    _source_of(request)
    source = (
        resolve_server_path(request.path, restrict=True)
        if request.path is not None
        else _store().path(request.upload_id or "")
    )
    answer = DatabaseImports(service(session)).inspect(source, engine=request.engine)
    return InspectResponse(**answer)


@router.get("/imports/uploads/{upload_id}", response_model=UploadResponse)
def upload_status(
    upload_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> UploadResponse:
    """
    Say how much of an upload the server holds, which is where to resume.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The upload.

    Raises:
        ValidationError: 400 when there is no such upload.
    """
    return _response(_store().get(upload_id, owner=actor_label(session)), with_format=True)


@router.put("/imports/uploads/{upload_id}", response_model=UploadResponse)
async def send_chunk(
    upload_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0, description="Where this chunk starts in the file")],
    session: Annotated[dict, Depends(get_current_session)],
) -> UploadResponse | JSONResponse:
    """
    Add the next chunk of an upload.

    The body is the raw bytes of the chunk. A chunk at an offset other than the
    one the server holds is refused with 409 and the offset to resume from.

    Args:
        upload_id: The upload.
        request: The request, whose body is the chunk.
        offset: Where the chunk starts.
        session: The authenticated session; only the one that started it.

    Returns:
        The upload, with the new offset.

    Raises:
        ValidationError: 400 for an unknown or finished upload.
    """
    data = await request.body()
    try:
        upload = await run_in_threadpool(
            _store().append, upload_id, offset, data, owner=actor_label(session)
        )
    except UploadOffsetError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": exc.message,
                "hint": exc.details or None,
                "error": "upload_offset",
                "received": exc.received,
            },
        )
    return _response(upload)


@router.post("/imports/uploads/{upload_id}/complete", response_model=UploadResponse)
def complete_upload(
    upload_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> UploadResponse | JSONResponse:
    """
    Close an upload: every byte is in, the digest matches, and say what it is.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The finished upload, with the format of the file.

    Raises:
        ValidationError: 400 when the digest does not match (the data is
            discarded) or the upload is unknown.
    """
    try:
        upload = _store().complete(upload_id, owner=actor_label(session))
    except UploadOffsetError as exc:
        return JSONResponse(
            status_code=409,
            content={
                "detail": exc.message,
                "hint": exc.details or None,
                "error": "upload_incomplete",
                "received": exc.received,
            },
        )
    return _response(upload, with_format=True)


@router.delete("/imports/uploads/{upload_id}", response_model=ActionResponse)
def discard_upload(
    upload_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> ActionResponse:
    """
    Throw an upload away.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The outcome.

    Raises:
        ValidationError: 400 when there is no such upload.
    """
    _store().get(upload_id, owner=actor_label(session))
    _store().discard(upload_id)
    return ActionResponse(success=True, message="Upload removed")


# ----------------------------------------------------------------- the import


def import_job(
    engine: str,
    database: str,
    upload_id: str | None = None,
    path: str | None = None,
    replace: bool = False,
    keep_owners: bool = False,
    safety_backup: bool = True,
    as_superuser: bool = False,
    actor: str | None = None,
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Import a dump into a database.

    Args:
        engine: The engine.
        database: The database.
        upload_id: A finished upload, removed once it has loaded.
        path: Or a path on the server, left where it is.
        replace: Drop and recreate the database first.
        keep_owners: PostgreSQL: keep the dump's owners and privileges.
        safety_backup: Take the safety copy when nothing is dropped.
        as_superuser: PostgreSQL: load it as the superuser (confirmed when queued).
        actor: Who queued it, as the audit trail names them.
        job_context: Injected by the job manager.

    Returns:
        What :meth:`~noust.managers.database.imports.DatabaseImports.run` did.
    """
    steps = {"count": 0}

    def progress(text: str) -> None:
        steps["count"] += 1
        if job_context is not None:
            job_context.update(text, min(90, 10 + 15 * steps["count"]))

    importer = DatabaseImports(_service(job_context, f"Importing into {database}", actor))
    if upload_id is not None:
        outcome = importer.run_upload(
            engine,
            database,
            upload_id,
            replace=replace,
            keep_owners=keep_owners,
            safety_backup=safety_backup,
            on_step=progress,
            as_superuser=as_superuser,
            confirm_superuser=as_superuser,
        )
    else:
        outcome = importer.run(
            engine,
            database,
            resolve_server_path(path or "", restrict=True),
            replace=replace,
            keep_owners=keep_owners,
            safety_backup=safety_backup,
            on_step=progress,
            as_superuser=as_superuser,
            confirm_superuser=as_superuser,
        )
    _done(job_context)
    return outcome.to_dict()


@router.post("/imports", response_model=JobAcceptedResponse, status_code=202)
def import_dump(
    request: ImportRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue importing a dump, with sudo mode.

    What is checked here is checked before the job exists, so a mistake is an
    answer and not a failed job: the upload is finished (or the path is in an
    allowed folder), the engine is running and the database name is valid.

    Args:
        request: The engine, the database, the dump and the options.
        session: The authenticated, elevated session.

    Returns:
        The queued job; its result says what the file was and what was done.

    Raises:
        ValidationError: 400 for an unfinished upload, a path outside the
            allowed folders or an unusable name.
        ConfirmationRequired: 400 ``confirmation_required`` for
            ``as_superuser`` without ``confirm_superuser``.
        DatabaseEngineError: When the engine is not running.
    """
    _source_of(request)
    databases = service(session)
    manager = databases.running(request.engine)
    name = manager.validate_database_name(request.database)
    if request.as_superuser:
        # Before the job exists: an unconfirmed request is an answer, not a failed job.
        DatabaseImports(databases).backups.superuser_options(
            manager, None, request.confirm_superuser
        )
    if request.upload_id is not None:
        # Finished, and the caller's: the job holds only the identifier.
        _store().get(request.upload_id, owner=actor_label(session))
        _store().path(request.upload_id)
    else:
        resolve_server_path(request.path or "", restrict=True)
    if manager.engine_type == "redis":
        raise ValidationError(
            "Redis has no dump to import here",
            details="Restore a Redis snapshot from the console's backups list.",
            field="engine",
        )
    return queue(
        session,
        job_type=JobType.RESTORE,
        name=f"Import into {name}",
        description=f"Importing a dump into the {manager.DISPLAY_NAME} database {name}",
        func=import_job,
        kwargs={
            "engine": manager.ENGINE_NAME,
            "database": name,
            "upload_id": request.upload_id,
            "path": request.path,
            "replace": request.replace,
            "keep_owners": request.keep_owners,
            "safety_backup": request.safety_backup,
            "as_superuser": request.as_superuser,
        },
        metadata={"engine": manager.ENGINE_NAME, "database": name, "kind": "database"},
        message=f"Importing into {name}",
    )
