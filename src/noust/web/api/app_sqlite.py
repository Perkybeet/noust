# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The SQLite databases an application keeps: list them, check one.

A client of :mod:`noust.managers.sqlite_files`, like ``noust db sqlite``. Both routes read:
the listing looks at the first bytes of each file, and the check opens a database read-only
and asks SQLite whether it is sound. A copy of a database is not offered here: it would put
an application's whole data in a response. ``noust db sqlite copy`` writes one to a file, and
every application backup already holds a consistent one.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.store import App
from noust.core.teams import scoped_store
from noust.managers import sqlite_files
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain

router = APIRouter(route_class=NoustErrorRoute)


class SqliteFileModel(BaseModel):
    """One SQLite database inside an application's directory."""

    path: str = Field(description="Relative to the application's directory, with '/' separators")
    size: int = Field(description="Bytes in the file itself")
    wal_bytes: int = Field(description="Bytes committed and still waiting in its write-ahead log")
    modified: float = Field(description="When the file last changed, seconds since the epoch")
    journal_mode: str = Field(description="wal, or rollback (the rollback journal)")
    page_size: int = Field(description="Bytes in a page; 0 when the header does not say")
    pages: int = Field(description="Pages in the file")


class SqliteListResponse(BaseModel):
    """The SQLite databases an application keeps."""

    domain: str
    files: list[SqliteFileModel]
    searched: list[str] = Field(description="Where Noust looked, relative to the application")
    truncated: bool = Field(description="The search stopped at its limit: there may be more")


class SqliteCheckRequest(BaseModel):
    """Which database to check, and how thoroughly."""

    path: str = Field(
        min_length=1,
        max_length=1024,
        description="As the listing gives it, for example data/app.db",
    )
    full: bool = Field(
        default=False,
        description="integrity_check, which also checks every index against its table; slower",
    )


class SqliteCheckResponse(BaseModel):
    """What SQLite said about one database."""

    path: str
    ok: bool = Field(description="SQLite found nothing wrong")
    complete: bool = Field(description="False when the time allowed ran out first")
    full: bool
    problems: list[str] = Field(description="What SQLite reported, at most 20")
    seconds: float


def _known(domain: str) -> App:
    """
    Validate a domain and find what is deployed there.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The application's row, as the caller's teams see it.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    found = scoped_store().get_app(validated)
    if found is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return found


@router.get("/{domain}/sqlite", response_model=SqliteListResponse)
def list_app_sqlite(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SqliteListResponse:
    """
    List the SQLite databases an application keeps. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The databases found in its data directories.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    listing = sqlite_files.find(_known(domain))
    return SqliteListResponse(**listing.to_dict())


@router.post("/{domain}/sqlite/check", response_model=SqliteCheckResponse)
def check_app_sqlite(
    domain: str,
    body: SqliteCheckRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SqliteCheckResponse:
    """
    Ask SQLite whether one of an application's databases is sound. Changes nothing.

    The database stays open for the application the whole time. A database over a gigabyte is
    refused here, because the request would wait on the disk: ``noust db sqlite check`` has no
    limit.

    Args:
        domain: Domain of the application.
        body: The database and the thoroughness.
        session: The authenticated session.

    Returns:
        What SQLite said.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The path is not a database of this application, or it is too large
            for a request (400).
    """
    app = _known(domain)
    located = sqlite_files.locate(app, body.path)
    if located.stat().st_size > sqlite_files.MAX_REQUEST_BYTES:
        raise ValidationError(
            f"{body.path} is too large to check from the console",
            details=f"It is over {sqlite_files.MAX_REQUEST_BYTES // (1024 * 1024)} MB. "
            f"Run it in a terminal: noust db sqlite check {app.domain} {body.path}",
        )
    return SqliteCheckResponse(**sqlite_files.check(app, body.path, full=body.full).to_dict())
