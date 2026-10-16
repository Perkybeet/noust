"""
Jobs API endpoints.

The queue itself lives in :mod:`noust.web.jobs`; this module only translates
HTTP into calls on it. Deploying a new application is not among them:
``POST /api/apps`` is the one route that queues a deployment, so there is one
implementation of "deploy" rather than two that could disagree - an earlier
``POST /api/jobs/deploy`` shelled out to the ``wasm`` binary and has been
removed in favour of it.

History is read from the store, not from the queue's own memory: a panel
restart empties the queue but the store remembers every job it ever
persisted, which is the whole point of persisting them. The in-memory queue
stays the source of truth only for what this process is doing right now
(``/active``).

Literal routes are declared before ``/{job_id}``: registered the other way
round, ``/active`` and ``/cleanup`` would be matched as job identifiers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.exceptions import CancelRefused, NoustError, ValidationError
from noust.core.store import JobRecord
from noust.core.teams import scoped_store
from noust.deployers.lifecycle import (
    NOTHING_NEW_HINT,
    check_upstream,
    require_schema_change_confirmed,
)
from noust.validators.names import validate_filename
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label
from noust.web.jobs import (
    Job,
    JobStatus,
    JobType,
    backup_app_job,
    cancel_verdict,
    cert_create_job,
    delete_app_job,
    get_job_manager,
    rollback_app_job,
    update_app_job,
    visible_state,
)
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(prefix="/jobs", tags=["jobs"], route_class=NoustErrorRoute)

#: Job identifiers are the first eight characters of a uuid4.
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{1,36}$")

#: Default number of trailing lines a job log request returns.
DEFAULT_LOG_TAIL = 500


class UpdateRequest(BaseModel):
    """Request to update an application."""

    domain: str = Field(..., description="Domain of the application to update")
    force: bool = Field(
        default=False,
        description="Rebuild even when the branch has no commit the live build lacks",
    )
    # Optional, not defaulted to False: a client that does not mean to rebuild from scratch
    # leaves it out, and the generated types then do not ask it to say so.
    no_cache: bool | None = Field(
        default=None,
        description="Build from scratch: no package manager cache, no dependencies taken from "
        "the release that serves, no image layer cache. Implies force",
    )


class DeleteRequest(BaseModel):
    """Request to delete an application."""

    domain: str = Field(..., description="Domain of the application to delete")
    remove_files: bool = Field(default=True, description="Remove application files")
    remove_ssl: bool = Field(default=True, description="Remove SSL certificates")
    remove_adopted_directory: str | None = Field(
        default=None,
        description="The application's directory, named exactly, when it is outside Noust's "
        "apps directory (an adopted stack): only then is it removed with the files",
    )


class BackupRequest(BaseModel):
    """Request to back up an application."""

    domain: str = Field(..., description="Domain of the application to back up")
    description: str = Field(default="", description="Description for the backup")


class RollbackRequest(BaseModel):
    """Request to roll an application back."""

    domain: str = Field(..., description="Domain of the application")
    backup_id: str | None = Field(default=None, description="Backup to roll back to")
    schema_changed_ok: bool = Field(
        default=False,
        description="Go back even past later deployments that changed the database schema",
    )


class CertRequest(BaseModel):
    """Request to obtain a certificate."""

    domain: str = Field(..., description="Domain for the certificate")
    email: str | None = Field(default=None, description="Registration email")
    webserver: str = Field(default="nginx", description="Certbot plugin to use")
    include_www: bool = Field(default=False, description="Also cover the www subdomain")


class JobResponse(BaseModel):
    """
    One job, as the queue records it.

    Attributes:
        deployment_id: The deployment history row a deploy or update job
            wrote, lifted out of ``result`` so a client can link to it
            without knowing which job types carry one. None for a job that
            never deployed anything, or one whose recording failed.
    """

    id: str
    type: str
    name: str
    description: str
    status: str
    progress: int
    total_steps: int
    current_step: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    logs: list[dict[str, Any]] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    actor: str | None = None
    deployment_id: int | None = None
    state: str = Field(
        default="",
        description="queued, running, succeeded, failed or cancelled: the five states of the queue",
    )
    queue_position: int | None = Field(
        default=None, description="Where it stands among the jobs that wait; 1 starts next"
    )
    waiting_for: str | None = Field(
        default=None, description="What a queued job waits for, in one sentence"
    )
    cancellable: bool = Field(
        default=False,
        description="Cancelling it now would be accepted: it waits, or it builds and has not "
        "started to activate its release",
    )
    cancel_blocked_reason: str | None = Field(
        default=None,
        description="Why a running job cannot be cancelled, when it cannot: activating (its "
        "release is being activated), builds_in_place (its application is rebuilt where it "
        "serves, or inside Docker) or unsupported (it is not a build, and is halfway through "
        "changing the system); null when it can be, or nothing is left to say",
    )
    cancel_requested: bool = Field(default=False, description="It was asked to stop and still runs")

    _iso_timestamps = iso_offset_validator("created_at", "started_at", "completed_at")


class QueueResponse(BaseModel):
    """What runs, what waits, and the limits that decide it."""

    running: list[JobResponse]
    queued: list[JobResponse]
    builds_running: int = Field(description="How many of the running jobs build an application")
    max_concurrent_builds: int = Field(description="deploy.max_concurrent_builds")
    queue_limit: int = Field(description="deploy.queue_limit: builds that may wait")
    builds_queued: int = Field(description="How many of the waiting jobs build an application")
    max_minutes: int = Field(
        default=60,
        description="deploy.max_minutes: the longest a build may take before it is stopped; "
        "0 is no limit",
    )


class JobListResponse(BaseModel):
    """Response for listing jobs."""

    jobs: list[JobResponse]
    total: int
    active: int


class JobCreatedResponse(BaseModel):
    """Response after queueing a job."""

    message: str
    job: JobResponse


class JobActionResponse(BaseModel):
    """Response for an action on a job."""

    message: str
    job_id: str | None = None
    state: str | None = Field(
        default=None,
        description="cancelled: it never started; cancelling: it runs and was asked to stop",
    )


class CleanupResponse(BaseModel):
    """Response after dropping old jobs."""

    message: str
    removed: int


class JobLogResponse(BaseModel):
    """The tail of a job's captured log."""

    content: str
    truncated: bool


