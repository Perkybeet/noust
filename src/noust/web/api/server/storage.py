# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/server/storage`` and ``/api/server/swap``: disks, what takes their space,
cleaning it, and swap.

Measuring a tree is a job with a deadline per path, never part of a request; the
answer is kept and the page says how old it is. Cleaning is a closed list of
actions: no endpoint here accepts a path, and the two Docker commands that would
take the databases' volumes or every Compose application's way back are not on
the list at all.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from noust.core.exceptions import ValidationError
from noust.managers.server.errors import (
    ConfirmationRequiredError,
    ServerError,
    UnsupportedHostError,
)
from noust.managers.server.storage import CLEANUP_ACTIONS, Analysis
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, require_elevated
from noust.web.api.server.common import (
    ServerRoute,
    accepted,
    audit_event,
    get_server_context,
)
from noust.web.api.server.jobs import analyze_job, cleanup_job, swap_job
from noust.web.api.server.models import (
    AnalysisOut,
    CandidateOut,
    CleanupPlanOut,
    CleanupRequest,
    CreateSwapRequest,
    DockerImageOut,
    MountOut,
    StepsOut,
    StorageOut,
    SwapDeviceOut,
    SwapOut,
    SwappinessRequest,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobType, get_job_manager

router = APIRouter(route_class=ServerRoute)
swap_router = APIRouter(route_class=ServerRoute)

ANALYSIS_KEY = "storage.analysis"

#: How long the storage page's answer is reused: asking Docker what it holds is a second.
TTL_STORAGE = 30


def _last_analysis() -> Analysis | None:
    """
    Read the last scan, when there is one.

    Returns:
        The analysis the last scan job stored, or None.
    """
    fact = get_server_context().cache.peek(ANALYSIS_KEY)
    return fact.value if fact is not None and isinstance(fact.value, Analysis) else None


def _params(body: CleanupRequest) -> dict[str, Any]:
    """
    Pick the parameters of a cleanup out of a request.

    Args:
        body: The request.

    Returns:
        The parameters the action takes, without the unset ones.
    """
    return {
        key: value
        for key, value in (("size_mb", body.size_mb), ("days", body.days), ("target", body.target))
        if value is not None
    }


@router.get("", response_model=StorageOut)
def get_storage(session: Annotated[dict, Depends(get_current_session)]) -> StorageOut:
    """
    Show the filesystems and what takes their space.

    Nothing here walks a tree: the journal and Docker are asked, the rest comes
    from the last scan (``analysis_at`` says when). ``POST
    /api/server/storage/analyze`` measures it.

    Args:
        session: The authenticated session.

    Returns:
        One row per real device (bind mounts and pseudo filesystems left out),
        the fullest writable one, and the places that could give space back.
    """
    ctx = get_server_context()
    fact = ctx.cache.get(
        "storage.usage", lambda: ctx.storage.usage(_last_analysis()), TTL_STORAGE, wait=True
    )
    if fact is None or fact.value is None:
        raise ServerError("Could not read the disks", (fact.error if fact else None) or "")
    usage = fact.value
    return StorageOut(
        mounts=[MountOut(**mount.to_dict()) for mount in usage["mounts"]],
        worst=MountOut(**usage["worst"].to_dict()) if usage["worst"] else None,
        candidates=[CandidateOut(**candidate.to_dict()) for candidate in usage["candidates"]],
        analysis_at=usage["analysis_at"],
    )


@router.post("/analyze", response_model=JobAcceptedResponse, status_code=202)
def analyze_storage(session: Annotated[dict, Depends(get_current_session)]) -> JobAcceptedResponse:
    """
    Measure the known places that take space, as a job.

    Each path has its own deadline, and the scan runs at the lowest I/O priority.
    It writes nothing, so it does not need sudo mode.

    Args:
        session: The authenticated session.

    Returns:
        The queued job; its result is the analysis.
    """
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.DISK_SCAN,
        name="Measure what takes disk space",
        description="Measuring caches, releases, backups, logs and data directories",
        func=analyze_job,
        kwargs={"actor": actor},
        actor=actor,
    )
    audit_event("server.storage", target="storage", stage="queued", job=job.id, action="analyze")
    return accepted(job, "Scan queued")


