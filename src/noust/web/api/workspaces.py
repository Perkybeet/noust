# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A monorepo as one application per workspace.

A client of :mod:`noust.deployers.workspaces`, like ``noust workspace``. ``POST
/api/workspaces/inspect`` reads a repository's workspaces and says which are applications;
``POST /api/workspaces/deploy`` queues the creation of one application per workspace it is
given a domain for, each through the import path (``POST /api/apps/import``): a deploy job on the
releases layout with the workspace as its base directory and its dependencies as watch paths.

The documents are made here, from what the repository says; a client names workspaces and
domains and never sends a build block, because a build block is code that runs as the
application.
"""

from __future__ import annotations

import threading
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.deployers.app_export import plan_import
from noust.deployers.workspaces import (
    WorkspaceReport,
    inspect_workspaces,
    workspace_document,
)
from noust.web.api.app_export import queue_import
from noust.web.api.apps import _require_local_source_privilege, _run_until_disconnected
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated, strict_domain

router = APIRouter(route_class=NoustErrorRoute)

#: How many applications one request creates.
MAX_APPS = 20


class WorkspaceInspectRequest(BaseModel):
    """A repository to read the workspaces of."""

    source: str = Field(..., description="Git URL, archive URL or local path")
    branch: str | None = Field(default=None, description="Git branch to read")


class WorkspaceEntry(BaseModel):
    """One package of the monorepo."""

    name: str
    directory: str
    app_type: str | None = Field(
        description="The Noust type that would deploy it; null for a library"
    )
    deployable: bool
    reason: str = Field(description="Why it is not an application; empty for one")
    depends_on: list[str]
    watch_paths: list[str] = Field(description="What a push must touch to deploy it")
    build: list[str] | None = Field(description="Its build command, when Turborepo builds it")


class WorkspacesResponse(BaseModel):
    """What a repository's workspaces are."""

    tool: str = Field(description="turborepo, nx, lerna, pnpm, yarn, npm or none")
    package_manager: str
    workspaces: list[WorkspaceEntry]
    problems: list[str]


class WorkspaceApp(BaseModel):
    """One application to create."""

    workspace: str = Field(description="The workspace's name or its directory")
    domain: str = Field(description="The domain to deploy it on")


class WorkspaceDeployRequest(BaseModel):
    """Applications to create from a monorepo."""

    source: str = Field(..., description="Git URL, archive URL or local path")
    branch: str | None = None
    apps: list[WorkspaceApp] = Field(description="The workspaces to deploy, each with its domain")


class WorkspaceDeployResponse(BaseModel):
    """The jobs that create the applications, in the order they were asked."""

    jobs: list[JobAcceptedResponse]


async def _report(
    request: Request, session: dict, source: str, branch: str | None
) -> WorkspaceReport:
    """
    Read a repository's workspaces, stopping if the client goes away.

    Args:
        request: The request, whose client is watched.
        session: The authenticated session.
        source: The repository.
        branch: The branch, or None.

    Returns:
        What was found.

    Raises:
        HTTPException: 403 for a local path the credential may not read.
        SourceError: The source is invalid or could not be fetched.
    """
    cancel = threading.Event()

    def work() -> WorkspaceReport:
        _require_local_source_privilege(request, session, source)
        return inspect_workspaces(source, branch=branch, cancel=cancel)

    return await _run_until_disconnected(request, cancel, work)


@router.post("/inspect", response_model=WorkspacesResponse)
async def inspect_workspace_source(
    body: WorkspaceInspectRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> WorkspacesResponse:
    """
    Read a repository's workspaces and say which are applications.

    Fetches only the configuration files and the ``package.json`` of each workspace; nothing
    is run and nothing is written outside the scratch checkout, which is removed before this
    returns.

    Args:
        body: The repository and the branch.
        request: The incoming request.
        session: The authenticated session.

    Returns:
        The workspaces: ``tool`` is ``none`` for a repository that declares none.

    Raises:
        HTTPException: 403 for a local path the credential may not read.
        SourceError: The source is invalid or could not be fetched (400).
    """
    report = await _report(request, session, body.source, body.branch)
    return WorkspacesResponse(**report.to_dict())


@router.post("/deploy", response_model=WorkspaceDeployResponse, status_code=202)
async def deploy_workspaces(
    body: WorkspaceDeployRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> WorkspaceDeployResponse:
    """
    Queue one application per workspace named.

    Everything is checked before anything is queued: the workspaces exist and are applications,
    each domain is valid and free, and every plan is one an import would accept. Sudo mode: it
    deploys as root, like an import.

    Args:
        body: The repository, and the workspaces with the domain each is deployed on.
        request: The incoming request.
        session: An elevated session.

    Returns:
        The queued jobs, in the order asked; each result lists what was applied.

    Raises:
        ValidationError: A workspace is unknown or a library, a domain repeats, or a plan is
            refused (a taken domain is a 409).
    """
    if not body.apps or len(body.apps) > MAX_APPS:
        raise ValidationError(
            f"Name between 1 and {MAX_APPS} workspaces to deploy",
            details="Each is one application with its own domain.",
            field="apps",
        )
    report = await _report(request, session, body.source, body.branch)
    if report.tool == "none" or not report.workspaces:
        raise ValidationError(
            "This repository declares no workspaces",
            details="Workspaces are listed in package.json, pnpm-workspace.yaml or lerna.json.",
            field="source",
        )
    plans = []
    seen: set[str] = set()
    for entry in body.apps:
        found = report.named(entry.workspace)
        if found is None:
            available = ", ".join(item.directory for item in report.deployable) or "none"
            raise ValidationError(
                f"There is no workspace {entry.workspace!r} in this repository",
                details=f"Applications here: {available}.",
                field="apps",
            )
        domain = strict_domain(entry.domain)
        if domain in seen:
            raise ValidationError(f"{domain} is given for two workspaces", field="apps")
        seen.add(domain)
        document = workspace_document(
            report, found, domain=domain, source=body.source, branch=body.branch
        )
        plans.append(plan_import(document, domain=domain))
    return WorkspaceDeployResponse(jobs=[queue_import(plan, request, session) for plan in plans])
