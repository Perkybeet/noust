# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"See what it will do": the plan of an operation, as a route (item 183).

Each route here is a client of one function of :mod:`noust.deployers.plans`, as the operation it
plans is a client of the manager that applies it. A plan is a ``POST`` that changes nothing, asked
with the permission to read what the operation would change, that never asks for sudo mode and that
leaves nothing behind (no file, no row, no job). The operations that have one, and the route of each,
are in :mod:`noust.core.plans.registry`, and the OpenAPI says so in each of them
(``x-noust-plan``).

What applying the operation would ask of a person (sudo mode, a second person) is read from the
operation's own route, and not written down here again.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from noust.core.exceptions import NoustError, ValidationError
from noust.core.plans.registry import PLANNED, PlannedOperation
from noust.core.plans.shape import PlanNeeds, PlanOut
from noust.deployers.plans.certificate import plan_cert_renew
from noust.deployers.plans.delete import plan_delete
from noust.deployers.plans.docker_cleanup import plan_docker_cleanup
from noust.deployers.plans.domains import plan_add_domain, plan_remove_domain
from noust.deployers.plans.limits import plan_limits
from noust.deployers.plans.migrate import plan_migrate
from noust.deployers.plans.rollback import plan_rollback
from noust.deployers.plans.update import plan_update
from noust.web.api.auth import get_current_session
from noust.web.api.deployments import _deployment_of
from noust.web.api.deps import NoustErrorRoute, known_app, strict_domain, strict_server_name
from noust.web.api.docker_models import DockerCleanupRequest
from noust.web.api.openapi import api_routes, route_requires_elevation
from noust.web.api.plan_models import PlanOutModel, plan_body
from noust.web.api.plan_service import plan_service

router = APIRouter(route_class=NoustErrorRoute)


class UpdatePlanRequest(BaseModel):
    """
    What an update would be asked to do: the body of ``POST /api/jobs/update`` and of
    ``POST /api/apps/{domain}/deploy`` without the application, which is in the path.

    A field the plan does not use (``frozen_ok``: the plan always says whether a freeze has to be
    confirmed) is accepted and ignored, so the operation's own body can be sent as it is.
    """

    force: bool = Field(default=False, description="Rebuild even when the branch has nothing new")
    no_cache: bool = Field(default=False, description="Build from scratch; implies force")
    commit: str | None = Field(default=None, description="Deploy exactly this commit")
    tag: str | None = Field(default=None, description="Deploy the commit this tag points at")
    frozen_ok: bool = Field(default=False, description="Accepted and ignored: see above")
    image_tag: str | None = Field(
        default=None, description="Not planned yet: a plan that is given one is refused"
    )
    pull_request: int | None = Field(
        default=None, description="Not planned yet: a plan that is given one is refused"
    )


class RollbackPlanRequest(BaseModel):
    """
    The body of ``POST /api/apps/{domain}/deployments/{deployment_id}/rollback``.

    ``schema_changed_ok`` is accepted and ignored: the plan says whether going back passes a
    migration, and applying it asks for that confirmation.
    """

    schema_changed_ok: bool = Field(default=False, description="Accepted and ignored: see above")


def _needs_of(request: Request, key: str) -> tuple[bool, bool]:
    """
    Read what applying an operation asks of whoever does it, from the operation's own route.

    Args:
        request: The request, whose application holds the routes.
        key: The operation's key in the registry.

    Returns:
        Whether it asks for sudo mode, and whether a second person approves it.
    """
    from noust.core.accounts.approvals import route_requirement
    from noust.web.permissions.registry import permission_for_route

    operations: list[PlannedOperation] = [item for item in PLANNED if item.key == key]
    elevation = approval = False
    for operation in operations:
        permission = permission_for_route(operation.method, operation.path)
        approval = approval or (
            route_requirement(operation.method, operation.path, [permission] if permission else [])
            is not None
        )
        for route in api_routes(request.app.routes):
            if route.path_format == operation.path and operation.method in (route.methods or ()):
                elevation = elevation or route_requires_elevation(route)
    return elevation, approval


