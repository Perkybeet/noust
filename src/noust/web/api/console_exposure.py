# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console on a public name, over HTTP: ``/api/console/exposure``.

A thin layer over :class:`noust.managers.console_exposure.ConsoleExposure`
(rule 3): reading the state, previewing what an exposure would do, publishing
and unpublishing. Publishing and unpublishing are jobs, because they wait on a
certificate authority, and ask for sudo mode: they are the most that can be done
to how the console is reached. They restart the console, which is answering
the request, so the restart is scheduled a few seconds ahead instead of made in
the middle of it.

The permission is ``server.host_access``, the one that changes how the host is
reached: a central holds it on a node only where that node allowed host access,
so a central can never publish a node's console on its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.auth import actor_label, require_auth
from noust.web.jobs import JobContext, JobType, get_job_manager

if TYPE_CHECKING:
    from noust.managers.console_exposure import ConsoleExposure

router = APIRouter(route_class=NoustErrorRoute)


class ExposeRequest(BaseModel):
    """
    Body of ``POST /api/console/exposure``.

    Attributes:
        domain: The public name, already pointing at this server.
        allow_ips: Addresses and networks allowed to connect; empty allows everyone.
        force_ens: Publish although the ENS profile is on. The caller confirmed
            it in so many words; it needs an allow-list and is audited.
        skip_dns_check: Do not require the name to resolve to this server's
            addresses (a server behind NAT).
    """

    domain: str = Field(min_length=1, max_length=253)
    allow_ips: list[str] = Field(default_factory=list, max_length=64)
    force_ens: bool = False
    skip_dns_check: bool = False


class ExposurePlanResponse(BaseModel):
    """
    Response of ``POST /api/console/exposure/preview``.

    Attributes:
        domain: The name, normalised.
        steps: What publishing would do, in order. Every refusal raised instead.
    """

    domain: str
    steps: list[str]


def _manager() -> ConsoleExposure:
    """
    Build the exposure manager.

    Returns:
        A :class:`~noust.managers.console_exposure.ConsoleExposure`.
    """
    from noust.managers.console_exposure import ConsoleExposure

    return ConsoleExposure()


def expose_job(
    domain: str,
    allow_ips: list[str],
    force_ens: bool,
    skip_dns_check: bool,
    actor: str,
    job_context: JobContext,
) -> dict[str, Any]:
    """
    Publish the console, as a job.

    Args:
        domain: The public name.
        allow_ips: The allow-list.
        force_ens: The ENS override.
        skip_dns_check: Skip the DNS check.
        actor: Who asked.
        job_context: The job's handle.

    Returns:
        The outcome, as :meth:`ExposureResult.to_dict`.
    """
    job_context.update("Checking", 5)
    result = _manager().expose(
        domain,
        allow_ips=allow_ips,
        force_ens=force_ens,
        skip_dns_check=skip_dns_check,
        restart="later",
        actor=actor,
        progress=job_context.log,
    )
    job_context.log(f"The console is published at {result.url}", "success")
    return result.to_dict()


def unexpose_job(job_context: JobContext) -> dict[str, Any]:
    """
    Take the console off its public name, as a job.

    Args:
        job_context: The job's handle.

    Returns:
        The outcome, as :meth:`ExposureResult.to_dict`.
    """
    job_context.update("Removing", 5)
    result = _manager().unexpose(restart="later", progress=job_context.log)
    for item in result.removed:
        job_context.log(f"Removed {item}", "warning" if item.startswith("NOT removed") else "info")
    return result.to_dict()


@router.get("/exposure")
def get_exposure(session: Annotated[dict[str, Any], Depends(require_auth)]) -> dict[str, Any]:
    """
    Say whether the console is published, where, and whether it is still in place.

    Args:
        session: The authenticated session.

    Returns:
        ``exposed``, the recorded exposure when there is one, ``ens_allows``
        (whether the ENS profile lets it be published) and ``problems``.
    """
    return dict(_manager().status())


@router.post("/exposure/preview", response_model=ExposurePlanResponse)
def preview_exposure(
    body: ExposeRequest, session: Annotated[dict[str, Any], Depends(require_auth)]
) -> ExposurePlanResponse:
    """
    Run every check publishing runs and say what it would do, changing nothing.

    Args:
        body: The same request publishing takes.
        session: The authenticated session.

    Returns:
        The steps. A refusal (the ENS profile, an account without a second
        factor, a name that does not point here) is the error's own message.
    """
    from noust.managers.console_exposure import normalise_domain

    steps = _manager().plan(
        body.domain,
        allow_ips=body.allow_ips,
        force_ens=body.force_ens,
        skip_dns_check=body.skip_dns_check,
    )
    return ExposurePlanResponse(domain=normalise_domain(body.domain), steps=steps)


@router.post("/exposure", response_model=JobAcceptedResponse, status_code=202)
def expose_console(
    body: ExposeRequest, session: Annotated[dict[str, Any], Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Publish the console on a name of its own, over TLS.

    The checks run before the job is queued, so a refusal is the response and
    not a failed job: the ENS profile, an account without a second factor, a
    name that is an application's or does not point at this server.

    Args:
        body: The name and who may connect.
        session: The authenticated session in sudo mode.

    Returns:
        The queued job.
    """
    manager = _manager()
    manager.plan(
        body.domain,
        allow_ips=body.allow_ips,
        force_ens=body.force_ens,
        skip_dns_check=body.skip_dns_check,
    )
    job = get_job_manager().create_job(
        job_type=JobType.SITE_ACTION,
        name=f"Publish the console at {body.domain}",
        description="Writing the nginx site, obtaining a certificate and restarting the console",
        func=expose_job,
        kwargs={
            "domain": body.domain,
            "allow_ips": list(body.allow_ips),
            "force_ens": body.force_ens,
            "skip_dns_check": body.skip_dns_check,
            "actor": actor_label(session),
        },
        metadata={"kind": "console_expose", "console_domain": body.domain},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Publishing the console queued",
        job=job.to_dict(),
    )


@router.delete("/exposure", response_model=JobAcceptedResponse, status_code=202)
def unexpose_console(
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Take the console off its public name, removing exactly what publishing made.

    Args:
        session: The authenticated session in sudo mode.

    Returns:
        The queued job.
    """
    job = get_job_manager().create_job(
        job_type=JobType.SITE_ACTION,
        name="Unpublish the console",
        description="Removing the nginx site and what publishing the console changed",
        func=unexpose_job,
        metadata={"kind": "console_unexpose"},
        actor=actor_label(session),
    )
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message="Unpublishing the console queued",
        job=job.to_dict(),
    )
