# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/backups/failures``: the whole output of a backup that failed (checklist H-10).

A client of :class:`~noust.managers.backup_failures.BackupFailureLog`, which ``noust backup
failures`` reads too. The notice of a failed backup carries the end of what the tool said and links
to ``GET /{id}/output``, which answers that text whole and as plain text: a signed-in operator opens
it in the browser and nothing in it can be taken for a page.

It is mounted before :mod:`noust.web.api.backups`, whose ``/{backup_id}`` would otherwise take
``failures`` for a backup's name. The records are the server's, not a team's: they hold what
backup tools said about any application.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from noust.managers.backup_failures import BackupFailure, BackupFailureLog
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class BackupFailureSummary(BaseModel):
    """
    One failed backup, without what the tool said.

    Attributes:
        id: The record's number, the one a notice names.
        created_at: When it was recorded, UTC.
        kind: ``backup`` when no backup was taken, ``upload`` when a destination refused one.
        subject: What it was about: a domain, ``Noust``, ``engine/database``.
        destination: The destination that refused it, for an ``upload``.
        size: The output's size in bytes.
        truncated: Whether the front of a very long output was cut.
        command: The command that prints it.
    """

    id: int
    created_at: str
    kind: str
    subject: str
    destination: str | None = None
    size: int
    truncated: bool
    command: str


class BackupFailureDetail(BackupFailureSummary):
    """
    One failed backup with all that the tool said.

    Attributes:
        output: What the tool said, whole up to the bound (512 KiB), scrubbed of the credentials
            Noust knows.
    """

    output: str


class BackupFailuresResponse(BaseModel):
    """
    The failed backups, newest first.

    Attributes:
        failures: The records, without their output.
    """

    failures: list[BackupFailureSummary] = Field(default_factory=list)


def _found(failure_id: int) -> BackupFailure:
    """
    Args:
        failure_id: A record's id.

    Returns:
        The record.

    Raises:
        HTTPException: 404 when there is none (or it was pruned).
    """
    record = BackupFailureLog().get(failure_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Backup failure not found: {failure_id}")
    return record


@router.get("", response_model=BackupFailuresResponse)
def list_failures(
    session: Annotated[dict, Depends(get_current_session)],
    subject: Annotated[str | None, Query(max_length=253)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    """
    List the backups that failed, newest first, without their output.

    Args:
        session: The authenticated session.
        subject: Only the failures about this.
        limit: How many at most.

    Returns:
        The records.
    """
    records = BackupFailureLog().recent(subject=subject, limit=limit)
    return {"failures": [record.summary() for record in records]}


@router.get("/{failure_id}/output", response_class=PlainTextResponse)
def failure_output(
    failure_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> PlainTextResponse:
    """
    Answer all that a failed backup said, as plain text.

    Args:
        failure_id: The record the notice names.
        session: The authenticated session.

    Returns:
        The output, ``text/plain`` and never sniffed into anything else.
    """
    record = _found(failure_id)
    return PlainTextResponse(
        record.output + "\n",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


@router.get("/{failure_id}", response_model=BackupFailureDetail)
def failure_detail(
    failure_id: int,
    session: Annotated[dict, Depends(get_current_session)],
) -> dict[str, Any]:
    """
    Say one failed backup with all that its tool said.

    Args:
        failure_id: The record the notice names.
        session: The authenticated session.

    Returns:
        The record.
    """
    return _found(failure_id).to_dict()
