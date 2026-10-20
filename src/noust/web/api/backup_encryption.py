# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/backups/encryption``: the backups kept on this server's disk, sealed (checklist H-08).

A client of :class:`~noust.core.backup_files.BackupKeys` and
:class:`~noust.managers.backup_sealing.SealExisting`, which ``noust backup encryption`` uses too.
Turning sealing on, rotating the key and stopping change how backups are kept, and importing or
showing a key touches a secret, so those ask for sudo mode; showing one also needs the permission
that reads secrets, answers the value once per request and is recorded without it. Sealing what is
already on the disk is a job, refused up front while sealing is off.

It is mounted before :mod:`noust.web.api.backups`, whose ``/{backup_id}`` would otherwise take
``encryption`` for a backup's name. These are the server's, not a team's.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.audit import Actor
from noust.core.backup_files import BackupKeys
from noust.core.exceptions import BackupError, ValidationError
from noust.managers.backup_sealing import SealExisting, SealItem, encryption_status
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobContext, JobType

router = APIRouter(route_class=NoustErrorRoute)


class KeyInfo(BaseModel):
    """
    A key this server holds, without its value.

    Attributes:
        id: Its identifier, written in the label of every backup it sealed.
        current: Whether new backups are sealed with it.
    """

    id: str
    current: bool


class EncryptionStatus(BaseModel):
    """
    Whether backups are sealed on this server.

    Attributes:
        enabled: Whether new backups are sealed.
        current_key: The key they are sealed with.
        keys: Every key held.
        sealed: Backups on the disk that are sealed.
        clear: Backups on the disk that are in clear.
    """

    enabled: bool
    current_key: str | None = None
    keys: list[KeyInfo] = Field(default_factory=list)
    sealed: int = 0
    clear: int = 0


class EnableResult(BaseModel):
    """
    The outcome of turning sealing on.

    Attributes:
        enabled: Always True.
        key_id: The current key; its value is shown by ``GET /keys/{key_id}``, once the operator
            asks for it with sudo mode.
        created: False when sealing was already on and nothing changed.
    """

    enabled: bool
    key_id: str
    created: bool


class RotateResult(BaseModel):
    """
    The outcome of a rotation.

    Attributes:
        key_id: The new current key.
    """

    key_id: str


class DisableResult(BaseModel):
    """
    The outcome of stopping.

    Attributes:
        enabled: Always False.
        was_enabled: Whether anything was being sealed.
    """

    enabled: bool
    was_enabled: bool


class KeyValue(BaseModel):
    """
    A key's value, shown once per request.

    Attributes:
        key_id: Its id.
        key: Its value. Keep it somewhere that is not this server.
    """

    key_id: str
    key: str


class ImportKeyRequest(BaseModel):
    """
    A key to put back, from the copy kept elsewhere.

    Attributes:
        key_id: The id a backup that does not open names.
        key: Its value.
    """

    key_id: str = Field(max_length=64)
    key: str = Field(max_length=512)


class KeyId(BaseModel):
    """
    Which key was put back.

    Attributes:
        key_id: Its id.
    """

    key_id: str


class SealPlan(BaseModel):
    """
    What sealing the backups already there would touch.

    Attributes:
        applications: Application archives in clear.
        data: Data backups in clear.
        databases: Database dumps in clear.
        pitr: Base backups of point-in-time recovery in clear.
        total: The sum.
        sealing: Whether new backups are sealed; a run needs it.
    """

    applications: int
    data: int
    databases: int
    pitr: int
    total: int
    sealing: bool


def _refused(error: BackupError) -> ValidationError:
    """
    Args:
        error: A refusal of the keys or the sealing.

    Returns:
        The same words as a request the client got wrong (400), not a server fault.
    """
    return ValidationError(error.message, details=error.details)


@router.get("", response_model=EncryptionStatus)
def status(session: Annotated[dict, Depends(get_current_session)]) -> dict[str, Any]:
    """
    Say whether new backups are sealed, with which key, and how many are on the disk each way.

    Args:
        session: The authenticated session.

    Returns:
        The status; never a key's value.
    """
    return encryption_status()


@router.post("/enable", response_model=EnableResult)
def enable(session: Annotated[dict, Depends(require_elevated)]) -> dict[str, Any]:
    """
    Seal every backup this server makes from now on.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The current key's id, and whether it was made now. Backups made before stay in clear
        until ``POST /seal-existing``.
    """
    key_id, created = BackupKeys().enable()
    return {"enabled": True, "key_id": key_id, "created": created}


