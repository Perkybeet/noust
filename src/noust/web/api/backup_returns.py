# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/backup-collection/returns`` and ``/restore``: a copy the central holds comes back (item 195).

The central keeps the copies of its servers and gives one back, to the server it came from or to
another of the fleet. This is the server's receiving end, a client of
:class:`~noust.managers.backup_return.BackupReturn` (which calls the manager that always restored
that kind of copy, rule 3):

- ``POST /returns`` starts the arrival of a file, with its size and digest; ``PUT /returns/{id}``
  adds a chunk at the offset the server holds (a retried one can never write a chunk twice or leave a
  hole); ``GET`` says where to resume; ``POST .../complete`` closes it when every byte is in and the
  digest matches; ``DELETE`` throws it away. The protocol of every upload here.
- ``POST /restore`` queues restoring what arrived, with sudo mode: a restore replaces data.

A restore asks for ``backups.manage``, like restoring a backup that is already on the server, and the
central's call reaches it only as far as this server's ceiling lets a central go (``admin``).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.managers.backup_catalogue import KINDS
from noust.managers.backup_return import BackupReturn, ReturnRequest
from noust.managers.database.uploads import Upload, UploadOffsetError, UploadProfile, UploadStore
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)

#: What a copy that comes back may end in: an archive, a dump, an encrypted bundle.
RETURNS = UploadProfile(
    extensions=("gz", "dump", "sql", "tar", "archive", "enc", "zip", "bz2", "xz", "rdb", "aof"),
    limit_key="backup.return_max_bytes",
    default_max=256 * 1024**3,
    noun="a copy",
    extension_lead="A copy's name ends in one of: ",
    name_details="Name the copy with a plain file name, without a path.",
    extension_hint="A backup is an archive (.tar.gz), a dump or an encrypted bundle (.enc).",
    limit_details="Raise backup.return_max_bytes with 'noust config set'.",
    event="backup.collection.return",
    target="copy:return",
    directory=".returned-copies",
)

#: The most a chunk of a copy that comes back may carry.
CHUNK_BYTES = 512 * 1024


class ReturnStart(BaseModel):
    """Start the return of a copy."""

    size: int = Field(..., gt=0, description="Its size in bytes")
    sha256: str = Field(..., min_length=64, max_length=64, description="Its digest, 64 hex digits")
    file_name: str = Field(..., max_length=255, description="Its name")


class ReturnOut(BaseModel):
    """A copy on its way back."""

    id: str
    size: int
    received: int = Field(description="Bytes held, which is where to resume")
    sha256: str | None
    complete: bool
    chunk_size: int = Field(description="The most one chunk request may carry")


class ReturnRestore(BaseModel):
    """Restore a copy that came back."""

    upload_id: str = Field(..., description="The finished upload that holds the copy")
    kind: str = Field(..., description="The kind of copy")
    sidecar: dict[str, Any] | None = Field(
        default=None, description="What the copy's sidecar said, when it had one"
    )
    meta: dict[str, str] = Field(
        default_factory=dict, description="The small facts the central recorded of it"
    )
    target: dict[str, Any] = Field(
        default_factory=dict,
        description="Where it goes: `domain`, or `engine`, `database` and `new_name`",
    )
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="`restore_env`, `safety_backup`, `drop_existing`, `schema_changed_ok`",
    )


def _returns() -> UploadStore:
    """
    Returns:
        The copies that are coming back to this server.
    """
    return UploadStore(profile=RETURNS)


def _return(upload: Upload) -> ReturnOut:
    """
    Args:
        upload: An upload session.

    Returns:
        The response model.
    """
    return ReturnOut(
        id=upload.id,
        size=upload.size,
        received=upload.received,
        sha256=upload.sha256,
        complete=upload.complete,
        chunk_size=CHUNK_BYTES,
    )


def _offset_refusal(exc: UploadOffsetError, error: str) -> JSONResponse:
    """
    Args:
        exc: What went wrong with the offset.
        error: The error code the client reads.

    Returns:
        The 409 that carries where to resume.
    """
    return JSONResponse(
        status_code=409,
        content={
            "detail": exc.message,
            "hint": exc.details or None,
            "error": error,
            "received": exc.received,
        },
    )


