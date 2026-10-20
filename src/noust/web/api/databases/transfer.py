# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/backups/{name}/data``: a dump read by ranges, for a central carrying it away (C-21).

Moving a database to another server of the fleet is the central's job (``noust db transfer``): it
asks this server for a dump, reads it here and writes it to the other server's upload, in pieces,
because a dump is gigabytes and a request through a tunnel is cut after minutes. This is the
reading half. It has the download's power and nothing more (sudo mode, a dump of the engine's own
folder by name and never a path), and a piece can be asked again after a failure.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from fastapi.concurrency import run_in_threadpool

from noust.core import audit
from noust.web.api.databases.common import service, session_actor
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)

#: The most one piece may carry. A central holds one piece at a time, never a dump, and a request
#: through a tunnel is cut after minutes, so a piece is a few megabytes and not a gigabyte.
MAX_PIECE_BYTES = 8 * 1024**2

#: What a piece weighs when the caller does not say.
DEFAULT_PIECE_BYTES = 1024**2


class DumpBytes(Response):
    """A range of a dump: bytes the caller carries on, never JSON."""

    media_type = "application/octet-stream"


def _read(path: str, offset: int, length: int) -> bytes:
    """
    Read a range of a file.

    Args:
        path: The file.
        offset: Where to start.
        length: How many bytes at most.

    Returns:
        The bytes; fewer at the end of the file and none past it.
    """
    with open(path, "rb") as handle:
        handle.seek(offset)
        return handle.read(length)


@router.get(
    "/backups/{name}/data",
    response_class=DumpBytes,
    responses={
        200: {
            "description": "The bytes asked for: fewer at the end of the dump, none past it.",
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def dump_data(
    name: str,
    engine: Annotated[str, Query(description="The engine")],
    offset: Annotated[int, Query(ge=0, description="Where to start in the dump")],
    session: Annotated[dict, Depends(require_elevated)],
    length: Annotated[
        int, Query(ge=1, le=MAX_PIECE_BYTES, description="How many bytes at most")
    ] = DEFAULT_PIECE_BYTES,
) -> DumpBytes:
    """
    Read a range of a dump, with sudo mode.

    A dump is the whole database, so this is the download's power: a sensitive read, of a dump in
    the engine's own backup folder, by name. The reading is recorded where it begins (offset 0),
    once, with the dump's size; a piece asked again in the middle records nothing more.

    Args:
        name: The dump's file name.
        engine: The engine.
        offset: Where to start.
        session: The authenticated, elevated session.
        length: How many bytes at most.

    Returns:
        The raw bytes; fewer at the end of the dump, none past it.

    Raises:
        DatabaseNotFoundError: 404 when the dump does not exist.
    """
    databases = service(session)
    manager = databases.manager(engine)
    path = databases.dump_path(manager.ENGINE_NAME, name)
    if offset == 0:
        audit.record(
            "db.backup.download",
            actor=session_actor(session),
            target=f"db:{manager.ENGINE_NAME}",
            details={"file": path.name, "size": path.stat().st_size, "ranged": True},
        )
    return DumpBytes(content=await run_in_threadpool(_read, str(path), offset, length))
