# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Files and server paths mounted into an application (3.4, checklist B-53).

A client of :mod:`noust.managers.app_mounts`, like ``noust app mount``: ``GET``
lists them without their content, ``GET .../content`` opens a file to edit it
(that reveals what may be a secret: sudo mode and the permission to reveal
secrets), ``PUT`` writes a file or binds a server path, ``DELETE`` removes one.
A file is linked into every release and written to disk, never to a column; a
server path is bound into a container read-only unless asked, and is
root-equivalent, so it needs that permission. What a running application has been
given is not reloaded for it: the pending changes of the application say it needs a
restart.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.managers.app_mounts import MountManager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label
from noust.web.permissions import Permission
from noust.web.permissions.enforce import PermissionDenied, has_permission

router = APIRouter(route_class=NoustErrorRoute)


class MountInfo(BaseModel):
    """A file or a server path mounted into the application."""

    kind: str = Field(description="file, or path for a directory or file of the server")
    target: str = Field(description="Where the application sees it")
    source: str | None = Field(default=None, description="For a path: where it is on the server")
    mode: str = Field(description="For a file: its permissions as octal text")
    read_only: bool
    path: str = Field(description="Where the file or the source is on this server")
    exists: bool
    size: int | None = None
    modified: str | None = None
    updated_by: str | None = None
    updated_at: str | None = None


class MountList(BaseModel):
    """The mounts of an application."""

    domain: str
    mounts: list[MountInfo]
    total: int
    container: bool = Field(
        description="Whether the application runs in a container Noust describes"
    )


class MountContent(BaseModel):
    """The text of a mounted file."""

    domain: str
    target: str
    content: str


class PutMountRequest(BaseModel):
    """A file to keep for the application, or a path of the server to bind."""

    target: str = Field(..., min_length=1, description="Where the application sees it")
    kind: str = Field(default="file", description="file, or path to bind a path of the server")
    content: str | None = Field(default=None, description="For a file: its text")
    mode: int | None = Field(
        default=None, description="For a file: its permissions in octal digits, such as 600"
    )
    source: str | None = Field(default=None, description="For a path: where it is on the server")
    read_only: bool = Field(default=True, description="For a path: mount it read-only")


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/mounts", response_model=MountList)
def list_mounts(domain: str, session: Annotated[dict, Depends(get_current_session)]) -> MountList:
    """
    List the files and server paths mounted into an application. Never their content.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The mounts and whether each is there.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The application takes no mounts (400).
    """
    validated = _known(domain)
    found = [MountInfo(**view.to_dict()) for view in MountManager().list_mounts(validated)]
    app = scoped_store().get_app(validated)
    return MountList(
        domain=validated,
        mounts=found,
        total=len(found),
        container=bool(app is not None and app.app_type == "container"),
    )


@router.get("/{domain}/mounts/content", response_model=MountContent)
def read_mount(
    domain: str,
    target: Annotated[str, Query(min_length=1)],
    session: Annotated[dict, Depends(require_elevated)],
) -> MountContent:
    """
    Open a mounted file to edit it.

    Args:
        domain: Domain of the application.
        target: Where the file appears in the application.
        session: The authenticated, elevated session.

    Returns:
        Its text.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: There is no such file, or it is not text (400).
    """
    validated = _known(domain)
    text = MountManager().read_file(validated, target, actor=actor_label(session))
    return MountContent(domain=validated, target=target, content=text)


@router.put("/{domain}/mounts", response_model=MountInfo)
def put_mount(
    domain: str, body: PutMountRequest, session: Annotated[dict, Depends(require_elevated)]
) -> MountInfo:
    """
    Keep a file for the application, or bind a path of the server into its container.

    Args:
        domain: Domain of the application.
        body: What to mount.
        session: The authenticated, elevated session.

    Returns:
        The mount.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A target, content, mode or path that cannot be mounted (400).
    """
    validated = _known(domain)
    manager = MountManager()
    actor = actor_label(session)
    view: Any
    if body.kind == "file":
        if body.content is None:
            raise ValidationError("A file mount needs its content", field="content")
        mode = int(str(body.mode), 8) if body.mode is not None else 0o600
        view = manager.put_file(validated, body.target, body.content, mode=mode, actor=actor)
    elif body.kind == "path":
        if not has_permission(session, Permission.ROOT_EQUIVALENT):
            # A container that reads or writes a path of the server owns it.
            raise PermissionDenied(
                "permission_denied",
                Permission.ROOT_EQUIVALENT,
                "Binding a path of the server into a container is root-equivalent",
                "Ask an administrator, or keep the files in a volume or a file mount.",
            )
        if body.source is None:
            raise ValidationError("A bound path needs its source on the server", field="source")
        view = manager.put_path(
            validated, body.source, body.target, read_only=body.read_only, actor=actor
        )
    else:
        raise ValidationError("A mount is a file or a path", field="kind")
    return MountInfo(**view.to_dict())


@router.delete("/{domain}/mounts", response_model=MountList)
def delete_mount(
    domain: str,
    target: Annotated[str, Query(min_length=1)],
    session: Annotated[dict, Depends(require_elevated)],
) -> MountList:
    """
    Remove a mount. A file is deleted with it.

    Args:
        domain: Domain of the application.
        target: Where it appears in the application.
        session: The authenticated, elevated session.

    Returns:
        The mounts that remain.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: There is no such mount (400).
    """
    validated = _known(domain)
    manager = MountManager()
    manager.remove(validated, target, actor=actor_label(session))
    found = [MountInfo(**view.to_dict()) for view in manager.list_mounts(validated)]
    app = scoped_store().get_app(validated)
    return MountList(
        domain=validated,
        mounts=found,
        total=len(found),
        container=bool(app is not None and app.app_type == "container"),
    )