def _with_needs(request: Request, plan: PlanOut) -> PlanOutModel:
    """
    Args:
        request: The request.
        plan: What the operation's function said.

    Returns:
        The plan as the API sends it, with what the operation's own route asks for put in its needs.
    """
    elevation, approval = _needs_of(request, plan.operation)
    needs = PlanNeeds(
        elevation=elevation, approval=approval, confirmations=plan.needs.confirmations
    )
    return plan_body(replace(plan, needs=needs))


@router.post("/{domain}/update/plan", response_model=PlanOutModel)
def plan_app_update(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    body: UpdatePlanRequest | None = None,
) -> Any:
    """
    Say what updating an application would do, without doing it.

    The commands in the order they would run, the files they would write and the diff of each, what
    is serving now and what has come in upstream (``git ls-remote``: nothing is downloaded, and it
    has a deadline), what applying it will ask to confirm and, said as such, what a plan cannot
    know: whether the build passes, how long it takes and what the new code changes.

    Args:
        domain: The application's domain.
        request: The request.
        session: The authenticated session or token.
        body: What the update would be asked to do; an empty body is an update of the branch.

    Returns:
        The plan.

    Raises:
        ValidationError: A field the plan does not cover (``image_tag``, ``pull_request``) was
            given.
    """
    domain = known_app(domain)
    options = body or UpdatePlanRequest()
    for name in ("image_tag", "pull_request"):
        if getattr(options, name) is not None:
            raise ValidationError(
                f"The plan of an update does not cover {name} yet",
                details="Ask for the plan without it, or apply the update.",
                field=name,
            )
    if options.commit is not None and options.tag is not None:
        raise ValidationError(
            "A commit and a tag both name what to deploy",
            details="Give one of them.",
            field="tag",
        )
    plan = plan_update(
        domain,
        force=options.force,
        no_cache=options.no_cache,
        commit=options.commit,
        tag=options.tag,
    )
    return _with_needs(request, plan)


@router.post("/{domain}/deployments/{deployment_id}/rollback/plan", response_model=PlanOutModel)
def plan_app_rollback(
    domain: str,
    deployment_id: int,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    body: RollbackPlanRequest | None = None,
) -> Any:
    """
    Say what going back to a deployment would do, without doing it.

    Which version serves again and how (a release already on disk, a commit built again, a backup
    restored), that the database does not go back and whether going back passes a migration, which
    applying it will ask to confirm.

    Args:
        domain: The application's domain.
        deployment_id: The deployment to go back to.
        request: The request.
        session: The authenticated session or token.
        body: Accepted and ignored: the plan says what has to be confirmed.

    Returns:
        The plan; one that cannot be gone back to says why in ``blocked``.
    """
    domain = known_app(domain)
    _deployment_of(domain, deployment_id)
    return _with_needs(request, plan_rollback(domain, deployment_id))


@router.post("/{domain}/restart/plan", response_model=PlanOutModel)
def plan_app_restart(
    domain: str, request: Request, session: Annotated[dict, Depends(get_current_session)]
) -> Any:
    """
    Say what restarting an application would do, without doing it.

    Args:
        domain: The application's domain.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan: the units it would restart and the commands to do it.
    """
    return _with_needs(request, plan_service(known_app(domain), "restart"))


@router.post("/{domain}/stop/plan", response_model=PlanOutModel)
def plan_app_stop(
    domain: str, request: Request, session: Annotated[dict, Depends(get_current_session)]
) -> Any:
    """
    Say what stopping an application would do, without doing it.

    Args:
        domain: The application's domain.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan: the units it would stop and the commands to do it.
    """
    return _with_needs(request, plan_service(known_app(domain), "stop"))


