# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The files of an application, from the console (3.4, checklist B-55).

A client of :mod:`noust.managers.app_files`, like ``noust app files``: list a
directory, open a text file, download one, save an edit, upload, create a
directory, move and delete. Everything runs as the application's own account and
never through a link in its tree; environment files are the Environment's. It is
what a shell as the application is, so it asks for the same permission, and
anything that reads or changes a file's content also asks for sudo mode.
"""

from __future__ import annotations

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.managers import app_files
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class FileEntry(BaseModel):
    """One entry of a directory."""

    name: str
    kind: str = Field(description="file, dir, link or other")
    size: int
    modified: str = Field(description="ISO timestamp in UTC")
    mode: str = Field(description="Permissions as octal text, such as 640")
    owner: str = ""
    target: str | None = Field(default=None, description="Where a link points; never followed")
    sensitive: bool = Field(
        default=False, description="An environment file, which the browser does not open"
    )


class FileListing(BaseModel):
    """A directory of the application."""

    domain: str
    path: str = Field(description="Relative to the application; empty for its own directory")
    entries: list[FileEntry]
    truncated: bool = False
    limits: dict[str, int] = Field(
        default_factory=dict, description="The most a file may be to edit and to upload, in bytes"
    )


class FileContent(BaseModel):
    """A text file opened for editing."""

    domain: str
    path: str
    size: int
    content: str


class SaveFileRequest(BaseModel):
    """The text of a file the console edited."""

    path: str = Field(..., min_length=1)
    content: str
    create: bool = Field(default=False, description="The file may be new")


class PathRequest(BaseModel):
    """A path inside the application."""

    path: str = Field(..., min_length=1)


class MoveRequest(BaseModel):
    """Move or rename something inside the application."""

    source: str = Field(..., min_length=1, description="What to move")
    destination: str = Field(..., min_length=1, description="Where it goes")
    overwrite: bool = False


class FileActionResponse(BaseModel):
    """What a change to a file did."""

    domain: str
    path: str
    bytes: int | None = None


def _files(domain: str, session: dict) -> app_files.AppFiles:
    """
    Build the operations for an application the caller may reach.

    Args:
        domain: Domain of the application, as the client sent it.
        session: The authenticated session, whose label goes in the audit record.

    Returns:
        The operations.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: It runs in containers, which have no files of this kind (400).
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return app_files.files_for(validated, actor=actor_label(session))


@router.get("/{domain}/files", response_model=FileListing)
def list_files(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    path: Annotated[str, Query(max_length=app_files.PATH_MAX)] = "",
) -> FileListing:
    """
    List a directory of the application. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.
        path: The directory, relative to the application; empty for its own.

    Returns:
        Its entries, directories first.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: Not a path inside the application, or a file (400).
        SecurityError: A link is on the way (400).
    """
    operations = _files(domain, session)
    listing = operations.list_directory(path)
    return FileListing(
        domain=operations.app.domain,
        path=listing.path,
        entries=[FileEntry(**entry.to_dict()) for entry in listing.entries],
        truncated=listing.truncated,
        limits={"edit": app_files.EDIT_MAX_BYTES, "upload": app_files.UPLOAD_MAX_BYTES},
    )


@router.get("/{domain}/files/content", response_model=FileContent)
def read_file(
    domain: str,
    path: Annotated[str, Query(min_length=1, max_length=app_files.PATH_MAX)],
    session: Annotated[dict, Depends(require_elevated)],
) -> FileContent:
    """
    Open a text file for editing.

    Args:
        domain: Domain of the application.
        path: The file, relative to the application.
        session: The authenticated, elevated session.

    Returns:
        Its text.

    Raises:
        ValidationError: An environment file, a directory, too large or not text (400).
    """
    operations = _files(domain, session)
    opened = operations.read_text(path)
    return FileContent(
        domain=operations.app.domain, path=opened.path, size=opened.size, content=opened.content
    )


class FileDownload(FileResponse):
    """A file as an attachment: bytes the client saves, never JSON."""

    media_type = "application/octet-stream"