def _deployment_id_of(result: dict[str, Any] | None) -> int | None:
    """
    Lift ``deployment_id`` out of a job's free-form result, when it has one.

    Only deploy and update jobs put it there
    (:func:`~noust.web.jobs.deploy_app_job`, :func:`~noust.web.jobs.run_update`);
    every other job type's result simply has no such key.

    Args:
        result: The job's result, or None for one that has not finished.

    Returns:
        The deployment id, or None.
    """
    if not isinstance(result, dict):
        return None
    deployment_id = result.get("deployment_id")
    return deployment_id if isinstance(deployment_id, int) else None


def _to_response(job: Job) -> JobResponse:
    """
    Convert a job into its API representation.

    Args:
        job: The job.

    Returns:
        The API model.
    """
    data = job.to_dict()
    cancellable, blocked = cancel_verdict(job)
    return JobResponse(
        deployment_id=_deployment_id_of(data.get("result")),
        cancellable=cancellable,
        cancel_blocked_reason=blocked,
        **data,
    )


def _from_record(record: JobRecord) -> JobResponse:
    """
    Convert a persisted job into its API representation.

    The store keeps less than the live queue does - individual log lines live
    in the job's own log file, read separately through
    :func:`get_job_log`, and ``current_step`` was never durable - so those
    fields come back empty rather than reconstructed. Everything a client
    already depends on is still there, at the same name.

    Args:
        record: The stored job.

    Returns:
        The API model.
    """
    result: dict[str, Any] | None = None
    if record.result_json:
        try:
            parsed = json.loads(record.result_json)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            result = parsed

    response = JobResponse(
        id=record.id,
        type=record.type,
        name=record.name,
        description=record.description,
        status=record.status,
        state=visible_state(record.status),
        progress=record.progress,
        total_steps=record.total_steps,
        current_step="",
        created_at=record.created_at or "",
        started_at=record.started_at,
        completed_at=record.finished_at,
        result=result,
        deployment_id=_deployment_id_of(result),
        error=record.error,
        logs=[],
        metadata={"domain": record.domain} if record.domain else {},
        actor=record.actor,
    )
    # What the queue says about a job is in this process's memory, not in the
    # store: a job that is still waiting or running here is asked.
    live = get_job_manager().get_job(record.id)
    if live is not None:
        response.queue_position = live.queue_position
        response.waiting_for = live.waiting_for
        response.cancellable, response.cancel_blocked_reason = cancel_verdict(live)
        response.cancel_requested = live.cancel_requested_by is not None
    return response