@router.post("/returns", response_model=ReturnOut, status_code=201)
def start_return(
    body: ReturnStart, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> ReturnOut:
    """
    Start the arrival of a copy the central gives back. Nothing is sent yet.

    Args:
        body: The copy's size, digest and name.
        session: The authenticated session, which owns the upload.

    Returns:
        The upload, with the offset to start at and the most a chunk may carry.

    Raises:
        ValidationError: 400 for a name, size or digest that is not usable, or a disk with no room.
    """
    upload = _returns().create(
        body.file_name, body.size, owner=actor_label(session), sha256=body.sha256
    )
    return _return(upload)


@router.get("/returns/{upload_id}", response_model=ReturnOut)
def return_status(
    upload_id: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> ReturnOut:
    """
    Say how much of a copy the server holds, which is where to resume.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The upload.
    """
    return _return(_returns().get(upload_id, owner=actor_label(session)))


@router.put("/returns/{upload_id}", response_model=ReturnOut)
async def send_return_chunk(
    upload_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0, description="Where this chunk starts in the file")],
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> ReturnOut | JSONResponse:
    """
    Add the next chunk of a copy.

    A chunk at an offset other than the one the server holds is refused with 409 and the offset to
    resume from.

    Args:
        upload_id: The upload.
        request: The request, whose body is the chunk.
        offset: Where the chunk starts.
        session: The authenticated session; only the one that started it.

    Returns:
        The upload, with the new offset.

    Raises:
        ValidationError: 400 for an unknown or finished upload, or a chunk past the size.
    """
    data = await request.body()
    if len(data) > CHUNK_BYTES:
        raise ValidationError(
            f"A chunk may be at most {CHUNK_BYTES} bytes here", details="Split the file."
        )
    try:
        upload = await run_in_threadpool(
            _returns().append, upload_id, offset, data, owner=actor_label(session)
        )
    except UploadOffsetError as exc:
        return _offset_refusal(exc, "upload_offset")
    return _return(upload)


@router.post("/returns/{upload_id}/complete", response_model=ReturnOut)
def complete_return(
    upload_id: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> ReturnOut | JSONResponse:
    """
    Close an upload: every byte is in and the digest matches.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The finished upload.

    Raises:
        ValidationError: 400 when the digest does not match (the data is discarded) or the upload
            is unknown.
    """
    try:
        upload = _returns().complete(upload_id, owner=actor_label(session))
    except UploadOffsetError as exc:
        return _offset_refusal(exc, "upload_incomplete")
    return _return(upload)


@router.delete("/returns/{upload_id}", status_code=204)
def discard_return(
    upload_id: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> None:
    """
    Throw an upload away.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.
    """
    store = _returns()
    store.get(upload_id, owner=actor_label(session))
    store.discard(upload_id)


def return_restore_job(
    upload_id: str,
    kind: str,
    sidecar: dict[str, Any] | None = None,
    meta: dict[str, str] | None = None,
    target: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    owner: str = "",
    job_context: JobContext | None = None,
) -> dict[str, Any]:
    """
    Restore a copy that came back, as a job, and let go of the file whatever happens.

    Args:
        upload_id: The finished upload that holds the copy.
        kind: The kind of copy.
        sidecar: What its sidecar said.
        meta: The small facts the central recorded of it.
        target: Where it goes.
        options: What the operator confirmed.
        owner: Who started the upload.
        job_context: Injected by the job manager.

    Returns:
        What was done.

    Raises:
        NoustError: The manager refused or the restore failed; its own words.
    """
    store = _returns()
    try:
        upload = store.get(upload_id, owner=owner)
        if job_context is not None:
            job_context.update(f"Restoring {upload.file_name}", 20)
        result = BackupReturn().restore(
            ReturnRequest(
                kind=kind,
                path=store.path(upload_id),
                sha256=upload.sha256 or "",
                file_name=upload.file_name,
                sidecar=sidecar,
                meta=meta or {},
                target=target or {},
                options=options or {},
            )
        )
    finally:
        # What came back is every application's data: it does not stay on a disk once it has been
        # tried.
        store.discard(upload_id)
    if job_context is not None:
        job_context.update("Done", 100)
    return result


@router.post("/restore", response_model=JobAcceptedResponse, status_code=202)
def restore_returned_copy(
    body: ReturnRestore, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue restoring a copy that the central gave back.

    A restore replaces what an application, a database or Noust's own state holds, so it asks for
    sudo mode exactly as restoring a backup does, and it goes through the manager of the kind of
    copy it is: its safety copy, its health gate, its refusals. Noust's own state is not restored
    here: it is put where ``noust self-backup restore`` reads it.

    Args:
        body: The upload and what the operator confirms.
        session: The authenticated, elevated session; only the one that started the upload.

    Returns:
        The queued job.

    Raises:
        ValidationError: 400 for an unknown kind or an upload that is not finished.
    """
    if body.kind not in KINDS:
        raise ValidationError(f"Unknown kind of copy: {body.kind!r}", field="kind")
    owner = actor_label(session)
    upload = _returns().get(body.upload_id, owner=owner)
    if not upload.complete:
        raise ValidationError(
            "The copy has not finished arriving",
            details=f"{upload.received} of {upload.size} bytes are in. Send the rest, then "
            "complete the upload.",
            field="upload_id",
        )
    domain = str(body.target.get("domain") or body.meta.get("domain") or "")
    metadata: dict[str, Any] = {"backup_id": body.upload_id, "from_central": True}
    if domain:
        metadata["domain"] = domain
    job = get_job_manager().create_job(
        job_type=JobType.RESTORE,
        name=f"Restore a {body.kind} copy from the central",
        description=f"Restoring {upload.file_name}, which the central gave back",
        func=return_restore_job,
        kwargs={
            "upload_id": body.upload_id,
            "kind": body.kind,
            "sidecar": body.sidecar,
            "meta": body.meta,
            "target": body.target,
            "options": body.options,
            "owner": owner,
        },
        metadata=metadata,
        actor=owner,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Restoring {upload.file_name} queued",
        job=job.to_dict(),
    )