@router.get("/analyze/latest", response_model=AnalysisOut)
def get_last_analysis(session: Annotated[dict, Depends(get_current_session)]) -> AnalysisOut:
    """
    Read the last scan.

    Args:
        session: The authenticated session.

    Returns:
        The analysis, or one with ``measured_at`` null when none has run since the
        console started.
    """
    analysis = _last_analysis()
    if analysis is None:
        return AnalysisOut()
    return AnalysisOut(
        measured_at=analysis.measured_at,
        candidates=[CandidateOut(**candidate.to_dict()) for candidate in analysis.candidates],
        errors=analysis.errors,
    )


@router.get("/docker/images", response_model=list[DockerImageOut])
def list_unused_images(
    session: Annotated[dict, Depends(get_current_session)],
) -> list[DockerImageOut]:
    """
    List the Docker images no container uses.

    The ``wasm-previous`` image of a Compose application, its way back, is left
    out even though nothing runs it.

    Args:
        session: The authenticated session.

    Returns:
        The images, to be removed one by one with ``docker-image``.
    """
    return [
        DockerImageOut(**image) for image in get_server_context().storage.docker_unused_images()
    ]


@router.get("/cleanup/plan", response_model=CleanupPlanOut)
def plan_cleanup(
    session: Annotated[dict, Depends(get_current_session)],
    action: Annotated[str, Query()],
    size_mb: Annotated[int | None, Query()] = None,
    days: Annotated[int | None, Query()] = None,
    target: Annotated[str | None, Query()] = None,
) -> CleanupPlanOut:
    """
    Say what a cleanup action would do, without doing it.

    Args:
        session: The authenticated session.
        action: One of the closed list of actions.
        size_mb: For ``journal``: what to vacuum it to.
        days: For ``journal``: keep this many days instead.
        target: For ``docker-image``: the image id.

    Returns:
        The commands, what it takes, what it would remove where it can list that,
        and whether it needs a confirmation.

    Raises:
        ValidationError: The action or a parameter is not valid (400).
    """
    params = _params(CleanupRequest(action=action, size_mb=size_mb, days=days, target=target))
    plan = get_server_context().storage.plan_cleanup(action, **params)
    return CleanupPlanOut(**asdict(plan))


