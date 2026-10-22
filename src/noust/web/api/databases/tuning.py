# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances/{name}/tuning``: what an instance may use, and how it is probed.

An instance is held to a memory limit, may be held to a number of CPUs and an amount of swap,
and is judged by a health probe whose interval, timeout and retries are settings. All of it is
:class:`~noust.managers.database.instance_tuning.InstanceTuning`, which ``noust db instance
limits`` and ``noust db instance health`` call too. Reading the settings and planning change
nothing and answer at once; changing them restarts the engine, asks for sudo mode and runs as a
job.

These are the server's, not a team's: an instance is nobody's.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.managers.database.instance_tuning import CLEARABLE, InstanceTuning
from noust.managers.database.managed import ManagedInstances
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.databases.jobs import tuning_job
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class TuningRequest(BaseModel):
    """
    A change of what an instance may use or how it is probed.

    A setting left out (null) is left as it is.

    Attributes:
        memory_mb: The new memory limit, in megabytes.
        cpus: The new CPU limit, such as ``1.5``.
        swap_mb: The swap it may use besides its memory, in megabytes; 0 gives it none.
        interval_s: Seconds between health probes.
        timeout_s: Seconds a probe may take; no more than the interval.
        retries: Failed probes in a row before the engine is unhealthy.
        clear: Settings to put back to their default: any of ``cpus``, ``swap``,
            ``interval``, ``timeout`` and ``retries``.
    """

    memory_mb: int | None = Field(default=None, ge=1, le=4194304)
    cpus: float | None = Field(default=None, gt=0, le=256)
    swap_mb: int | None = Field(default=None, ge=0, le=4194304)
    interval_s: int | None = Field(default=None, ge=1, le=100000)
    timeout_s: int | None = Field(default=None, ge=1, le=100000)
    retries: int | None = Field(default=None, ge=1, le=100000)
    clear: list[str] = Field(default_factory=list, max_length=len(CLEARABLE))

    def settings(self) -> dict[str, Any]:
        """
        Returns:
            The settings as :class:`InstanceTuning` takes them.
        """
        return {
            "memory": self.memory_mb,
            "cpus": self.cpus,
            "swap": self.swap_mb,
            "interval": self.interval_s,
            "timeout": self.timeout_s,
            "retries": self.retries,
            "clear": list(self.clear),
        }


class TuningLimits(BaseModel):
    """What an instance is held to: ``cpus`` and ``swap_mb`` are null for Docker's default."""

    memory_mb: int
    cpus: float | None = None
    swap_mb: int | None = None


class TuningProbe(BaseModel):
    """How an instance is probed, each as it is applied."""

    interval_s: int
    timeout_s: int
    retries: int


class TuningServer(BaseModel):
    """What the server can give an instance: ``available_mb`` counts the instance's own memory."""

    memory_mb: int
    available_mb: int
    cpus: int


class ProbeResult(BaseModel):
    """The last probe Docker ran."""

    exit_code: int | None = None
    output: str = ""


class TuningHealth(BaseModel):
    """
    What Docker says of the container now.

    Attributes:
        status: ``healthy``, ``starting``, ``unhealthy`` or ``absent`` (no container).
        failing_streak: Failed probes in a row.
        last: The last probe, with the output the engine's own command printed.
    """

    status: str
    failing_streak: int = 0
    last: ProbeResult | None = None


class TuningStatusResponse(BaseModel):
    """What an instance is held to, how it is probed, and how it is doing."""

    name: str
    key: str
    limits: TuningLimits
    probe: TuningProbe
    defaults: TuningProbe
    server: TuningServer
    health: TuningHealth


class TuningPlanResponse(BaseModel):
    """
    What a change would do. Nothing was changed.

    Attributes:
        changes: The settings that would change, each with ``setting``, ``from`` and ``to``.
        values: What the instance would have afterwards.
        restarts: Whether the engine restarts.
        apps: The applications that lose their connections while it does.
        blockers: What stops the change.
        warnings: What the operator should know first.
    """

    name: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    values: dict[str, Any] = Field(default_factory=dict)
    restarts: bool
    apps: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _tuning(session: dict[str, Any]) -> InstanceTuning:
    """
    Build what a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The tuning manager over the request's database service.
    """
    return InstanceTuning(ManagedInstances(service(session)))


@router.get("/instances/{name}/tuning", response_model=TuningStatusResponse)
def get_tuning(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> TuningStatusResponse:
    """
    Say what an instance is held to, how it is probed, and how it is doing.

    Args:
        name: The instance.
        session: The authenticated session.

    Returns:
        Its limits, its probe and the defaults of the probe, what the server can give
        it, and what Docker's health probe says now.

    Raises:
        DatabaseNotFoundError: There is no such instance.
    """
    return TuningStatusResponse(**_tuning(session).status(name))


@router.post("/instances/{name}/tuning/plan", response_model=TuningPlanResponse)
def plan_tuning(
    name: str, request: TuningRequest, session: Annotated[dict, Depends(get_current_session)]
) -> TuningPlanResponse:
    """
    Say what a change would do. Changes nothing.

    Args:
        name: The instance.
        request: The change.
        session: The authenticated session.

    Returns:
        The plan, with the applications that lose their connections and what blocks it.

    Raises:
        ValidationError: A value cannot be had; ``field`` says which.
        DatabaseNotFoundError: There is no such instance.
    """
    plan = _tuning(session).plan(name, **request.settings())
    return TuningPlanResponse(**plan.to_dict())


@router.put("/instances/{name}/tuning", response_model=JobAcceptedResponse, status_code=202)
def set_tuning(
    name: str, request: TuningRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue changing what an instance may use or how it is probed, with sudo mode.

    The engine restarts on the new numbers; when it does not come up the previous ones
    are put back and the job fails with the engine's own log.

    Args:
        name: The instance.
        request: The change.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: A value cannot be had, or the instance is being changed.
        DatabaseNotFoundError: There is no such instance.
    """
    tuning = _tuning(session)
    plan = tuning.plan(name, **request.settings())
    if plan.blockers:
        # The refusal the job would give, now rather than in a failed job.
        tuning.apply(name, **request.settings())
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Change the settings of {name}",
        description=f"Restarting {name} on its new limits and probe",
        func=tuning_job,
        kwargs={
            "name": name,
            "memory_mb": request.memory_mb,
            "cpus": request.cpus,
            "swap_mb": request.swap_mb,
            "interval_s": request.interval_s,
            "timeout_s": request.timeout_s,
            "retries": request.retries,
            "clear": list(request.clear),
        },
        metadata={"instance": name, "action": "tuning"},
        message=f"{name} will restart on its new settings",
    )
