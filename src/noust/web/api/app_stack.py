# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose stack's own settings (3.2): the copy before an update, and workers.

A client of the functions the commands call, like every endpoint:

- ``PATCH /{domain}/backup-before-update`` is ``noust app backup-before-update
  DOMAIN on|off``
  (:func:`~noust.managers.stack_databases.set_backup_before_update`, which
  refuses anything but a stack and records the change in the audit trail).
- ``GET /{domain}/headless`` says whether the stack is a worker and what
  ``noust app headless`` would offer; ``POST`` is the command
  (:func:`~noust.deployers.docker_compose.make_headless`, which refuses a
  stack that publishes a port and audits). Its site is removed only when the
  request says so, as the command only does when the operator answers yes.

- ``POST /{domain}/reclaim`` is ``noust app reclaim DOMAIN`` (item 73): a
  stack whose containers run while its unit is stopped is handed back to the
  unit, enabled and started
  (:mod:`~noust.deployers.compose_reclaim`), once a rehearsal proves the
  start recreates nothing or the operator accepts what it would.

- ``GET /{domain}/networks`` is ``noust app networks DOMAIN``: the Docker networks the stack is
  on and who else is on them, read from Docker and never changed
  (:mod:`~noust.managers.stack_networks`).

The first three need sudo mode: one switches off the copy that makes a migration
undoable, one can remove a site, and handing a stack back runs ``docker
compose up`` as root, like adopting one.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.core.update_policy_store import UpdateBackupPolicy
from noust.deployers.compose_adopt import AdoptionRefusedError
from noust.deployers.compose_reclaim import plan_reclaim, reclaim
from noust.deployers.docker_compose import headless_check, make_headless
from noust.deployers.lifecycle import set_update_backup_required, update_backup_policy
from noust.deployers.recorder import CapturingLogger
from noust.managers.stack_databases import set_backup_before_update
from noust.managers.stack_networks import stack_networks
from noust.web.api.auth import get_current_session
from noust.web.api.deps import (
    ErrorResponse,
    NoustErrorRoute,
    error_response,
    require_elevated,
    strict_domain,
)
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed at it.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


# ---------------------------------------------------------------------------
# The copy of a stack's databases before an update
# ---------------------------------------------------------------------------


class BackupBeforeUpdateRequest(BaseModel):
    """Switch the copy of a stack's databases before an update on or off."""

    enabled: bool = Field(description="Dump the stack's databases before each update")


class BackupBeforeUpdateResponse(BaseModel):
    """Whether an update copies a stack's databases first, and what it was."""

    domain: str
    backup_before_update: bool
    previous: bool


