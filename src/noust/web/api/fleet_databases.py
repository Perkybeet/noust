# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/databases``: a database moved to another server of the fleet (checklist C-21).

A thin layer (rule 3). Moving a database is a bulk action of the fleet engine
(:mod:`noust.web.fleet_database_actions`), planned and queued exactly as ``POST /api/fleet/actions``
plans and queues the others, so one confirmation of sudo mode covers the job and each server
answers for the operator with its own permissions. Nothing here dumps, reads or loads anything.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from noust.web.api.deps import NoustErrorRoute
from noust.web.api.fleet import ActionOut, Session, _plan_and_start

router = APIRouter(route_class=NoustErrorRoute)


class DatabaseTransferIn(BaseModel):
    """
    Move a database to another server.

    Attributes:
        database: The database on the source.
        engine: The engine it is on there: ``postgresql``, ``mysql`` (MariaDB too) or ``mongodb``,
            or an instance (``postgresql@pg16``).
        from_node: The server that holds it.
        to: The server that receives it.
        target_engine: The engine or instance on the target; the source's when absent.
        new_name: Its name on the target; the same when absent.
        replace: Replace a database of that name the target holds, after a safety copy there.
        accounts: Also make the accounts with access to it, with the password the source holds
            for each and the profile they had.
        drop_source: Drop it from the source once it has moved, after a last dump kept there;
            refused for a database an application still uses.
        plan: Only say what would happen.
    """

    database: str
    engine: str
    from_node: str
    to: str
    target_engine: str | None = None
    new_name: str | None = None
    replace: bool = False
    accounts: bool = False
    drop_source: bool = False
    plan: bool = False


@router.post("/transfer", response_model=ActionOut)
async def transfer_database(
    request: Request, body: DatabaseTransferIn, session: Session
) -> dict[str, Any]:
    """
    Move a database to another server: dump it there, carry it, load it here.

    The source dumps the database and the central carries the dump through the two tunnels in
    chunks that resume, never keeping it; the target loads it with its own import, as the
    database's owner and never as the superuser. The accounts follow when asked, with the passwords
    that pass through the central and are kept nowhere. The source keeps the database unless told
    to drop it. Planned and queued like a bulk action: sudo mode when a server's schema asks for
    it, a job to follow, a record per server.

    Args:
        request: The request.
        body: What to move, from where and to where.
        session: The authenticated session.

    Returns:
        The plan and, unless ``plan``, the job.

    Raises:
        ValidationError: A server is not ready for it, the target holds the database, or a value is
            invalid.
    """
    from noust.web.fleet_database_actions import transfer_request

    fleet_request = transfer_request(
        body.database,
        to=body.to,
        engine=body.engine,
        from_node=body.from_node,
        target_engine=body.target_engine,
        new_name=body.new_name,
        replace=body.replace,
        accounts=body.accounts,
        drop_source=body.drop_source,
    )
    return await _plan_and_start(request, session, fleet_request, body.plan)
