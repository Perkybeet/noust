# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/backup-collection``: what a central takes away from this server, and what it says it holds (item 195).

The tunnel a central opens goes one way (the central dials, the server cannot reach back), so a
server does not send its copies anywhere: its central comes for them, one at a time, in pieces it
can ask again after a failure, and says afterwards which it has. A client of
:class:`~noust.managers.backup_catalogue.BackupCatalogue`, which is the only thing that decides
which file of this server may be read:

- ``GET /api/backup-collection`` lists the copies, as the files each is made of and the digest
  Noust recorded for each, with a fingerprint of the list: asked again with ``known=<fingerprint>``
  it answers "nothing changed" and lists nothing, which is how a central looks every minute without
  carrying the list every minute.
- ``GET /api/backup-collection/files`` reads a range of one file by its kind, its copy and its name
  in that list (``offset`` and ``length``, as every other range read here). A name the list does not
  have is a 404, whatever it looks like: nothing a caller sends is joined to a path.
- ``PUT /api/backup-collection/held`` is a central saying which copies it holds, in place of what it
  said before. It is what the server's retention waits for and what counts as a copy kept
  elsewhere; it never makes the server delete anything.

All of it needs ``backups.collect``, which only a central holds here (a ceiling of ``read`` does not
give it away) and which reads and records and changes nothing else. A copy is the application's
``.env`` and its databases, so reading one is recorded, once, where it begins.
"""

from __future__ import annotations

import os
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from noust.core import audit
from noust.core.backup_collection_store import CentralHeld, HeldCopy
from noust.core.exceptions import SecurityError, ValidationError
from noust.core.fleet_tokens import central_of_token
from noust.managers.backup_catalogue import KINDS, BackupCatalogue, open_for_reading
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import session_actor
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)

#: The most one piece may carry: a central holds one at a time, and a request through a tunnel is
#: cut after minutes, so a piece is a few megabytes and never a whole archive.
MAX_PIECE_BYTES = 8 * 1024**2

#: What a piece weighs when the caller does not say.
DEFAULT_PIECE_BYTES = 4 * 1024**2


class CopyFileOut(BaseModel):
    """One file of a copy."""

    name: str
    size: int
    sha256: str | None = Field(
        default=None, description="The digest Noust recorded for it; none for a file it did not"
    )
    role: str = Field(description="`archive` is the copy; `sidecar` says what it is")


class CopyOut(BaseModel):
    """One copy, as the files it is made of."""

    kind: str = Field(description="`app`, `database`, `volume` or `noust`")
    id: str = Field(description="Its identifier on this server, unique for its kind")
    owner: str = Field(description="What it is a copy of")
    folder: str = Field(description="The same as one plain name")
    created_at: str = Field(description="When it was taken, UTC")
    origin: str = Field(description="`scheduled`, `manual` or `safety`")
    sealed: bool = Field(description="Whether its file is encrypted where it lies")
    size: int = Field(description="What all its files weigh")
    files: list[CopyFileOut]
    meta: dict[str, str] = Field(default_factory=dict)
    blocked: str | None = Field(
        default=None,
        description="Why a central may not take it: `sealing_required` when this server lets a "
        "copy leave only encrypted and this one is not",
    )


class ProblemOut(BaseModel):
    """A kind of copy that could not be listed."""

    kind: str
    message: str


class CollectionOut(BaseModel):
    """The copies of this server."""

    fingerprint: str = Field(description="Changes when a copy appears, goes or changes size")
    unchanged: bool = Field(
        default=False, description="True when `known` was the fingerprint: nothing is listed"
    )
    items: list[CopyOut] = Field(default_factory=list)
    problems: list[ProblemOut] = Field(default_factory=list)
    piece_size: int = Field(
        default=DEFAULT_PIECE_BYTES, description="What a piece weighs by default"
    )
    max_piece: int = Field(default=MAX_PIECE_BYTES, description="The most a piece may weigh")


class HeldItem(BaseModel):
    """A copy a central says it holds."""

    kind: str
    id: str
    sha256: str | None = Field(default=None, description="The digest it checked the copy against")


class HeldIn(BaseModel):
    """Everything a central holds of this server."""

    items: list[HeldItem] = Field(default_factory=list)


class HeldOut(BaseModel):
    """What was recorded."""

    central: str
    held: int
    synced_at: str


class HeldCentralOut(BaseModel):
    """A central that has said what it holds."""

    central: str
    held: int
    synced_at: str
    fresh: bool = Field(description="It spoke recently enough to count")


class HeldListOut(BaseModel):
    """The centrals that collect from this server."""

    centrals: list[HeldCentralOut]


class PieceBytes(Response):
    """A range of a file: bytes the caller carries on, never JSON."""

    media_type = "application/octet-stream"


def _read(descriptor: int, offset: int, length: int) -> bytes:
    """
    Read a range of an open file and close it.

    Args:
        descriptor: A file descriptor, open for reading.
        offset: Where to start.
        length: How many bytes at most.

    Returns:
        The bytes; fewer at the end of the file and none past it.
    """
    try:
        os.lseek(descriptor, offset, os.SEEK_SET)
        return os.read(descriptor, length)
    finally:
        os.close(descriptor)


@router.get("", response_model=CollectionOut)
def list_collection(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    kinds: Annotated[str | None, Query(description="Only these kinds, separated by commas")] = None,
    known: Annotated[
        str | None, Query(description="The fingerprint the caller already has")
    ] = None,
) -> CollectionOut:
    """
    List the copies of this server.

    Args:
        session: The authenticated session.
        kinds: Only these kinds.
        known: A fingerprint the caller already holds: when it is still the list's, nothing is
            listed and ``unchanged`` says so.

    Returns:
        The copies and the fingerprint of the list.

    Raises:
        ValidationError: A kind that is not one.
    """
    wanted: list[str] | None = None
    if kinds:
        wanted = [part.strip() for part in kinds.split(",") if part.strip()]
        unknown = [kind for kind in wanted if kind not in KINDS]
        if unknown:
            raise ValidationError(
                f"Unknown kind of copy: {unknown[0]!r}",
                details=f"The kinds are: {', '.join(KINDS)}.",
                field="kinds",
            )
    catalogue = BackupCatalogue().build(wanted)
    if known and known == catalogue.fingerprint:
        return CollectionOut(fingerprint=catalogue.fingerprint, unchanged=True)
    return CollectionOut(
        fingerprint=catalogue.fingerprint,
        items=[CopyOut(**item.to_dict()) for item in catalogue.items],
        problems=[ProblemOut(**problem) for problem in catalogue.problems],
    )


@router.get(
    "/files",
    response_class=PieceBytes,
    responses={
        200: {
            "description": "The bytes asked for: fewer at the end of the file, none past it.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def read_file(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    kind: Annotated[str, Query(description="The kind of copy")],
    item: Annotated[str, Query(description="The copy's identifier, as listed")],
    name: Annotated[str, Query(description="The file's name, as listed")],
    offset: Annotated[int, Query(ge=0, description="Where to start in the file")],
    length: Annotated[
        int, Query(ge=1, le=MAX_PIECE_BYTES, description="How many bytes at most")
    ] = DEFAULT_PIECE_BYTES,
) -> PieceBytes:
    """
    Read a range of one file of a copy.

    The file is the one the list names for that copy, found by exactly what the list said; the
    request's text never reaches a path. The reading is recorded where it begins (offset 0), once
    per file; a piece asked again in the middle records nothing more.

    Args:
        session: The authenticated session.
        kind: The kind of copy.
        item: Its identifier.
        name: The file's name.
        offset: Where to start.
        length: How many bytes at most.

    Returns:
        The raw bytes; fewer at the end of the file and none past it.

    Raises:
        HTTPException: 404 when this server lists no such file, or it went away since.
        SecurityError: 400 when this server lets a copy leave only encrypted and this one is not.
    """
    catalogue = BackupCatalogue()
    found = await run_in_threadpool(catalogue.item, kind, item)
    file = found.file(name) if found is not None else None
    if found is None or file is None:
        raise HTTPException(status_code=404, detail="This server lists no such copy file")
    if found.blocked:
        raise SecurityError(
            "This server lets a copy leave only encrypted, and this one is not",
            details="The ens-medium profile (or backup.encryption: required) refuses it. Seal "
            "the backups here: noust backup encryption enable, then noust backup encryption "
            "seal-existing.",
        )
    try:
        descriptor = open_for_reading(file)
    except OSError as exc:
        raise HTTPException(
            status_code=404, detail="The file went away or changed since it was listed"
        ) from exc
    if offset == 0:
        audit.record(
            "backup.collection.read",
            actor=session_actor(session),
            target=f"{found.kind}:{found.owner}",
            details={"copy": found.id, "file": file.name, "size": file.size},
        )
    return PieceBytes(content=await run_in_threadpool(_read, descriptor, offset, length))


@router.get("/held", response_model=HeldListOut)
def held_by_centrals(
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> HeldListOut:
    """
    Say which centrals collect the copies of this server.

    Args:
        session: The authenticated session.

    Returns:
        Each central that has said what it holds, and whether it spoke recently.
    """
    return HeldListOut(
        centrals=[
            HeldCentralOut(
                central=sync.central, held=sync.held, synced_at=sync.synced_at, fresh=sync.fresh()
            )
            for sync in CentralHeld().syncs()
        ]
    )


@router.put("/held", response_model=HeldOut)
def say_what_is_held(
    body: HeldIn,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
) -> HeldOut:
    """
    Record which copies the calling central holds, in place of what it said before.

    Only a central says it: the name comes from the fleet token it presented, never from the body.

    Args:
        body: Every copy it holds of this server.
        session: The authenticated session.

    Returns:
        What was recorded.

    Raises:
        ValidationError: The caller is not a central, or a copy is not one.
    """
    central = central_of_token(str(session.get("token_name") or ""))
    if central is None:
        raise ValidationError(
            "Only a central says which copies of this server it holds",
            details="Call this with the fleet token a central holds for this server.",
        )
    sync = CentralHeld().replace(
        central, [HeldCopy(item.kind, item.id, item.sha256) for item in body.items]
    )
    audit.record(
        "backup.collection.held",
        actor=session_actor(session),
        target=f"central:{central}",
        details={"held": sync.held},
    )
    return HeldOut(central=sync.central, held=sync.held, synced_at=sync.synced_at)
