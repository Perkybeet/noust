# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the server is doing that is not a job: ``GET /api/operations/active``.

The long operations that answer on their request (``noust.web.operations`` lists them) are told as
the ``operation`` event and listed here while they run, so another operator, the console's tray and
a central can say what is happening. A thin client of :data:`noust.web.operations.active_operations`;
the central asks each server's list through its tunnel (``/api/fleet/jobs/active``), and a server
that predates this route answers 404, which the central reads as "not updated yet".
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from noust.core.teams import ScopedStore, current_scope
from noust.web.api.deps import CodedText, NoustErrorRoute
from noust.web.operations import Running, active_operations

router = APIRouter(route_class=NoustErrorRoute)


class OperationOut(BaseModel):
    """
    One operation that is running.

    Attributes:
        id: Names its start and its end in the ``operation`` event.
        title: What it is, as a sentence of the server's catalog (code and values).
        target: Its subject (an application, a service, a node), or null for the whole server.
        domain: The application it concerns, when its subject is one.
        actor: Who asked, as the audit trail names them.
        started_at: When it started, ISO 8601 in UTC.
    """

    id: str
    title: CodedText
    target: str | None = None
    domain: str | None = None
    actor: str | None = None
    started_at: str

    @classmethod
    def of(cls, running: Running) -> OperationOut:
        """
        Args:
            running: An operation that is running.

        Returns:
            Its answer.
        """
        return cls(
            id=running.id,
            title=CodedText(**running.title.to_dict()),
            target=running.target,
            domain=running.domain,
            actor=running.actor,
            started_at=running.started_at,
        )


class OperationsResponse(BaseModel):
    """
    The operations running now.

    Attributes:
        operations: Oldest first. A team member's holds only what concerns its own applications.
    """

    operations: list[OperationOut]


@router.get("/active", response_model=OperationsResponse)
def active_operations_now() -> OperationsResponse:
    """
    List the long operations that are running now.

    Returns:
        What runs, oldest first. For a principal that does not see every team, only the
        operations on an application it sees: what is about the machine is the instance's.
    """
    scoped = ScopedStore(current_scope())
    return OperationsResponse(
        operations=[
            OperationOut.of(running)
            for running in active_operations.active()
            if scoped.sees_domain(running.domain)
        ]
    )
