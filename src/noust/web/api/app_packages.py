# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/apps/{domain}/packages``: the system packages an application needs on the host.

A client of :class:`~noust.managers.app_packages.AppPackages`, which ``noust app packages``
calls too. ``GET`` says what an application declares and what the host has of it, ``PUT``
replaces the list (recording it, installing nothing), and ``POST .../install`` queues the job
that installs what is missing. The next update installs it as well, before it builds.

Writing a name makes root run the package manager, so ``PUT`` and ``POST`` are root-equivalent
(and ask for sudo mode), exactly like writing an application's deploy hooks; a team's role never
holds that permission. The names are validated and judged against ``security.app_packages`` in
the manager, not here (rule 4).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.deployers.recorder import CapturingLogger
from noust.managers.app_packages import MAX_PACKAGES, AppPackages, PackageState
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    JobAcceptedResponse,
    NoustErrorRoute,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label
from noust.web.jobs import JobContext, JobType, get_job_manager

router = APIRouter(route_class=NoustErrorRoute)


class PackagesRequest(BaseModel):
    """The packages an application needs."""

    packages: list[str] = Field(
        default_factory=list,
        max_length=MAX_PACKAGES,
        description="Distribution package names, one word each; empty forgets the list",
    )


class PackagesResponse(BaseModel):
    """What an application declares and what the host has of it."""

    domain: str
    declared: list[str] = Field(description="The packages declared, in order")
    installed: list[str] = Field(description="Those the host has")
    missing: list[str] = Field(description="Those it lacks: the next update installs them")
    refused: list[str] = Field(description="Those the server's policy no longer allows")
    policy: list[str] = Field(
        description="security.app_packages: the names and globs applications may declare; "
        "empty allows any package"
    )
    manager: str = Field(description="The host's package manager: apt, dnf, zypper or none")
    supported: bool = Field(description="Whether Noust installs packages on this host")
    updated_at: str | None = Field(description="When the list last changed")
    updated_by: str | None = Field(description="Who changed it")


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one the principal cannot see.

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


def _response(state: PackageState) -> PackagesResponse:
    """
    Args:
        state: What the manager says.

    Returns:
        The API model.
    """
    data: dict[str, Any] = state.to_dict()
    return PackagesResponse(**data)


@router.get("/{domain}/packages", response_model=PackagesResponse)
def get_packages(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PackagesResponse:
    """
    Say what an application declares and what the host has of it. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Its packages.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _response(AppPackages().state(_known(domain)))


@router.put("/{domain}/packages", response_model=PackagesResponse)
def put_packages(
    domain: str,
    body: PackagesRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> PackagesResponse:
    """
    Declare the packages an application needs, replacing its list. Installs nothing.

    The next update installs what is missing before it builds, or ``POST .../install`` does it
    now. A name that is not a package name, or that ``security.app_packages`` does not allow,
    refuses the whole list.

    Args:
        domain: Domain of the application.
        body: The names.
        session: The authenticated, elevated session.

    Returns:
        Its packages as they are now.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A name is not acceptable, or the application is a preview or runs in
            containers (400, with the field).
        AppBusyError: Another operation is running on the application (409).
    """
    validated = _known(domain)
    change = AppPackages().set(validated, body.packages, actor=actor_label(session), install=False)
    return _response(change.state)


def install_packages_job(
    domain: str, actor: str, job_context: JobContext | None = None
) -> dict[str, Any]:
    """
    Install the packages an application declares that the host lacks, as a background job.

    Args:
        domain: Domain of the application.
        actor: Who asked, for the audit trail.
        job_context: Injected by the job manager.

    Returns:
        What was installed.

    Raises:
        DeploymentError: The package manager failed, or does not install on this host.
    """
    logger = CapturingLogger(verbose=False)
    if job_context is not None:
        job_context.set_metadata("domain", domain)
        job_context.update("Installing the system packages", 10)
        logger.attach_sink(job_context.log)
    change = AppPackages().install(domain, actor=actor, logger=logger)
    if job_context is not None:
        job_context.update("Done", 100)
    return {
        "domain": domain,
        "installed": list(change.installed_now),
        "missing": list(change.state.missing),
    }


@router.post("/{domain}/packages/install", response_model=JobAcceptedResponse, status_code=202)
def install_packages(
    domain: str,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the installation of the packages an application declares and the host lacks.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.

    Returns:
        The queued job; its log carries the package manager's own output.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    actor = actor_label(session)
    job = get_job_manager().create_job(
        job_type=JobType.CUSTOM,
        name=f"System packages for {validated}",
        description=f"Installing the system packages {validated} needs",
        func=install_packages_job,
        kwargs={"domain": validated, "actor": actor},
        metadata={"domain": validated},
        actor=actor,
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Installing the system packages {validated} needs",
        job=job.to_dict(),
    )
