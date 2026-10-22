# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/uploads``: send the code of an application as an archive.

A client of one class, :class:`~noust.managers.source_uploads.SourceUploads`, which
``noust upload add`` calls too. An upload is a session the client fills in chunks and can
resume from the offset the server reports (the protocol of the database dumps, on the same
store), and finishing it extracts the archive once, with every check an archive gets, into a
directory whose address is the ``upload:<id>`` the response carries. That is what an
application's ``source`` is set to, at creation (``POST /api/apps``) or afterwards
(``PUT /api/apps/{domain}/source``).

A chunk is at most :data:`CHUNK_BYTES`, so it fits in the default request limit and nothing
here widens it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.managers.database.uploads import Upload, UploadOffsetError
from noust.managers.source_uploads import DOCKERFILE_MAX_BYTES, SourceUploads, UploadedSource
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)

#: The most a client should put in one chunk: half the default request limit, which leaves room
#: for the headers and for a proxy that adds some.
CHUNK_BYTES = 512 * 1024


class UploadStartRequest(BaseModel):
    """Start an upload of an archive."""

    file_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="The archive's name: .zip, .tar, .tar.gz, .tgz, .tar.bz2 or .tar.xz",
    )
    size: int = Field(..., gt=0, description="Its size in bytes")
    sha256: str | None = Field(
        default=None,
        max_length=64,
        description="The digest the finished file must have, as 64 hex digits",
    )


class UploadResponse(BaseModel):
    """An upload in progress."""

    id: str
    file_name: str
    size: int
    received: int = Field(description="Bytes the server holds, which is where to resume")
    sha256: str | None
    complete: bool
    chunk_size: int = Field(description="The most one chunk request may carry")
    created_at: str
    updated_at: str
    source: str | None = Field(
        default=None, description="What an application's source is set to, once finished"
    )
    files: int | None = Field(default=None, description="Files in the extracted project")
    bytes: int | None = Field(default=None, description="Bytes in the extracted project")


class DockerfileRequest(BaseModel):
    """A Dockerfile pasted as text."""

    content: str = Field(
        ...,
        min_length=1,
        max_length=DOCKERFILE_MAX_BYTES,
        description="The Dockerfile's text",
    )
    name: str = Field(
        default="Dockerfile", max_length=40, description="Dockerfile or Containerfile"
    )


def _manager() -> SourceUploads:
    """
    Returns:
        The archives uploaded as code on this server.
    """
    return SourceUploads()


def _session(upload: Upload) -> UploadResponse:
    """
    Args:
        upload: A session.

    Returns:
        The response model.
    """
    return UploadResponse(
        id=upload.id,
        file_name=upload.file_name,
        size=upload.size,
        received=upload.received,
        sha256=upload.sha256,
        complete=upload.complete,
        chunk_size=CHUNK_BYTES,
        created_at=upload.created_at,
        updated_at=upload.updated_at,
    )


def _finished(uploaded: UploadedSource) -> UploadResponse:
    """
    Args:
        uploaded: An extracted upload.

    Returns:
        The response model.
    """
    return UploadResponse(
        id=uploaded.id,
        file_name=uploaded.file_name,
        size=uploaded.size,
        received=uploaded.size,
        sha256=uploaded.sha256,
        complete=True,
        chunk_size=CHUNK_BYTES,
        created_at=uploaded.created_at,
        updated_at=uploaded.created_at,
        source=uploaded.source,
        files=uploaded.files,
        bytes=uploaded.bytes,
    )


def _offset_refusal(exc: UploadOffsetError, error: str) -> JSONResponse:
    """
    Args:
        exc: What went wrong with the offset.
        error: The error code the client reads.

    Returns:
        The 409 that carries where to resume.
    """
    content: dict[str, Any] = {
        "detail": exc.message,
        "hint": exc.details or None,
        "error": error,
        "received": exc.received,
    }
    return JSONResponse(status_code=409, content=content)


