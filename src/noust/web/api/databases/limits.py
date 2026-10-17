# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/engines/{engine}/limits``: what an engine on the host may use.

A client of :class:`~noust.managers.database.host_limits.HostLimits`, which ``noust db limits``
calls too. The limits are a systemd drop-in applied without restarting the engine and proved
twice (systemd's own answer and the kernel's cgroup files); a memory limit below what the engine
uses now is refused until it is confirmed. An engine in a container has its own limits, under
``/api/databases/instances/{name}/tuning``.

These are the server's, not a team's: an engine on the host is nobody's.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.deps import NoustErrorRoute, require_elevated

router = APIRouter(route_class=NoustErrorRoute)


class LimitValuesResponse(BaseModel):
    """
    The four limits; null is none.

    Attributes:
        memory_mb: ``MemoryMax``, in megabytes.
        cpus: ``CPUQuota``, in processors (``1.5`` is 150%).
        swap_mb: ``MemorySwapMax``, in megabytes; 0 gives it no swap.
        tasks: ``TasksMax``: processes and threads.
    """

    memory_mb: int | None = None
    cpus: float | None = None
    swap_mb: int | None = None
    tasks: int | None = None


class EngineLimitsResponse(BaseModel):
    """
    Response for ``GET /api/databases/engines/{engine}/limits``.

    Attributes:
        unit: The systemd unit the limits are on.
        file: The drop-in Noust writes.
        configured: What Noust's drop-in sets.
        effective: What systemd holds for the unit, from every source.
        enforced: Per limit, whether the kernel's cgroup holds it: true, false (systemd has it and
            the kernel does not) or null when it cannot be read.
        memory_in_use_mb: What the engine's cgroup holds now, when known.
        server_memory_mb: This server's memory.
        cpus: This server's processors.
        minimum_memory_mb: The least memory the engine runs in.
        elsewhere: Directives another drop-in or the unit itself sets.
    """

    engine: str
    display_name: str
    unit: str
    file: str
    configured: LimitValuesResponse
    effective: LimitValuesResponse
    enforced: dict[str, bool | None]
    memory_in_use_mb: int | None = None
    server_memory_mb: int
    cpus: int
    minimum_memory_mb: int
    elsewhere: list[str] = Field(default_factory=list)


class EngineLimitsRequest(BaseModel):
    """
    A change of an engine's limits. A limit left out (null) is left as it is.

    Attributes:
        memory_mb: The memory limit, in megabytes.
        cpus: The processors it may use, such as ``1.5``.
        swap_mb: The swap it may use, in megabytes; 0 gives it none.
        tasks: The processes and threads it may have.
        clear: Limits to remove: any of ``memory_mb``, ``cpus``, ``swap_mb`` and ``tasks``.
        confirm: The operator accepts a memory limit below what the engine uses now. Without it
            such a change is refused with ``confirmation_required`` and nothing is changed.
    """

    memory_mb: int | None = Field(default=None, ge=1, le=4194304)
    cpus: float | None = Field(default=None, gt=0, le=256)
    swap_mb: int | None = Field(default=None, ge=0, le=4194304)
    tasks: int | None = Field(default=None, ge=1, le=4194304)
    clear: list[str] = Field(default_factory=list, max_length=4)
    confirm: bool = False

    def changes(self) -> dict[str, object]:
        """
        Returns:
            The limits to set, those left out dropped.
        """
        values: dict[str, object] = {
            "memory_mb": self.memory_mb,
            "cpus": self.cpus,
            "swap_mb": self.swap_mb,
            "tasks": self.tasks,
        }
        return {key: value for key, value in values.items() if value is not None}


class EngineLimitsOutcomeResponse(BaseModel):
    """
    What a change of limits did.

    Attributes:
        changed: The limits that changed.
        configured: What Noust's drop-in sets now.
        warnings: What the operator should know: a limit the kernel does not enforce.
    """

    engine: str
    display_name: str
    file: str
    changed: list[str]
    configured: LimitValuesResponse
    warnings: list[str] = Field(default_factory=list)


@router.get("/engines/{engine}/limits", response_model=EngineLimitsResponse)
def get_engine_limits(
    engine: str, session: Annotated[dict, Depends(get_current_session)]
) -> EngineLimitsResponse:
    """
    Read the limits of an engine on the host and whether the kernel holds them. Changes nothing.

    Args:
        engine: Engine name.
        session: The authenticated session.

    Returns:
        What Noust sets, what systemd holds, what the kernel enforces.
    """
    return EngineLimitsResponse(**service(session).engine_limits(engine).to_dict())


@router.put("/engines/{engine}/limits", response_model=EngineLimitsOutcomeResponse)
def put_engine_limits(
    engine: str,
    request: EngineLimitsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> EngineLimitsOutcomeResponse:
    """
    Set, change or remove the limits of an engine on the host, with sudo mode.

    The drop-in is written and systemd applies it without restarting the engine; the limits are
    then proved against systemd and the kernel and the engine must still answer. When any of that
    fails the previous limits are put back and the error carries the engine's journal.

    Args:
        engine: Engine name.
        request: The limits and the confirmation.
        session: The authenticated, elevated session.

    Returns:
        What changed.
    """
    outcome = service(session).change_engine_limits(
        engine, request.changes(), clear=request.clear, confirm=request.confirm
    )
    return EngineLimitsOutcomeResponse(**outcome.to_dict())