def _validated_job_id(job_id: str) -> str:
    """
    Check that a job identifier looks like one.

    Args:
        job_id: Identifier as supplied by the client.

    Returns:
        The identifier.

    Raises:
        ValidationError: When it is not a hexadecimal identifier.
    """
    if not JOB_ID_PATTERN.fullmatch(job_id):
        raise ValidationError(
            f"Invalid job id: {job_id!r}",
            details="Job ids are hexadecimal, as returned when the job was queued.",
        )
    return job_id


def _queued(message: str, job: Job) -> JobCreatedResponse:
    """
    Build the response for a freshly queued job.

    Args:
        message: Human-readable summary.
        job: The queued job.

    Returns:
        The response body.
    """
    return JobCreatedResponse(message=message, job=_to_response(job))


@router.get("", response_model=JobListResponse)
def list_jobs(
    session: Annotated[dict, Depends(get_current_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    status: Annotated[str | None, Query(description="Filter by job status")] = None,
    domain: Annotated[str | None, Query(description="Filter by domain")] = None,
) -> JobListResponse:
    """
    List recent jobs, from the store, so a panel restart does not erase history.

    Args:
        limit: Maximum number of jobs to return.
        status: Status to filter by.
        domain: Domain to filter by.
        session: The authenticated session.

    Returns:
        The jobs, newest first.

    Raises:
        ValidationError: When the status filter is not a known status.
    """
    if status:
        try:
            JobStatus(status)
        except ValueError as exc:
            raise ValidationError(
                f"Unknown job status: {status!r}",
                details=f"Use one of: {', '.join(s.value for s in JobStatus)}.",
            ) from exc

    records = scoped_store().list_jobs(limit=limit, status=status, domain=domain)

    return JobListResponse(
        jobs=[_from_record(record) for record in records],
        total=len(records),
        active=len(
            scoped_store().filter_domains(
                get_job_manager().get_active_jobs(), lambda job: job.metadata.get("domain")
            )
        ),
    )


@router.get("/active", response_model=JobListResponse)
def list_active_jobs(session: Annotated[dict, Depends(get_current_session)]) -> JobListResponse:
    """
    List queued and running jobs.

    Args:
        session: The authenticated session.

    Returns:
        The active jobs.
    """
    # What the caller may see of what is running: a job that acts on no
    # application (an update of the server itself) is the instance's.
    jobs = scoped_store().filter_domains(
        get_job_manager().get_active_jobs(), lambda job: job.metadata.get("domain")
    )
    return JobListResponse(
        jobs=[_to_response(job) for job in jobs], total=len(jobs), active=len(jobs)
    )


@router.get("/queue", response_model=QueueResponse)
def get_queue(session: Annotated[dict, Depends(get_current_session)]) -> QueueResponse:
    """
    Show what is building and what is waiting to.

    Builds of different applications run at the same time up to
    ``deploy.max_concurrent_builds``; one application builds one thing at a
    time, and the requests that arrive meanwhile wait here in order (a push
    during a deploy is queued, not refused). The queue holds
    ``deploy.queue_limit`` builds; the next is refused with 429.

    Args:
        session: The authenticated session.

    Returns:
        The running jobs, the waiting ones in the order they start, and the limits.
    """
    snapshot = get_job_manager().queue()
    scoped = scoped_store()

    def domain_of(job: Job) -> str | None:
        domain = job.metadata.get("domain")
        return domain if isinstance(domain, str) else None

    # What is building for another team is not the caller's to see, and neither is
    # how much of it there is: a team member counts its own builds.
    running = scoped.filter_domains(snapshot.running, domain_of)
    queued = scoped.filter_domains(snapshot.queued, domain_of)
    return QueueResponse(
        running=[_to_response(job) for job in running],
        queued=[_to_response(job) for job in queued],
        builds_running=len(running) if scoped.scope.restricted else snapshot.builds_running,
        max_concurrent_builds=snapshot.max_concurrent_builds,
        queue_limit=snapshot.queue_limit,
        builds_queued=len(queued) if scoped.scope.restricted else snapshot.builds_queued,
        max_minutes=snapshot.max_minutes,
    )


def refuse_if_nothing_new(domain: str) -> None:
    """
    Refuse an update that would rebuild the commit that is already live.

    The head of the branch on the remote is compared with the live commit
    (``git ls-remote``, nothing is downloaded).

    Args:
        domain: A validated domain.

    Raises:
        HTTPException: 409 ``nothing_new`` when the branch has nothing new.
    """
    upstream = check_upstream(domain)
    if upstream is not None and not upstream.has_new_commits:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "nothing_new",
                "detail": upstream.summary,
                "hint": NOTHING_NEW_HINT,
            },
        )