@router.patch("/{domain}/backup-before-update", response_model=BackupBeforeUpdateResponse)
def patch_backup_before_update(
    domain: str,
    body: BackupBeforeUpdateRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> BackupBeforeUpdateResponse:
    """
    Switch the copy of a Docker Compose stack's databases before an update.

    On by default: an update dumps each database the stack runs into the
    backup it takes first, and stops when a dump fails, so a migration can
    always be undone. Off for a database backed up another way.

    Args:
        domain: Domain of the application.
        body: The setting.
        session: The authenticated, elevated session.

    Returns:
        The setting now and what it was.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
        ValidationError: It is not a Docker Compose stack (400).
    """
    validated = _known(domain)
    previous = set_backup_before_update(validated, body.enabled)
    return BackupBeforeUpdateResponse(
        domain=validated, backup_before_update=body.enabled, previous=previous
    )


# ---------------------------------------------------------------------------
# A backup the update cannot go without
# ---------------------------------------------------------------------------


class BackupRequiredRequest(BaseModel):
    """Choose whether an update is refused when its backup fails."""

    required: bool | None = Field(
        description="True or false to choose; null follows the default again "
        "(required for an application with a database)"
    )


class BackupRequiredResponse(BaseModel):
    """Whether an application's update needs its backup, and why."""

    domain: str
    required: bool = Field(description="An update is refused when its backup fails")
    explicit: bool = Field(description="An operator chose it; false means the default")
    default_required: bool = Field(description="What the default is for this application")
    databases: list[str] = Field(
        default_factory=list, description="The databases that make the default what it is"
    )


def _required_response(policy: UpdateBackupPolicy) -> BackupRequiredResponse:
    """
    Translate the policy to its API model.

    Args:
        policy: The policy.

    Returns:
        The model.
    """
    return BackupRequiredResponse(
        domain=policy.domain,
        required=policy.required,
        explicit=policy.explicit,
        default_required=policy.default_required,
        databases=list(policy.databases),
    )


@router.get("/{domain}/backup-required", response_model=BackupRequiredResponse)
def get_backup_required(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> BackupRequiredResponse:
    """
    Say whether an update of an application is refused when its backup fails.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The setting, whether it is the default, and the databases behind it.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
    """
    return _required_response(update_backup_policy(_known(domain)))


@router.put("/{domain}/backup-required", response_model=BackupRequiredResponse)
def put_backup_required(
    domain: str,
    body: BackupRequiredRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> BackupRequiredResponse:
    """
    Choose whether an update of an application is refused when its backup fails.

    On, a backup that fails (the database dumps included) stops the update
    before anything changes. Off, the update goes on with a warning. Null
    returns to the default: on for an application with a database. Turning it
    off removes the guarantee that a migration can be undone, so it needs sudo
    mode.

    Args:
        domain: Domain of the application.
        body: The choice.
        session: The authenticated, elevated session.

    Returns:
        The setting now in force.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
    """
    validated = _known(domain)
    policy = set_update_backup_required(validated, body.required, actor=actor_label(session))
    return _required_response(policy)


# ---------------------------------------------------------------------------
# A worker registered as a web
# ---------------------------------------------------------------------------


class HeadlessCheckResponse(BaseModel):
    """Whether a stack is a worker, and what clearing its port would offer."""

    domain: str
    headless: bool = Field(description="It is a Compose stack that publishes no port")
    recorded_port: int | None = Field(
        default=None, description="The port recorded for it, which nothing listens on"
    )
    site_retirable: bool = Field(
        description="Its site was written by Noust and answers no other name, so it can go too"
    )


class HeadlessRequest(BaseModel):
    """Record a stack that publishes no port as a worker."""

    remove_site: bool = Field(
        default=False,
        description="Also remove the site Noust wrote for it. Never implied: a worker serves "
        "nothing, but removing a site is the operator's call",
    )


class HeadlessResponse(BaseModel):
    """What recording a stack as a worker did."""

    domain: str
    previous_port: int | None = None
    site: Literal["removed", "kept", "kept_operator", "kept_aliases", "absent"]


@router.get("/{domain}/headless", response_model=HeadlessCheckResponse)
def get_headless(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> HeadlessCheckResponse:
    """
    Say whether a stack is a worker that still has a port recorded.

    Reads the compose file and the site; nothing is changed or run.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The check.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
    """
    check = headless_check(_known(domain))
    return HeadlessCheckResponse(
        domain=check.domain,
        headless=check.headless,
        recorded_port=check.recorded_port,
        site_retirable=check.site_retirable,
    )


@router.post("/{domain}/headless", response_model=HeadlessResponse)
def post_headless(
    domain: str,
    session: Annotated[dict, Depends(require_elevated)],
    body: HeadlessRequest | None = None,
) -> HeadlessResponse:
    """
    Record a Docker Compose stack that publishes no port as a worker.

    Its port is cleared, so every health reader judges it by its containers.
    Its site is removed only when ``remove_site`` says so, Noust wrote it and
    it answers no other name.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.
        body: Whether to remove its site too.

    Returns:
        The port cleared and what became of the site.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
        ValidationError: It is not a Compose stack, or it publishes a port (400).
        AppBusyError: Another operation is running on it (409).
    """
    validated = _known(domain)
    change = make_headless(
        validated,
        remove_site=body.remove_site if body is not None else False,
        logger=CapturingLogger(verbose=False),
    )
    return HeadlessResponse(
        domain=change.domain, previous_port=change.previous_port, site=change.site
    )


# ---------------------------------------------------------------------------
# A stack running outside its unit, handed back (item 73)
# ---------------------------------------------------------------------------


class ReclaimRequest(BaseModel):
    """Hand a stack that runs outside its unit back to it, or preview doing so."""

    accept_recreate: bool = Field(
        default=False,
        description="Hand it back even when starting the unit would recreate a container",
    )
    preview: bool = Field(default=False, description="Answer what would be done; change nothing")


class ReclaimResponse(BaseModel):
    """What handing a stack back found, and whether it was done."""

    domain: str
    unit: str = Field(description="The unit enabled and started")
    project: str | None = Field(description="The Compose project the unit addresses")
    containers: list[str] = Field(description="Its containers running now, outside the unit")
    enabled: bool = Field(description="Whether the unit was already enabled at boot")
    dry_run: str = Field(description="docker compose up --dry-run's output, verbatim")
    changes: list[str] = Field(description="What starting the unit would change (accepted)")
    reclaimed: bool = Field(description="False for a preview or a rehearsal")


@router.post(
    "/{domain}/reclaim",
    response_model=ReclaimResponse,
    responses={409: {"model": ErrorResponse, "description": "Starting it would recreate"}},
)
def post_reclaim(
    domain: str,
    session: Annotated[dict, Depends(require_elevated)],
    body: ReclaimRequest | None = None,
) -> Any:
    """
    Hand a Docker Compose stack that runs outside its unit back to it.

    ``noust app reclaim``: the unit is enabled and started, which runs
    ``docker compose up -d`` over containers already running. A rehearsal
    proves first that it would recreate nothing; when it would, the answer is
    409 with Compose's own output, unless ``accept_recreate`` says the
    operator read it. ``preview`` answers what was found and changes nothing.
    The ``app`` event follows from the middleware, as for every mutation of
    an application.

    Args:
        domain: Domain of the application.
        session: The authenticated, elevated session.
        body: Whether to accept a recreate, and whether only to preview.

    Returns:
        What was found and done; 409 with Compose's output when starting the
        unit would recreate something that was not accepted.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
        DeploymentError: It is not a stack, its unit is missing or already
            running, or none of its containers runs (400).
        ServiceError: systemd refused to enable or start the unit.
        AppBusyError: Another operation is running on it (409).
    """
    validated = _known(domain)
    wanted = body if body is not None else ReclaimRequest()
    try:
        plan = plan_reclaim(validated, accept_recreate=wanted.accept_recreate)
    except AdoptionRefusedError as exc:
        # The stack is not in the state the request assumed: the operator
        # reads Compose's output and resolves it or accepts it.
        refused: JSONResponse = error_response(exc)
        refused.status_code = 409
        return refused
    if wanted.preview:
        return ReclaimResponse(**plan.to_dict(), reclaimed=False)
    return ReclaimResponse(**reclaim(plan).to_dict())


# ---------------------------------------------------------------------------
# The Docker networks of a stack
# ---------------------------------------------------------------------------


class NetworkMemberModel(BaseModel):
    """
    A container on a network.

    Attributes:
        name: Its name.
        address: Its address on the network.
        own: It is one of the stack's own containers.
    """

    name: str
    address: str = ""
    own: bool


class StackNetworkModel(BaseModel):
    """
    One network a stack is on, or defines.

    Attributes:
        name: Its Docker name.
        id: Its id, shortened.
        logical: Its name in the compose file.
        driver: ``bridge``, ``overlay``, ``host``...
        scope: ``local`` or ``swarm``.
        internal: Containers on it cannot reach the outside.
        ipv6: IPv6 is enabled on it.
        subnets: Its address ranges.
        owner: The Compose project that made it.
        shared: Containers that are not the stack's are on it.
        members: The containers on it.
        findings: What is worth knowing about it, as Docker reported it.
    """

    name: str
    id: str
    logical: str = ""
    driver: str
    scope: str = ""
    internal: bool
    ipv6: bool
    subnets: list[str]
    owner: str = ""
    shared: bool
    members: list[NetworkMemberModel]
    findings: list[str]


class StackNetworksResponse(BaseModel):
    """
    The networks of an application.

    Attributes:
        app: The application's domain.
        applicable: It is a stack Docker runs.
        reason: Why not, when it is not.
        containers: The names of the stack's containers.
        isolated: Every network is the stack's own and nothing else is on it.
        networks: The networks.
    """

    app: str
    applicable: bool
    reason: str = ""
    containers: list[str]
    isolated: bool
    networks: list[StackNetworkModel]


@router.get("/{domain}/networks", response_model=StackNetworksResponse)
def get_networks(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> StackNetworksResponse:
    """
    Show the Docker networks a stack is on and who else is on them.

    Read from Docker; nothing is created, joined or removed.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The networks, with their members and what is worth knowing about each.

    Raises:
        HTTPException: 404 when nothing is deployed at the domain.
    """
    app = scoped_store().get_app(_known(domain))
    if app is None:  # pragma: no cover - _known just proved it
        raise HTTPException(status_code=404, detail=f"Application not found: {domain}")
    return StackNetworksResponse(**stack_networks(app).to_dict())