@router.post("", response_model=UploadResponse, status_code=201)
def start_upload(
    body: UploadStartRequest, session: Annotated[dict, Depends(get_current_session)]
) -> UploadResponse:
    """
    Start an upload. Nothing is sent yet.

    Args:
        body: The archive's name and size, and its digest if known.
        session: The authenticated session, which owns the upload.

    Returns:
        The upload, with the offset to start at and the most a chunk may carry.

    Raises:
        SourceError: 400 for a name that is not an archive format.
        ValidationError: 400 for a name, size or digest that is not usable.
        UploadTooLargeError: 400 for a size past the limit or the disk.
    """
    upload = _manager().start(
        body.file_name, body.size, owner=actor_label(session), sha256=body.sha256
    )
    return _session(upload)


@router.post("/dockerfile", response_model=UploadResponse, status_code=201)
def paste_dockerfile(
    body: DockerfileRequest, session: Annotated[dict, Depends(get_current_session)]
) -> UploadResponse:
    """
    Make a project of one file out of a Dockerfile that was pasted, and say its source.

    There is no repository and the build context is the Dockerfile alone: a ``COPY`` finds
    nothing, and the Dockerfile fetches what it needs itself. The ``source`` in the answer is
    what an application is set to, like an uploaded archive's.

    Args:
        body: The Dockerfile's text.
        session: The authenticated session, which owns the upload.

    Returns:
        The upload, finished.

    Raises:
        ValidationError: 400 for text that is empty, too large, or has no FROM instruction.
    """
    uploaded = _manager().add_dockerfile(body.content, owner=actor_label(session), name=body.name)
    return _finished(uploaded)


@router.get("/{upload_id}", response_model=UploadResponse)
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
        ValidationError: 400 when there is no such upload, or it is another's.
    """
    return _session(_manager().sessions.get(upload_id, owner=actor_label(session)))


@router.put("/{upload_id}", response_model=UploadResponse)
async def send_chunk(
    upload_id: str,
    request: Request,
    offset: Annotated[int, Query(ge=0, description="Where this chunk starts in the file")],
    session: Annotated[dict, Depends(get_current_session)],
) -> UploadResponse | JSONResponse:
    """
    Add the next chunk of an upload.

    The body is the raw bytes of the chunk. A chunk at an offset other than the one the server
    holds is refused with 409 and the offset to resume from.

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
            f"A chunk may be at most {CHUNK_BYTES} bytes here",
            details="Split the file in smaller pieces.",
        )
    try:
        upload = await run_in_threadpool(
            _manager().sessions.append, upload_id, offset, data, owner=actor_label(session)
        )
    except UploadOffsetError as exc:
        return _offset_refusal(exc, "upload_offset")
    return _session(upload)


@router.post("/{upload_id}/complete", response_model=UploadResponse)
def complete_upload(
    upload_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> UploadResponse | JSONResponse:
    """
    Close an upload: every byte is in, the digest matches, and the archive is extracted.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Returns:
        The finished upload, with the ``source`` an application is set to.

    Raises:
        SourceError: 400 for an archive that is malformed, holds an unsafe member, is past the
            caps on members and bytes, or holds no files (nothing is kept).
        ValidationError: 400 when the digest does not match (the data is discarded) or the
            upload is unknown.
    """
    try:
        uploaded = _manager().finish(upload_id, owner=actor_label(session))
    except UploadOffsetError as exc:
        return _offset_refusal(exc, "upload_incomplete")
    return _finished(uploaded)


@router.delete("/{upload_id}", status_code=204)
def discard_upload(upload_id: str, session: Annotated[dict, Depends(get_current_session)]) -> None:
    """
    Throw an upload away, whether it is still being sent or already extracted.

    An extracted upload an application uses is not removed: change that application's source
    first.

    Args:
        upload_id: The upload.
        session: The authenticated session; only the one that started it.

    Raises:
        ValidationError: 400 when there is no such upload, it is another's, or it is in use.
    """
    manager = _manager()
    owner = actor_label(session)
    uploaded = manager.get(upload_id)
    if uploaded is None:
        manager.sessions.get(upload_id, owner=owner)
        manager.sessions.discard(upload_id)
        return
    if uploaded.owner != owner:
        raise ValidationError("There is no such upload", field="upload_id")
    if uploaded.in_use:
        raise ValidationError(
            "An application deploys from that upload",
            details="Change the application's source first, then remove it.",
            field="upload_id",
        )
    manager.forget(upload_id)