@router.get(
    "/{domain}/files/download",
    response_class=FileDownload,
    responses={
        200: {
            "description": "The file, as an attachment.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
def download_file(
    domain: str,
    path: Annotated[str, Query(min_length=1, max_length=app_files.PATH_MAX)],
    session: Annotated[dict, Depends(require_elevated)],
) -> FileDownload:
    """
    Download a file, byte for byte.

    Args:
        domain: Domain of the application.
        path: The file, relative to the application.
        session: The authenticated, elevated session.

    Returns:
        The file as an attachment.

    Raises:
        ValidationError: An environment file or not a file (400).
    """
    operations = _files(domain, session)
    copy = operations.reserve()
    served = False
    try:
        operations.download(path, copy)
        served = True
    finally:
        # A copy that cannot be served is removed before the error leaves.
        if not served:
            app_files.discard(copy)
    name = os.path.basename(path.rstrip("/")) or "download"
    return FileDownload(
        copy,
        filename=name,
        background=BackgroundTask(lambda: app_files.discard(copy)),
    )


@router.put("/{domain}/files/content", response_model=FileActionResponse)
def save_file(
    domain: str, body: SaveFileRequest, session: Annotated[dict, Depends(require_elevated)]
) -> FileActionResponse:
    """
    Save the text of a file.

    The file is written beside itself and renamed over, with the mode it had.

    Args:
        domain: Domain of the application.
        body: The path, the text, and whether the file may be new.
        session: The authenticated, elevated session.

    Returns:
        The bytes written.

    Raises:
        ValidationError: An environment file, a directory, too large, or missing (400).
    """
    operations = _files(domain, session)
    written = operations.save_text(body.path, body.content, create=body.create)
    return FileActionResponse(domain=operations.app.domain, path=body.path, bytes=written)


@router.put("/{domain}/files/upload", response_model=FileActionResponse)
async def upload_file(
    domain: str,
    request: Request,
    path: Annotated[str, Query(min_length=1, max_length=app_files.PATH_MAX)],
    session: Annotated[dict, Depends(require_elevated)],
    overwrite: bool = False,
) -> FileActionResponse:
    """
    Upload a file: the body is its bytes.

    Args:
        domain: Domain of the application.
        request: The request, whose body is the file.
        path: Where it goes, relative to the application, name included.
        session: The authenticated, elevated session.
        overwrite: Replace a file that is there. Off, an existing one is refused.

    Returns:
        The bytes written.

    Raises:
        ValidationError: Too large, an environment file, a directory, or the file
            exists and ``overwrite`` is off (400).
    """
    operations = _files(domain, session)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > app_files.UPLOAD_MAX_BYTES:
        raise ValidationError(
            f"That is {declared} bytes: the console uploads up to "
            f"{app_files.UPLOAD_MAX_BYTES // 1024 // 1024} MiB",
            field="path",
        )
    destination = app_files.clean_path(path, allow_empty=False)
    if not overwrite and os.path.lexists(operations.resolve(destination)):
        raise ValidationError(
            f"{destination} exists already", details="Ask to replace it.", field="path"
        )
    with operations.staged() as staged:
        received = 0
        with open(staged, "wb") as sink:
            async for chunk in request.stream():
                received += len(chunk)
                if received > app_files.UPLOAD_MAX_BYTES:
                    raise ValidationError(
                        f"That is more than the {app_files.UPLOAD_MAX_BYTES // 1024 // 1024} MiB "
                        "the console uploads",
                        field="path",
                    )
                sink.write(chunk)
        written = operations.save_bytes(path, staged, create=True, action="upload")
    return FileActionResponse(domain=operations.app.domain, path=path, bytes=written)


@router.post("/{domain}/files/directory", response_model=FileActionResponse, status_code=201)
def make_directory(
    domain: str, body: PathRequest, session: Annotated[dict, Depends(require_elevated)]
) -> FileActionResponse:
    """
    Create a directory whose parent exists.

    Args:
        domain: Domain of the application.
        body: The directory.
        session: The authenticated, elevated session.

    Returns:
        The directory.

    Raises:
        ValidationError: It exists or its parent does not (400).
    """
    operations = _files(domain, session)
    operations.make_directory(body.path)
    return FileActionResponse(domain=operations.app.domain, path=body.path)


@router.post("/{domain}/files/move", response_model=FileActionResponse)
def move_file(
    domain: str, body: MoveRequest, session: Annotated[dict, Depends(require_elevated)]
) -> FileActionResponse:
    """
    Rename or move a file or directory.

    Args:
        domain: Domain of the application.
        body: What to move, where to, and whether to replace what is there.
        session: The authenticated, elevated session.

    Returns:
        Where it is now.

    Raises:
        ValidationError: Missing, an environment file, or the destination exists (400).
    """
    operations = _files(domain, session)
    operations.move(body.source, body.destination, overwrite=body.overwrite)
    return FileActionResponse(domain=operations.app.domain, path=body.destination)


@router.delete("/{domain}/files", response_model=FileActionResponse)
def delete_file(
    domain: str,
    path: Annotated[str, Query(min_length=1, max_length=app_files.PATH_MAX)],
    session: Annotated[dict, Depends(require_elevated)],
    recursive: bool = False,
) -> FileActionResponse:
    """
    Delete a file, a link, or a directory.

    Args:
        domain: Domain of the application.
        path: What to delete.
        session: The authenticated, elevated session.
        recursive: Delete a directory with what is in it. Off, only an empty one goes.

    Returns:
        What was deleted.

    Raises:
        ValidationError: Missing, an environment file, or a directory that is not empty (400).
    """
    operations = _files(domain, session)
    operations.remove(path, recursive=recursive)
    return FileActionResponse(domain=operations.app.domain, path=path)