@router.post("/rotate", response_model=RotateResult)
def rotate(session: Annotated[dict, Depends(require_elevated)]) -> dict[str, Any]:
    """
    Make a new key for the backups made from now on; the old one still opens what it sealed.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The new key's id.

    Raises:
        ValidationError: When sealing is off.
    """
    try:
        return {"key_id": BackupKeys().rotate()}
    except BackupError as exc:
        raise _refused(exc) from exc


@router.post("/disable", response_model=DisableResult)
def disable(session: Annotated[dict, Depends(require_elevated)]) -> dict[str, Any]:
    """
    Stop sealing new backups. Every key is kept, so what was sealed still opens.

    Args:
        session: The authenticated, elevated session.

    Returns:
        Whether anything was being sealed.
    """
    return {"enabled": False, "was_enabled": BackupKeys().stop_sealing()}


@router.get("/keys/{key_id}", response_model=KeyValue)
def show_key(
    key_id: str,
    response: Response,
    session: Annotated[dict, Depends(require_elevated)],
) -> dict[str, str]:
    """
    Show a key so it can be kept somewhere that is not this server.

    Args:
        key_id: The key's id, or ``current``.
        response: The response, to keep the value out of every cache.
        session: The authenticated, elevated session.

    Returns:
        The key's id and value. The read is recorded, without the value.

    Raises:
        HTTPException: 404 when no key is current or this server holds no such key.
    """
    try:
        shown, value = BackupKeys().reveal(None if key_id == "current" else key_id)
    except BackupError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    response.headers["Cache-Control"] = "no-store"
    return {"key_id": shown, "key": value}


@router.post("/keys", response_model=KeyId)
def import_key(
    request: ImportKeyRequest, session: Annotated[dict, Depends(require_elevated)]
) -> dict[str, str]:
    """
    Put back a key this server lost, from the copy kept elsewhere.

    Args:
        request: The id and the value.
        session: The authenticated, elevated session.

    Returns:
        The id; the value is never echoed.

    Raises:
        ValidationError: When it is not a key's id, the value is too short to be one, or the key
            is already here with another value.
    """
    try:
        BackupKeys().import_key(request.key_id, request.key)
    except BackupError as exc:
        raise _refused(exc) from exc
    return {"key_id": request.key_id}


@router.get("/seal-existing", response_model=SealPlan)
def seal_plan(session: Annotated[dict, Depends(get_current_session)]) -> dict[str, Any]:
    """
    Count the backups already on the disk that sealing them would touch. Changes nothing.

    Args:
        session: The authenticated session.

    Returns:
        How many of each kind are in clear.
    """
    return SealExisting().plan()


def seal_job(actor: str | None = None, job_context: JobContext | None = None) -> dict[str, Any]:
    """
    Seal the backups already on the disk.

    Args:
        actor: Who queued it, for the audit trail.
        job_context: Injected by the job manager.

    Returns:
        The report, as :meth:`~noust.managers.backup_sealing.SealReport.to_dict` says.
    """

    def progress(item: SealItem) -> None:
        if job_context is not None:
            line = f"{item.kind} {item.name}: {item.state}"
            job_context.log(f"{line} - {item.detail}" if item.detail else line)

    if job_context is not None:
        job_context.update("Sealing the backups already on this server", 5)
    with audit.bind(actor=Actor.from_label(actor) if actor else None):
        report = SealExisting().run(on_item=progress)
    if job_context is not None:
        job_context.update("Done", 100)
    return report.to_dict()


@router.post("/seal-existing", response_model=JobAcceptedResponse, status_code=202)
def seal_existing(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Queue sealing the backups that were already on the disk when sealing was turned on.

    Args:
        session: The authenticated, elevated session.

    Returns:
        The queued job; its result is the report.

    Raises:
        ValidationError: When sealing is off.
    """
    if not BackupKeys().enabled():
        raise ValidationError(
            "Backups are not sealed on this server",
            details="Turn sealing on first: noust backup encryption enable, or "
            "POST /api/backups/encryption/enable.",
        )
    return queue(
        session,
        job_type=JobType.CUSTOM,
        name="Seal the existing backups",
        description="Sealing the backups that were made in clear and writing their records again",
        func=seal_job,
        kwargs={},
        metadata={"kind": "seal-existing"},
        message="Sealing the existing backups",
    )
