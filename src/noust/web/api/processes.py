# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The processes an application runs beside its web process (3.4, checklist B-67, B-68).

A client of :mod:`noust.managers.app_processes`, like ``noust app process``:
``GET`` lists them with their copies, ``PUT`` defines or changes one, ``POST
.../scale`` runs another number of copies, ``DELETE`` removes one. A change is
judged like an activation: the copies it starts must stay up, or the process is
put back as it was and the answer carries the state of each and the end of the
journal. A process runs a command as the application's account, so changing one
needs sudo mode.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.app_process_store import ProcessRecord
from noust.core.teams import scoped_store
from noust.managers.app_processes import ProcessManager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class ProcessCopy(BaseModel):
    """One copy of a process, as systemd sees it."""

    unit: str
    number: int
    state: str = Field(description="systemd's active state: active, failed, inactive...")
    sub_state: str = ""
    restarts: int = Field(default=0, description="How many times systemd restarted it")


class ProcessInfo(BaseModel):
    """A process of an application and its copies."""

    name: str
    command: str
    instances: int = Field(description="How many copies run; 0 keeps the definition and stops them")
    memory_max_mb: int | None = None
    cpu_quota_percent: int | None = None
    tasks_max: int | None = None
    source: str = Field(description="operator or repository")
    unit: str = Field(description="The template its copies are made from")
    installed: bool
    running: int = Field(description="How many copies run now")
    copies: list[ProcessCopy] = Field(default_factory=list)


class ProcessListResponse(BaseModel):
    """The processes of an application."""

    domain: str
    processes: list[ProcessInfo]
    total: int


class ProcessRequest(BaseModel):
    """The definition of a process."""

    command: str = Field(
        ...,
        min_length=1,
        description="The program and its arguments, split like a POSIX shell would and run "
        "without one",
    )
    instances: int = Field(default=1, ge=0, le=32, description="How many copies run")
    memory_max_mb: int | None = Field(default=None, description="MemoryMax of each copy")
    cpu_quota_percent: int | None = Field(default=None, description="CPUQuota of each copy")
    tasks_max: int | None = Field(default=None, description="TasksMax of each copy")


class ScaleRequest(BaseModel):
    """How many copies of a process to run."""

    instances: int = Field(..., ge=0, le=32, description="0 stops them and keeps the definition")


class ProcessChangeResponse(BaseModel):
    """What changing a process did."""

    domain: str
    name: str
    instances: int = 0
    started: list[str] = Field(default_factory=list)
    stopped: list[str] = Field(default_factory=list)
    removed: bool = False


class ProcessLogsResponse(BaseModel):
    """The journal of a process."""

    domain: str
    name: str
    logs: str
    lines: int


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _info(entry: dict[str, Any]) -> ProcessInfo:
    return ProcessInfo(**entry)


@router.get("/{domain}/processes", response_model=ProcessListResponse)
def list_processes(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ProcessListResponse:
    """
    List the processes of an application with their copies.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Each process and the state of each copy.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    found = [_info(item.to_dict()) for item in ProcessManager().status(validated)]
    return ProcessListResponse(domain=validated, processes=found, total=len(found))


@router.put("/{domain}/processes/{name}", response_model=ProcessChangeResponse)
def put_process(
    domain: str,
    name: str,
    body: ProcessRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ProcessChangeResponse:
    """
    Define a process, or change one, and run it.

    Its copies must stay up for a few seconds, as the web process must after a
    deploy; if they do not it is put back as it was and the answer says why.

    Args:
        domain: Domain of the application.
        name: The process, such as ``worker``.
        body: Its command, copies and limits.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A name, count, command or limit a unit cannot have (400).
        DeploymentError: A copy did not stay up (the process is as it was).
    """
    validated = _known(domain)
    change = ProcessManager().set_process(
        validated,
        ProcessRecord(
            name=name,
            command=body.command,
            instances=body.instances,
            memory_max_mb=body.memory_max_mb,
            cpu_quota_percent=body.cpu_quota_percent,
            tasks_max=body.tasks_max,
        ),
        actor=actor_label(session),
    )
    return ProcessChangeResponse(
        domain=change.domain,
        name=change.name,
        instances=change.instances,
        started=list(change.started),
        stopped=list(change.stopped),
    )


@router.post("/{domain}/processes/{name}/scale", response_model=ProcessChangeResponse)
def scale_process(
    domain: str,
    name: str,
    body: ScaleRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ProcessChangeResponse:
    """
    Run another number of copies of a process on this server.

    The copies that already run are not restarted.

    Args:
        domain: Domain of the application.
        name: The process.
        body: How many copies.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: There is no such process, or the count is out of range (400).
        DeploymentError: A new copy did not stay up (the count is as it was).
    """
    validated = _known(domain)
    change = ProcessManager().scale(validated, name, body.instances, actor=actor_label(session))
    return ProcessChangeResponse(
        domain=change.domain,
        name=change.name,
        instances=change.instances,
        started=list(change.started),
        stopped=list(change.stopped),
    )


@router.delete("/{domain}/processes/{name}", response_model=ProcessChangeResponse)
def delete_process(
    domain: str, name: str, session: Annotated[dict, Depends(require_elevated)]
) -> ProcessChangeResponse:
    """
    Stop a process and remove it.

    Args:
        domain: Domain of the application.
        name: The process.
        session: The authenticated, elevated session.

    Returns:
        What was done.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: There is no such process (400).
    """
    validated = _known(domain)
    change = ProcessManager().remove(validated, name, actor=actor_label(session))
    return ProcessChangeResponse(
        domain=change.domain, name=change.name, stopped=list(change.stopped), removed=True
    )


@router.get("/{domain}/processes/{name}/logs", response_model=ProcessLogsResponse)
def process_logs(
    domain: str,
    name: str,
    session: Annotated[dict, Depends(get_current_session)],
    lines: Annotated[int, Query(ge=1, le=5000)] = 100,
) -> ProcessLogsResponse:
    """
    Read the journal of a process: all its copies, interleaved.

    Args:
        domain: Domain of the application.
        name: The process.
        session: The authenticated session.
        lines: How many lines.

    Returns:
        The journal.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: There is no such process (400).
    """
    validated = _known(domain)
    text = ProcessManager().logs(validated, name, lines=lines)
    return ProcessLogsResponse(
        domain=validated, name=name, logs=text or "No logs available", lines=lines
    )