def queue_update(
    domain: str,
    *,
    actor: str,
    no_cache: bool = False,
    commit: str | None = None,
    tag: str | None = None,
) -> tuple[Job, bool]:
    """
    Queue an update of an application, the one way every door does.

    An update that waits already builds the branch as it is when its turn
    comes, so asking for another is the same request and joins it; one that
    asks for no cache makes the waiting one build without it. A commit or a tag
    names exactly what to deploy, so it is its own job.

    Args:
        domain: A validated domain.
        actor: Who asked, for the job's record.
        no_cache: Build from scratch.
        commit: Deploy this commit instead of the head of the branch.
        tag: Deploy the commit this tag points at.

    Returns:
        The job and whether the request joined one that was already waiting.
    """
    kwargs: dict[str, Any] = {"domain": domain}
    metadata: dict[str, Any] = {"domain": domain}
    for key, value in (("no_cache", no_cache or None), ("commit", commit), ("tag", tag)):
        if value is not None:
            kwargs[key] = value
            metadata[key] = value
    exact = commit is not None or tag is not None
    joined: list[str] = []
    job = get_job_manager().create_job(
        job_type=JobType.UPDATE,
        name=f"Update {domain}",
        description=f"Updating the application at {domain}",
        func=update_app_job,
        kwargs=kwargs,
        metadata=metadata,
        actor=actor,
        coalesce_key=None if exact else f"update:{domain}",
        merge={"no_cache": True} if no_cache and not exact else None,
        note=f"{actor} asked for the update again: merged into this one",
        on_merge=lambda waiting: joined.append(waiting.id),
    )
    return job, bool(joined)


def _queue_update(
    domain: str, force: bool, session: dict[str, Any], *, no_cache: bool = False
) -> tuple[Job, bool]:
    """
    Queue the update of one application.

    The one implementation of "queue an update": a single request and a group
    of them go through it, so they cannot refuse or merge differently.

    Args:
        domain: The application, validated.
        force: Rebuild even when the branch has no commit the live build lacks.
        session: The authenticated payload.
        no_cache: Build from scratch: no cache of any kind. Implies force.

    Returns:
        The job, and whether the request joined an update that was already waiting.

    Raises:
        HTTPException: 409 ``nothing_new`` when the branch has nothing new.
    """
    # Rebuilding without the cache is asked for to rebuild what is live.
    if not force and not no_cache:
        refuse_if_nothing_new(domain)
    return queue_update(domain, actor=actor_label(session), no_cache=no_cache)