@router.post("/{domain}/start/plan", response_model=PlanOutModel)
def plan_app_start(
    domain: str, request: Request, session: Annotated[dict, Depends(get_current_session)]
) -> Any:
    """
    Say what starting an application would do, without doing it.

    Args:
        domain: The application's domain.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan: the units it would start and the commands to do it.
    """
    return _with_needs(request, plan_service(known_app(domain), "start"))


class LimitsPlanRequest(BaseModel):
    """The body of ``PATCH /api/apps/{domain}/limits``: the three limits, set together."""

    memory_max_mb: int | None = Field(default=None, description="MemoryMax, in MB; null: no limit")
    cpu_quota_percent: int | None = Field(default=None, description="CPUQuota; null: no limit")
    tasks_max: int | None = Field(default=None, description="TasksMax; null: no limit")
    restart: bool = Field(default=False, description="Restart now, behind the health check")


@router.post("/{domain}/limits/plan", response_model=PlanOutModel)
def plan_app_limits(
    domain: str,
    body: LimitsPlanRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> Any:
    """
    Say what giving an application these limits would do, without doing it.

    Args:
        domain: The application's domain.
        body: The limits, as ``PATCH /api/apps/{domain}/limits`` takes them.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan: the unit it would rewrite, with the diff of the file, and the restart when asked.

    Raises:
        ValidationError: A limit is out of range (400, with the range).
    """
    plan = plan_limits(
        known_app(domain),
        memory_max_mb=body.memory_max_mb,
        cpu_quota_percent=body.cpu_quota_percent,
        tasks_max=body.tasks_max,
        restart=body.restart,
    )
    return _with_needs(request, plan)


class DomainPlanRequest(BaseModel):
    """The body of ``POST /api/apps/{domain}/domains``; ``domain`` is the name to add."""

    domain: str = Field(description="The name to add")
    kind: str = Field(default="alias", description="alias or redirect")
    force: bool = False
    port: int | None = None
    dns: bool = True
    certificate: str | None = None
    challenge: str | None = None
    dns_provider: str | None = None


@router.post("/{domain}/domains/plan", response_model=PlanOutModel)
def plan_app_domain_add(
    domain: str,
    body: DomainPlanRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> Any:
    """
    Say what making an application answer on another name would do, without doing it.

    Args:
        domain: The application's primary domain.
        body: The name and how, as ``POST /api/apps/{domain}/domains`` takes them.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan: the site it would rewrite and the web server it would reload.
    """
    plan = plan_add_domain(
        known_app(domain),
        strict_server_name(body.domain),
        kind=body.kind,
        force=body.force,
        dns=body.dns,
        port=body.port,
        certificate=body.certificate,
        challenge=body.challenge,
        dns_provider=body.dns_provider,
    )
    return _with_needs(request, plan)


@router.post("/{domain}/domains/{name}/plan", response_model=PlanOutModel)
def plan_app_domain_remove(
    domain: str,
    name: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> Any:
    """
    Say what stopping an application answering on a name would do, without doing it.

    Args:
        domain: The application's primary domain.
        name: The alias or redirect to remove.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan: the site it would rewrite and the web server it would reload.
    """
    return _with_needs(request, plan_remove_domain(known_app(domain), strict_server_name(name)))


class DeletePlanRequest(BaseModel):
    """The query parameters of ``DELETE /api/apps/{domain}`` and the body of ``POST /api/jobs/delete``."""

    remove_files: bool = Field(default=False, description="Also remove the application directory")
    remove_ssl: bool = Field(default=False, description="Also remove its certificate")
    remove_volumes: bool = Field(default=False, description="Also remove its Docker volumes")
    remove_databases: bool = Field(default=False, description="Also drop its own databases")
    remove_adopted_directory: str | None = Field(
        default=None, description="The directory of an adopted stack, named to remove it"
    )


@router.post("/{domain}/delete/plan", response_model=PlanOutModel)
def plan_app_delete(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    body: DeletePlanRequest | None = None,
) -> Any:
    """
    Say what deleting an application would delete and what it would leave, without doing it.

    Args:
        domain: The application's domain.
        request: The request.
        session: The authenticated session or token.
        body: What to remove besides the application itself; nothing else by default.

    Returns:
        The plan: what it would stop, remove and rewrite, and in its findings what stays.
    """
    options = body or DeletePlanRequest()
    plan = plan_delete(
        known_app(domain),
        remove_files=options.remove_files,
        remove_ssl=options.remove_ssl,
        remove_volumes=options.remove_volumes,
        remove_databases=options.remove_databases,
        remove_adopted_directory=options.remove_adopted_directory,
    )
    return _with_needs(request, plan)


certs_router = APIRouter(route_class=NoustErrorRoute)


class RenewPlanRequest(BaseModel):
    """The body of ``POST /api/certs/{domain}/renew``."""

    force: bool = Field(default=False, description="Renew although it is not near its expiry")


@certs_router.post("/{domain}/renew/plan", response_model=PlanOutModel)
def plan_certificate_renewal(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    body: RenewPlanRequest | None = None,
) -> Any:
    """
    Say what renewing a certificate would do, without doing it.

    Args:
        domain: The certificate's name.
        request: The request.
        session: The authenticated session or token.
        body: Renewal options, as ``POST /api/certs/{domain}/renew`` takes them.

    Returns:
        The plan: the ``certbot`` commands it would run. Whether the authority accepts the renewal
        is said as what a plan cannot know.

    Raises:
        HTTPException: 404 when no such certificate exists.
    """
    name = strict_domain(domain)
    try:
        plan = plan_cert_renew(name, force=bool(body and body.force))
    except NoustError as exc:
        if exc.message.startswith("Certificate not found"):
            raise HTTPException(status_code=404, detail=f"Certificate not found: {name}") from exc
        raise
    return _with_needs(request, plan)


class MigratePlanRequest(BaseModel):
    """The body of ``POST /api/apps/{domain}/migrate``."""

    persist: list[str] | None = Field(
        default=None,
        description="Paths to keep in shared/, relative to the application. Omitted: what git "
        "does not track, or the usual upload directories",
    )


@router.post("/{domain}/migrate/plan", response_model=PlanOutModel)
def plan_app_migration(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
    body: MigratePlanRequest | None = None,
) -> Any:
    """
    Say what moving an application onto releases would do, in the one shape of every plan.

    ``GET /api/apps/{domain}/migrate/plan`` keeps its own answer, the paths and the rewrites of the
    move; this is the same plan as the operation's rehearsal says it, with what it cannot know.

    Args:
        domain: The application's domain.
        request: The request.
        session: The authenticated session or token.
        body: Paths to keep in ``shared/``, when the detection is not wanted.

    Returns:
        The plan.

    Raises:
        HTTPException: 409 when the application is on releases already.
        ValidationError: A path is not inside the application.
    """
    from noust.web.api.apps import _migratable

    app = _migratable(known_app(domain))
    plan = plan_migrate(app.domain, body.persist if body else None)
    return _with_needs(request, plan)


docker_router = APIRouter(route_class=NoustErrorRoute)


@docker_router.post("/cleanup/plan", response_model=PlanOutModel)
def plan_docker_cleanup_route(
    body: DockerCleanupRequest,
    request: Request,
    session: Annotated[dict, Depends(get_current_session)],
) -> Any:
    """
    Say what applying a cleanup plan, as it was shown, would run, without running it.

    ``GET /api/docker/cleanup/plan`` says what would go and what stays; this says which commands
    removing what was chosen would run, through the gate that judges whose each thing is.

    Args:
        body: The token of the plan that was shown, the ids chosen and the volume names typed.
        request: The request.
        session: The authenticated session or token.

    Returns:
        The plan; a plan that is no longer the one that was shown says it would be refused.
    """
    return _with_needs(
        request,
        plan_docker_cleanup(body.token, body.select, confirm_names=body.confirm_names),
    )