@router.post("/cleanup", response_model=JobAcceptedResponse, status_code=202)
def cleanup_storage(
    body: CleanupRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Run one cleanup action, as a job.

    Args:
        body: The action, its parameters and whether the caller read what it takes.
        session: The elevated session.

    Returns:
        The queued job, whose log is the tools' own output.

    Raises:
        ValidationError: The action or a parameter is not valid (400).
        ConfirmationRequiredError: The action takes something the operator may
            want back and ``confirm`` was not given (409).
        UnsupportedHostError: This machine has nothing to clean that way (501).
    """
    if body.action not in CLEANUP_ACTIONS:
        raise ValidationError(
            f"Unknown cleanup action: {body.action!r}",
            f"Use one of: {', '.join(CLEANUP_ACTIONS)}.",
            field="action",
        )
    params = _params(body)
    # Judged now, so a refusal is an answer and not a job that fails a second later.
    plan = get_server_context().storage.plan_cleanup(body.action, **params)
    if plan.needs_confirmation and not body.confirm:
        raise ConfirmationRequiredError(
            f"'{body.action}' deletes something you may want back",
            plan.effect,
            required={
                "action": body.action,
                "commands": list(plan.commands),
                "items": list(plan.items),
            },
        )
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.CLEANUP,
        name=f"Clean: {body.action}",
        description=plan.effect,
        func=cleanup_job,
        kwargs={"action": body.action, "confirm": body.confirm, "params": params, "actor": actor},
        metadata={"action": body.action},
        actor=actor,
    )
    audit_event(
        "server.storage",
        target="storage",
        stage="queued",
        job=job.id,
        action="cleanup",
        cleanup=body.action,
        **params,
    )
    return accepted(job, "Cleanup queued")


def _swap_out() -> SwapOut:
    """
    Describe the swap of the machine.

    Returns:
        Its API shape.
    """
    status = get_server_context().swap.status()
    return SwapOut(
        devices=[SwapDeviceOut(**asdict(device)) for device in status.devices],
        total_bytes=status.total_bytes,
        used_bytes=status.used_bytes,
        swappiness=status.swappiness,
        memory_bytes=status.memory_bytes,
        suggested_bytes=status.suggested_bytes,
        recommended=status.recommended,
        supported=status.supported,
        reason=status.reason,
        noust_swapfile=status.noust_swapfile,
        warnings=status.warnings,
    )


@swap_router.get("", response_model=SwapOut)
def get_swap(session: Annotated[dict, Depends(get_current_session)]) -> SwapOut:
    """
    Describe the swap of the machine.

    Args:
        session: The authenticated session.

    Returns:
        Every active swap area, the RAM, the size Noust would suggest, whether a
        swap file can be made here (a container cannot) and why not.
    """
    return _swap_out()


@swap_router.post("", response_model=JobAcceptedResponse, status_code=202)
def create_swap(
    body: CreateSwapRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Make a swap file and switch it on, as a job.

    Refused when the machine is a container, when ``/swapfile`` exists, when the
    filesystem cannot hold one, or when it would leave the disk too full. Each
    step is undone if a later one fails.

    Args:
        body: The size in MiB and the swappiness.
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        UnsupportedHostError: A container or an unsuitable filesystem (501).
        ValidationError: The size is out of range or would fill the disk (400).
        ServerError: ``/swapfile`` already exists.
    """
    ctx = get_server_context()
    status = ctx.swap.status()
    if not status.supported:
        raise UnsupportedHostError("Swap cannot be made here", status.reason)
    ctx.swap.check_size(body.size_mb * 1024**2)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SWAP,
        name=f"Make a {body.size_mb} MiB swap file",
        description="fallocate, mkswap, swapon and an fstab line",
        func=swap_job,
        kwargs={
            "action": "create",
            "size_mb": body.size_mb,
            "swappiness": body.swappiness,
            "actor": actor,
        },
        metadata={"action": "create"},
        actor=actor,
    )
    audit_event(
        "server.storage",
        target="swap",
        stage="queued",
        job=job.id,
        action="swap-create",
        size_mb=body.size_mb,
    )
    return accepted(job, "Swap queued")


@swap_router.delete("", response_model=JobAcceptedResponse, status_code=202)
def remove_swap(session: Annotated[dict, Depends(require_elevated)]) -> JobAcceptedResponse:
    """
    Switch off and delete the swap file Noust made, as a job.

    Only that file: a swap partition, the installer's swap image and zram are
    never touched, and the guard is the mark Noust leaves in ``fstab``.

    Args:
        session: The elevated session.

    Returns:
        The queued job.

    Raises:
        ServerError: The swap is not one Noust made (409 by the job's failure).
    """
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.SWAP,
        name="Remove the swap file",
        description="swapoff, then the fstab line and the file",
        func=swap_job,
        kwargs={"action": "remove", "actor": actor},
        metadata={"action": "remove"},
        actor=actor,
    )
    audit_event("server.storage", target="swap", stage="queued", job=job.id, action="swap-remove")
    return accepted(job, "Removal queued")


@swap_router.put("/swappiness", response_model=StepsOut)
def set_swappiness(
    body: SwappinessRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> StepsOut:
    """
    Set how eagerly the kernel swaps, now and at boot.

    Args:
        body: 0 to 100.
        session: The elevated session.

    Returns:
        What was done.

    Raises:
        ValidationError: The value is out of range (400).
    """
    steps = get_server_context().swap.set_swappiness(body.value)
    audit_event("server.storage", target="swap", action="swappiness", value=body.value)
    return StepsOut(steps=steps)


__all__ = ["router", "swap_router"]