@router.post("/update", response_model=JobCreatedResponse, status_code=202)
def create_update_job(
    request: UpdateRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobCreatedResponse:
    """
    Queue an update.

    Unless ``force`` is set, the head of the branch on the remote is compared
    with the live commit first (``ls-remote``, nothing is downloaded), and
    an update that would rebuild the same commit is refused with ``409
    nothing_new``. The webhook does not ask: a push is itself the news. ``no_cache`` rebuilds what
    is live from nothing, so it is not asked either.

    Args:
        request: The update request.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 409 ``nothing_new`` when the branch has nothing new.
    """
    job, joined = _queue_update(
        strict_domain(request.domain), request.force, session, no_cache=bool(request.no_cache)
    )
    if joined:
        return _queued(
            "An update of this application is already waiting; this request joined it", job
        )
    return _queued("Update job created", job)


#: The most applications one request updates: a group, not the whole server.
MAX_GROUP = 100


class UpdateManyRequest(BaseModel):
    """Request to update a group of applications."""

    team: str | None = Field(default=None, description="Every application of this team")
    project: str | None = Field(default=None, description="Every application of this project")
    environment: str | None = Field(
        default=None, description="Every application of this environment (staging, production...)"
    )
    domains: list[str] = Field(default_factory=list, description="These applications, by domain")
    force: bool = Field(
        default=False,
        description="Rebuild each even when its branch has no commit the live build lacks",
    )


class UpdateManyItem(BaseModel):
    """
    What became of one application of a group.

    Attributes:
        domain: The application.
        status: ``queued``, ``joined`` (an update of it was already waiting),
            or ``skipped``.
        job_id: Its job, when it has one.
        reason: Why it was skipped.
    """

    domain: str
    status: str
    job_id: str | None = None
    reason: str | None = None


class UpdateManyResponse(BaseModel):
    """
    The updates a group request queued.

    Attributes:
        message: A summary.
        items: One per application, by domain.
        queued: How many have a job.
        skipped: How many were not updated.
    """

    message: str
    items: list[UpdateManyItem]
    queued: int
    skipped: int


@router.post("/update-many", response_model=UpdateManyResponse, status_code=202)
def create_update_jobs(
    request: UpdateManyRequest, session: Annotated[dict, Depends(get_current_session)]
) -> UpdateManyResponse:
    """
    Queue the update of every application of a team, a project or an environment.

    One call for what a pipeline would otherwise loop over: each application
    gets the job ``POST /api/jobs/update`` would have queued, so each goes
    through its own health gate and a failure of one does not stop the others
    (the deploy queue runs them as ``deploy.max_concurrent_builds`` allows). An
    application the caller may not deploy, or whose branch has nothing new, is
    reported as skipped with the reason; nothing is refused for it.

    Args:
        request: Which applications: a team, a project, an environment, or domains.
        session: The authenticated session.

    Returns:
        What became of each.

    Raises:
        ValidationError: 400 when nothing says which applications, or more than
            :data:`MAX_GROUP` match.
    """
    from noust.web.permissions import Permission
    from noust.web.permissions.team_roles import permissions_at

    scoped = scoped_store()
    named = [strict_domain(domain) for domain in request.domains]
    if not (named or request.team or request.project or request.environment):
        raise ValidationError(
            "Say which applications",
            details="Name a team, a project, an environment, or the domains to update.",
            field="project",
        )
    chosen: dict[str, Any] = {}
    if request.team or request.project or request.environment:
        for app in scoped.list_apps(
            project=request.project, environment=request.environment, team=request.team
        ):
            chosen[app.domain] = app
    items: list[UpdateManyItem] = []
    for domain in named:
        found = chosen.get(domain) or scoped.get_app(domain)
        if found is None:
            items.append(
                UpdateManyItem(domain=domain, status="skipped", reason="no such application")
            )
        else:
            chosen[domain] = found
    if len(chosen) > MAX_GROUP:
        raise ValidationError(
            f"That is {len(chosen)} applications; a request updates at most {MAX_GROUP}",
            details="Narrow it to a project or an environment.",
            field="project",
        )
    for domain in sorted(chosen):
        app = chosen[domain]
        placed = scoped.placement_of_project(app.project_id, app.environment_id)
        held = (
            permissions_at(
                session,
                placed.team_id if placed else None,
                placed.project_id if placed else None,
                (placed.environment_id or None) if placed else None,
            )
            if "team_roles" in session
            else {Permission.APPS_DEPLOY}
        )
        if Permission.APPS_DEPLOY not in held:
            items.append(
                UpdateManyItem(domain=domain, status="skipped", reason="you may not deploy it")
            )
            continue
        try:
            job, joined = _queue_update(domain, request.force, session)
        except HTTPException as exc:
            detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
            if exc.status_code == 409 and detail.get("error") == "nothing_new":
                items.append(
                    UpdateManyItem(
                        domain=domain,
                        status="skipped",
                        reason=str(detail.get("detail") or "nothing new"),
                    )
                )
                continue
            raise
        items.append(
            UpdateManyItem(domain=domain, status="joined" if joined else "queued", job_id=job.id)
        )
    items.sort(key=lambda item: item.domain)
    queued = sum(1 for item in items if item.job_id)
    return UpdateManyResponse(
        message=f"Queued {queued} update(s), skipped {len(items) - queued}",
        items=items,
        queued=queued,
        skipped=len(items) - queued,
    )


@router.post("/delete", response_model=JobCreatedResponse, status_code=202)
def create_delete_job(
    request: DeleteRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobCreatedResponse:
    """
    Queue a deletion.

    Runs the same :func:`~noust.web.jobs.delete_app_job` that
    ``DELETE /api/apps/{domain}`` queues, so it is guarded the same way: a
    cookie session must have confirmed recently (see
    :func:`~noust.web.api.deps.require_elevated`), the same as any other
    irreversible action. A stale client still calling this route instead of
    the app-level one gets no less protection for it.

    Args:
        request: The deletion request.
        session: The authenticated, elevated session.

    Returns:
        The queued job.
    """
    domain = strict_domain(request.domain)
    job = get_job_manager().create_job(
        job_type=JobType.DELETE,
        name=f"Delete {domain}",
        description=f"Deleting the application at {domain}",
        func=delete_app_job,
        kwargs={
            "domain": domain,
            "remove_files": request.remove_files,
            "remove_ssl": request.remove_ssl,
            "remove_adopted_directory": request.remove_adopted_directory,
        },
        metadata={"domain": domain},
        actor=actor_label(session),
    )
    return _queued("Deletion job created", job)


@router.post("/backup", response_model=JobCreatedResponse, status_code=202)
def create_backup_job(
    request: BackupRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobCreatedResponse:
    """
    Queue a backup.

    Args:
        request: The backup request.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    domain = strict_domain(request.domain)
    job = get_job_manager().create_job(
        job_type=JobType.BACKUP,
        name=f"Backup {domain}",
        description=f"Creating a backup of {domain}",
        func=backup_app_job,
        kwargs={"domain": domain, "description": request.description},
        metadata={"domain": domain},
        actor=actor_label(session),
    )
    return _queued("Backup job created", job)


def _schema_changes_since_backup(domain: str, backup_id: str | None, confirmed: bool) -> list[int]:
    """
    Ask, before queueing, whether restoring a backup goes back past a schema change.

    The job asks again through the manager (rule 4); this only answers the
    console with the deployments while it can still confirm.

    Args:
        domain: The application, validated.
        backup_id: The backup asked for; None for the one the manager picks.
        confirmed: The operator already said yes.

    Returns:
        The deployments gone back past that changed the schema; empty when
        there is no backup to name (the job reports that).

    Raises:
        SchemaChangedError: 409 ``schema_changed`` naming them, when not confirmed.
    """
    from noust.managers.backup_manager import RollbackManager

    try:
        target = RollbackManager(verbose=False).rollback_target(domain, backup_id)
    except NoustError:
        return []
    return require_schema_change_confirmed(
        domain,
        target=f"backup {target.id}",
        schema_changed_ok=confirmed,
        since=target.created_at,
    )


@router.post("/rollback", response_model=JobCreatedResponse, status_code=202)
def create_rollback_job(
    request: RollbackRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobCreatedResponse:
    """
    Queue a rollback.

    Restoring a backup puts the files back, never the database: going back
    past later deployments that changed the schema is refused with 409
    ``schema_changed``, naming them, unless the body says ``schema_changed_ok``.

    Args:
        request: The rollback request.
        session: The authenticated session.

    Returns:
        The queued job.

    Raises:
        SchemaChangedError: 409 ``schema_changed`` when it was not confirmed.
    """
    domain = strict_domain(request.domain)
    backup_id = validate_filename(request.backup_id) if request.backup_id else None
    changed = _schema_changes_since_backup(domain, backup_id, request.schema_changed_ok)

    job = get_job_manager().create_job(
        job_type=JobType.ROLLBACK,
        name=f"Rollback {domain}",
        description=(f"Rolling back {domain}" + (f" to backup {backup_id}" if backup_id else "")),
        func=rollback_app_job,
        kwargs={
            "domain": domain,
            "backup_id": backup_id,
            # Only when it was asked and answered: a job queued without it
            # still refuses a change made between queueing and running.
            **({"schema_changed_ok": True} if changed and request.schema_changed_ok else {}),
        },
        metadata={"domain": domain, "backup_id": backup_id},
        actor=actor_label(session),
    )
    return _queued("Rollback job created", job)


@router.post("/cert", response_model=JobCreatedResponse, status_code=202)
def create_cert_job(
    request: CertRequest, session: Annotated[dict, Depends(get_current_session)]
) -> JobCreatedResponse:
    """
    Queue a certificate issuance.

    Args:
        request: The certificate request.
        session: The authenticated session.

    Returns:
        The queued job.
    """
    domain = strict_domain(request.domain)
    job = get_job_manager().create_job(
        job_type=JobType.CERT_CREATE,
        name=f"SSL for {domain}",
        description=f"Obtaining an SSL certificate for {domain}",
        func=cert_create_job,
        kwargs={
            "domain": domain,
            "email": request.email,
            "webserver": request.webserver,
            "include_www": request.include_www,
        },
        metadata={"domain": domain},
        actor=actor_label(session),
    )
    return _queued("Certificate job created", job)


@router.delete("/cleanup", response_model=CleanupResponse)
def cleanup_jobs(
    session: Annotated[dict, Depends(get_current_session)],
    max_age_hours: Annotated[int, Query(ge=1, le=168)] = 24,
) -> CleanupResponse:
    """
    Forget finished jobs older than a cutoff.

    Args:
        max_age_hours: Age above which a finished job is dropped.
        session: The authenticated session.

    Returns:
        How many jobs were dropped.
    """
    removed = get_job_manager().cleanup_old_jobs(max_age_hours=max_age_hours)
    return CleanupResponse(
        message=f"Cleaned up jobs older than {max_age_hours} hours", removed=removed
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(job_id: str, session: Annotated[dict, Depends(get_current_session)]) -> JobResponse:
    """
    Describe one job, from the store, so it is still there after a restart.

    Args:
        job_id: Job identifier.
        session: The authenticated session.

    Returns:
        The job.

    Raises:
        HTTPException: 404 when the job is unknown.
    """
    record = scoped_store().get_job(_validated_job_id(job_id))
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    return _from_record(record)


@router.get("/{job_id}/log", response_model=JobLogResponse)
def get_job_log(
    job_id: str,
    session: Annotated[dict, Depends(get_current_session)],
    tail: Annotated[int, Query(ge=1, le=20000, description="Lines to return, from the end")] = (
        DEFAULT_LOG_TAIL
    ),
) -> JobLogResponse:
    """
    Read a job's captured log.

    The log is a plain file on disk, one line per progress update or message
    the job reported, written by the job manager as the job ran; this reads
    it back rather than replaying it from memory, which is what makes it
    available after a restart.

    Args:
        job_id: Job identifier.
        tail: Maximum number of trailing lines to return.
        session: The authenticated session.

    Returns:
        The log tail and whether earlier lines were left out.

    Raises:
        HTTPException: 404 when the job is unknown.
    """
    record = scoped_store().get_job(_validated_job_id(job_id))
    if record is None:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    if not record.log_path:
        return JobLogResponse(content="", truncated=False)

    try:
        lines = Path(record.log_path).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return JobLogResponse(content="", truncated=False)

    return JobLogResponse(content="\n".join(lines[-tail:]), truncated=len(lines) > tail)


@router.post("/{job_id}/cancel", response_model=JobActionResponse)
def cancel_job(
    job_id: str, session: Annotated[dict, Depends(get_current_session)]
) -> JobActionResponse:
    """
    Cancel a job: take it out of the queue, or stop the build that runs.

    A job that waits is cancelled at once and never starts. A deploy or an
    update that is building is stopped: its build's unit is stopped, the
    release it was making is removed, the deployment is recorded as cancelled
    and what served before is never touched; the application's lock is
    released and the queue moves on. From the moment its release starts to be
    activated it is too late (409 ``cancel_refused``): the activation finishes
    or puts back what served by itself.

    Args:
        job_id: Job identifier.
        session: The authenticated session.

    Returns:
        The action outcome: ``cancelled`` for a job that had not started,
        ``cancelling`` for one that runs and was asked to stop.

    Raises:
        HTTPException: 404 when the job is unknown.
        CancelRefused: 409 when it has finished, cannot be interrupted, or its
            release is already being activated.
    """
    validated = _validated_job_id(job_id)
    manager = get_job_manager()

    try:
        outcome = manager.cancel(validated, actor=actor_label(session))
    except CancelRefused as refused:
        if refused.reason == "unknown":
            raise HTTPException(status_code=404, detail=f"Job not found: {validated}") from None
        raise

    if outcome.state == "cancelled":
        return JobActionResponse(message="Job cancelled", job_id=validated, state=outcome.state)
    return JobActionResponse(
        message="Cancelling: the build stops at once and its release is removed",
        job_id=validated,
        state=outcome.state,
    )
