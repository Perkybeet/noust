# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pull request previews of an application: settings and the previews themselves (2.2).

A client of :mod:`noust.managers.previews`, like ``noust preview``: every rule
(what can be previewed, the limits, the naming, the sweep timer) is the
manager's, and an endpoint only translates HTTP to a call and back. Removing
a preview takes an application down, so it runs as a job, and every mutation
here asks for sudo mode.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from noust.core.preview_store import PreviewStore
from noust.core.store import PreviewRecord, PreviewSettings, get_store
from noust.core.teams import scoped_store
from noust.core.utils import domain_to_app_name
from noust.core.variables.model import BOTH, PREVIEW
from noust.core.variables.store import VariableStore
from noust.managers import previews
from noust.managers.service_manager import ServiceManager
from noust.web.api.auth import get_current_session
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label, get_audit_logger, get_client_ip
from noust.web.jobs import JobType, get_job_manager
from noust.web.pydantic_compat import iso_offset_validator

router = APIRouter(route_class=NoustErrorRoute)


class PreviewSettingsOut(BaseModel):
    """
    The previews an application allows.

    Attributes:
        base_domain: Previews answer at ``pr-<n>-<app>.<base_domain>``; a
            wildcard record for it must point at this server.
        max_previews: How many may exist at once (1 to 20).
        ttl_hours: Hours a preview lives without a push (1 to 2160).
        allow_bots: Whether pull requests from bot accounts (Dependabot,
            Renovate) get a preview. A preview builds in the sandbox, as
            ``noust-build`` and without a network while it compiles, but its
            build and its unit get a copy of the application's secrets, so
            this is off unless turned on.
        exclude_env: Variables never copied to a preview when it is given all
            of the application's.
        inherit_env: ``allowlist`` (a preview is given only ``allow_env`` and
            the set defined for previews) or ``all`` (every variable less
            ``exclude_env``, as before 3.4).
        allow_env: The variables an ``allowlist`` preview copies.
        data_mode: ``shared`` (a preview uses the application's databases),
            ``empty`` (a database of its own) or ``clone``.
    """

    base_domain: str
    max_previews: int
    ttl_hours: int
    allow_bots: bool
    exclude_env: list[str]
    inherit_env: str = "all"
    allow_env: list[str] = Field(default_factory=list)
    data_mode: str = "shared"
    created_at: str | None = None
    updated_at: str | None = None

    _iso_timestamps = iso_offset_validator("created_at", "updated_at")


class PreviewOut(BaseModel):
    """
    One pull request's preview.

    Attributes:
        domain: The preview's own application domain.
        url: Where it answers.
        number: The pull (merge) request number.
        branch: The branch it deploys.
        head_sha: The commit last deployed or asked for.
        provider: ``github``, ``gitlab``, ``gitea`` or ``bitbucket``.
        repository: ``owner/repo``.
        status: ``pending``, ``deploying``, ``ready``, ``failed`` or
            ``removing``.
        error: Why the last build failed.
        expires_at: When it is removed unless pushed to again.
        kind: ``pr`` (a pull request), ``branch`` or ``tag`` (made by hand).
        ref: The branch or tag a preview made by hand deploys.
        databases: ``engine/name`` of the databases made for it alone.
    """

    domain: str
    url: str
    number: int
    kind: str = "pr"
    ref: str | None = None
    databases: list[str] = Field(default_factory=list)
    branch: str
    head_sha: str | None = None
    provider: str
    repository: str | None = None
    status: str
    error: str | None = None
    expires_at: str
    created_at: str | None = None
    updated_at: str | None = None

    _iso_timestamps = iso_offset_validator("expires_at", "created_at", "updated_at")


class PreviewsResponse(BaseModel):
    """
    An application's preview settings and previews.

    Attributes:
        domain: The application.
        enabled: Whether it allows previews.
        settings: Its settings, when enabled.
        previews: Its previews, oldest first. Some may remain after previews
            were turned off, while their removal runs or when it failed.
        total: How many.
    """

    domain: str
    enabled: bool
    settings: PreviewSettingsOut | None = None
    previews: list[PreviewOut]
    total: int


class PreviewSettingsRequest(BaseModel):
    """
    Turn previews on, or change their settings.

    Attributes:
        base_domain: The domain a wildcard record points at this server
            (``previews.example.com`` for ``*.previews.example.com``).
        max_previews: How many at once, 1 to 20.
        ttl_hours: Hours one lives without a push, 1 to 2160 (90 days).
        allow_bots: Build previews of bot accounts' pull requests; null
            keeps the current value (off for new settings).
        exclude_env: Names of variables never copied to a preview (and taken
            out of existing ones at their next build); null keeps the
            current list (none for new settings).
        inherit_env: ``allowlist`` or ``all``; null keeps the current way
            (``allowlist`` for previews turned on now).
        allow_env: The variables an ``allowlist`` preview copies; null keeps
            the current list (none for previews turned on now).
        data_mode: ``shared``, ``empty`` or ``clone``; null keeps the current
            way (``empty`` for an application that has a database).
    """

    base_domain: str
    max_previews: int = previews.DEFAULT_MAX_PREVIEWS
    ttl_hours: int = previews.DEFAULT_TTL_HOURS
    allow_bots: bool | None = None
    exclude_env: list[str] | None = None
    inherit_env: str | None = None
    allow_env: list[str] | None = None
    data_mode: str | None = None


class PreviewsDisabledResponse(BaseModel):
    """
    Previews turned off.

    Attributes:
        domain: The application.
        enabled: Always false.
        removing: Domains of the previews being removed.
        job_id: The job removing them; None when there were none.
    """

    domain: str
    enabled: bool = False
    removing: list[str]
    job_id: str | None = None


def _settings_out(settings: PreviewSettings) -> PreviewSettingsOut:
    """
    Args:
        settings: As stored.

    Returns:
        The API shape.
    """
    options = PreviewStore().options(settings.app_domain)
    return PreviewSettingsOut(
        base_domain=settings.base_domain,
        max_previews=settings.max_previews,
        ttl_hours=settings.ttl_hours,
        allow_bots=settings.allow_bots,
        exclude_env=list(settings.exclude_env),
        inherit_env=options.inherit_env,
        allow_env=list(options.allow_env),
        data_mode=options.data_mode,
        created_at=settings.created_at,
        updated_at=settings.updated_at,
    )


def _preview_out(record: PreviewRecord) -> PreviewOut:
    """
    Args:
        record: As stored.

    Returns:
        The API shape.
    """
    origin = PreviewStore().origin(record.domain)
    return PreviewOut(
        domain=record.domain,
        url=previews.preview_url(record.domain),
        number=record.number,
        kind=origin.kind,
        ref=origin.ref,
        databases=list(origin.databases),
        branch=record.branch,
        head_sha=record.head_sha,
        provider=record.provider,
        repository=record.repository,
        status=record.status,
        error=record.error,
        expires_at=record.expires_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def _known_app(domain: str) -> str:
    """
    Validate a domain and require an application behind it.

    Args:
        domain: From the path.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


def _audit(request: Request, session: dict[str, Any], action: str, detail: str) -> None:
    """
    Record a previews change in the audit log, with what changed.

    Args:
        request: The request.
        session: Who made it.
        action: ``previews.<verb>``.
        detail: What was done.
    """
    audit = get_audit_logger()
    if audit:
        audit.record(
            action=action,
            result="success",
            client_ip=get_client_ip(request),
            actor=actor_label(session),
            resource=request.url.path,
            detail=detail,
        )


@router.get("/{domain}/previews", response_model=PreviewsResponse)
def list_previews(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PreviewsResponse:
    """
    Read an application's preview settings and its previews.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        Settings (None when previews are off) and previews.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known_app(domain)
    store = get_store()
    settings = store.get_preview_settings(validated)
    items = [_preview_out(record) for record in store.list_previews(validated)]
    return PreviewsResponse(
        domain=validated,
        enabled=settings is not None,
        settings=_settings_out(settings) if settings else None,
        previews=items,
        total=len(items),
    )


@router.put("/{domain}/previews/settings", response_model=PreviewSettingsOut)
def put_preview_settings(
    domain: str,
    body: PreviewSettingsRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewSettingsOut:
    """
    Turn previews on for an application, or change their settings.

    Installs ``noust-previews.timer`` the first time any application turns
    previews on. A preview builds in the sandbox (as ``noust-build``, with the
    network but without the variables classified as secret and without loading
    the application's ``.env``; the strict profile, a build without network, is
    the application's opt-in), with a copy of the application's environment
    minus ``exclude_env``; only pull requests from
    people trusted with the repository get one (see
    :func:`noust.managers.previews.handle_pull_request`).

    Args:
        domain: The application.
        body: The settings.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The settings as stored.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A value is refused (400, with the reason).
        ServiceError: The sweep timer could not be installed.
    """
    validated = _known_app(domain)
    stored = previews.enable_previews(
        validated,
        body.base_domain,
        max_previews=body.max_previews,
        ttl_hours=body.ttl_hours,
        allow_bots=body.allow_bots,
        exclude_env=body.exclude_env,
        inherit_env=body.inherit_env,
        allow_env=body.allow_env,
        data_mode=body.data_mode,
    )
    _audit(
        request,
        session,
        "previews.settings",
        f"previews on under {stored.base_domain}, at most {stored.max_previews}, "
        f"{stored.ttl_hours} h, bots {'allowed' if stored.allow_bots else 'refused'}, "
        f"{len(stored.exclude_env)} variable(s) excluded, "
        f"{PreviewStore().options(stored.app_domain).inherit_env} variables, "
        f"{PreviewStore().options(stored.app_domain).data_mode} data",
    )
    return _settings_out(stored)


@router.delete("/{domain}/previews/settings", response_model=PreviewsDisabledResponse)
def delete_preview_settings(
    domain: str,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewsDisabledResponse:
    """
    Turn previews off for an application and queue the removal of the ones it has.

    Previews stop at once: a pull request opened from now on gets none. The
    existing ones are removed by one job.

    Args:
        domain: The application.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        What is being removed, and the job doing it.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known_app(domain)
    store = get_store()
    had_settings = store.delete_preview_settings(validated)
    removing = [record.domain for record in store.list_previews(validated)]
    job_id: str | None = None
    if removing:
        job = get_job_manager().create_job(
            job_type=JobType.DELETE,
            name=f"Remove the previews of {validated}",
            description=f"Removing {len(removing)} preview(s) of {validated}",
            func=previews.remove_previews_job,
            kwargs={"parent_domain": validated},
            metadata={"domain": validated, "previews": removing},
            actor=actor_label(session),
        )
        job_id = job.id
    else:
        previews.refresh_sweep_timer()
    _audit(
        request,
        session,
        "previews.disable",
        f"previews off (were {'on' if had_settings else 'off'}); removing {len(removing)}",
    )
    return PreviewsDisabledResponse(domain=validated, removing=removing, job_id=job_id)


class PullRequestOut(BaseModel):
    """
    An open pull request of the application's repository.

    Attributes:
        number: Its number.
        title: Its title.
        branch: Its source branch.
        head_sha: The commit at the tip of its branch.
        author: Who opened it.
        draft: Whether it is a draft.
        from_fork: Whether its branch lives in another repository (a preview of
            it is refused unless asked for).
        trusted: Whether Noust would build it on its own: its author is an
            owner, member or collaborator, and it is not from a fork.
        url: Its page on the code host.
        preview: The domain of the preview it already has, if any.
        status: That preview's status.
    """

    number: int
    title: str
    branch: str
    base_branch: str = ""
    head_sha: str
    author: str
    draft: bool = False
    from_fork: bool = False
    trusted: bool = False
    url: str = ""
    preview: str | None = None
    status: str | None = None


class PullRequestsResponse(BaseModel):
    """The open pull requests of an application's repository."""

    domain: str
    pull_requests: list[PullRequestOut]
    total: int


class CreatePreviewRequest(BaseModel):
    """
    A preview to make by hand. Name exactly one of the three.

    Attributes:
        pull_request: The number of an open pull request (needs the GitHub App).
        branch: A branch.
        tag: A tag.
        allow_fork: Preview a pull request whose branch lives in a fork.
    """

    pull_request: int | None = None
    branch: str | None = None
    tag: str | None = None
    allow_fork: bool = False


class PreviewQueuedResponse(BaseModel):
    """
    A preview recorded and its build queued.

    Attributes:
        preview: The preview, pending.
        job_id: The job building it.
    """

    preview: PreviewOut
    job_id: str | None = None


class PreviewDetailResponse(BaseModel):
    """
    One preview, and what runs it.

    Attributes:
        preview: The preview.
        active: Whether its unit is running now.
        last_deployment: Its newest deployment's status and when.
    """

    preview: PreviewOut
    active: bool = False
    last_deployment: dict[str, Any] | None = None


class PreviewLogsResponse(BaseModel):
    """
    What a preview's process printed.

    Attributes:
        domain: The preview.
        logs: The journal's tail.
        lines: How many lines were asked for.
        build: The log of its last build, when it has one.
    """

    domain: str
    logs: str
    lines: int
    build: str | None = None


class PreviewVariableRequest(BaseModel):
    """
    A variable defined for the previews of an application.

    Attributes:
        value: Its value; ``{{...}}`` references are allowed (``{{app.URL}}`` is the
            preview's own address).
        secret: True or False to say what it is, null to let Noust decide.
        audience: ``both``, ``build`` or ``runtime``.
    """

    value: str
    secret: bool | None = None
    audience: str = BOTH


class PreviewVariablesResponse(BaseModel):
    """
    The variables defined for the previews of an application.

    Attributes:
        domain: The application.
        variables: Name to value (a secret masked), with its audience.
    """

    domain: str
    variables: list[dict[str, Any]]


def _variables_response(
    domain: str, app_id: int, *, reveal: bool = False
) -> PreviewVariablesResponse:
    """
    Args:
        domain: The application.
        app_id: Its id.
        reveal: Show values in clear.

    Returns:
        The variables, secrets masked unless asked for.
    """
    from noust.core.secret_detection import classify
    from noust.web.api.apps import _redact_env

    found = VariableStore().list(PREVIEW, app_id)
    marks = {v.name: v.secret for v in found if v.secret is not None}
    values = {v.name: v.value for v in found}
    shown = values if reveal else _redact_env(values, marks)
    return PreviewVariablesResponse(
        domain=domain,
        variables=[
            {
                "name": v.name,
                "value": shown[v.name],
                "secret": classify(v.name, v.value, marks).secret,
                "audience": v.audience,
                "updated_at": v.updated_at,
            }
            for v in found
        ],
    )


def _app_id(domain: str) -> int:
    """
    Args:
        domain: A validated domain the caller sees.

    Returns:
        The application's id.

    Raises:
        HTTPException: 404 when it has none.
    """
    app = scoped_store().get_app(domain)
    if app is None or app.id is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {domain}")
    return app.id


@router.get("/{domain}/previews/pull-requests", response_model=PullRequestsResponse)
def list_pull_requests(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PullRequestsResponse:
    """
    List the open pull requests of the application's repository.

    Needs this server's GitHub App to reach the repository; any other host
    answers with how to make a preview from a branch instead.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        The open pull requests, with the preview each already has.

    Raises:
        HTTPException: 404 when the application is unknown.
        IntegrationError: No GitHub App reaches the repository (502).
    """
    validated = _known_app(domain)
    items = [PullRequestOut(**item) for item in previews.list_pull_requests(validated)]
    return PullRequestsResponse(domain=validated, pull_requests=items, total=len(items))


@router.get("/{domain}/previews/variables", response_model=PreviewVariablesResponse)
def get_preview_variables(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> PreviewVariablesResponse:
    """
    Read the variables defined for the previews of an application.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        Them, secrets masked.
    """
    validated = _known_app(domain)
    return _variables_response(validated, _app_id(validated))


@router.put("/{domain}/previews/variables/{name}", response_model=PreviewVariablesResponse)
def put_preview_variable(
    domain: str,
    name: str,
    body: PreviewVariableRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewVariablesResponse:
    """
    Define a variable the previews of an application are given.

    It wins over a variable of the application with the same name, and is given
    whatever the application's settings copy.

    Args:
        domain: The application.
        name: The variable's name.
        body: Its value, mark and audience.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The variables defined for previews.
    """
    validated = _known_app(domain)
    app_id = _app_id(validated)
    VariableStore().set(
        PREVIEW,
        app_id,
        name,
        body.value,
        secret=body.secret,
        audience=body.audience,
        actor=actor_label(session),
    )
    _audit(request, session, "previews.variables", f"set {name} for the previews of {validated}")
    return _variables_response(validated, app_id)


@router.delete("/{domain}/previews/variables/{name}", response_model=PreviewVariablesResponse)
def delete_preview_variable(
    domain: str,
    name: str,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewVariablesResponse:
    """
    Stop giving previews a variable.

    Args:
        domain: The application.
        name: The variable's name.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The variables defined for previews.

    Raises:
        HTTPException: 404 when it is not defined.
    """
    validated = _known_app(domain)
    app_id = _app_id(validated)
    if not VariableStore().delete(PREVIEW, app_id, name):
        raise HTTPException(status_code=404, detail=f"No variable {name} for previews")
    _audit(
        request, session, "previews.variables", f"removed {name} for the previews of {validated}"
    )
    return _variables_response(validated, app_id)


@router.post("/{domain}/previews", response_model=PreviewQueuedResponse, status_code=202)
def create_preview(
    domain: str,
    body: CreatePreviewRequest,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewQueuedResponse:
    """
    Make a preview by hand, from an open pull request, a branch or a tag.

    Args:
        domain: The application.
        body: Exactly one of the three, and whether a fork is allowed.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The preview, pending, and the job that builds it.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: Previews are off, the limit is reached, or what was named
            cannot be previewed (400).
        IntegrationError: The pull request cannot be read (502).
    """
    validated = _known_app(domain)
    created = previews.create_preview(
        validated,
        pull_request=body.pull_request,
        branch=body.branch,
        tag=body.tag,
        allow_fork=body.allow_fork,
        actor=actor_label(session),
    )
    _audit(
        request,
        session,
        "previews.create",
        f"{created.kind} {created.record.branch} of {validated} at {created.record.domain}",
    )
    return PreviewQueuedResponse(preview=_preview_out(created.record), job_id=created.job_id)


def _known_preview(domain: str, number: int) -> tuple[str, PreviewRecord]:
    """
    Args:
        domain: The application, from the path.
        number: The preview's number.

    Returns:
        The validated domain and the preview.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
    """
    validated = _known_app(domain)
    record = get_store().get_preview(validated, number)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"{validated} has no preview of pull request #{number}"
        )
    return validated, record


@router.get("/{domain}/previews/{number}", response_model=PreviewDetailResponse)
def get_preview(
    domain: str, number: int, session: Annotated[dict, Depends(get_current_session)]
) -> PreviewDetailResponse:
    """
    Read one preview, with whether it runs and its last deployment.

    Args:
        domain: The application.
        number: The preview's number.
        session: The authenticated session.

    Returns:
        The preview.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
    """
    _validated, record = _known_preview(domain, number)
    app = scoped_store().get_app(record.domain)
    active = False
    if app is not None and not app.is_static:
        status = ServiceManager(verbose=False).get_status(domain_to_app_name(record.domain))
        active = bool(status.get("active"))
    recent = scoped_store().list_deployments(record.domain, limit=1)
    last = (
        {
            "id": recent[0].id,
            "status": recent[0].status,
            "git_commit": recent[0].git_commit,
            "started_at": recent[0].started_at,
        }
        if recent
        else None
    )
    return PreviewDetailResponse(preview=_preview_out(record), active=active, last_deployment=last)


@router.post(
    "/{domain}/previews/{number}/redeploy", response_model=PreviewQueuedResponse, status_code=202
)
def redeploy_preview(
    domain: str,
    number: int,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewQueuedResponse:
    """
    Build a preview again, from the head of its branch as it is now.

    Args:
        domain: The application.
        number: The preview's number.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The preview, pending, and the job that builds it.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
        ValidationError: It is being built or removed (400).
    """
    _validated, record = _known_preview(domain, number)
    created = previews.redeploy_preview(record.domain, actor=actor_label(session))
    _audit(request, session, "previews.redeploy", f"rebuilding {record.domain} (#{number})")
    return PreviewQueuedResponse(preview=_preview_out(created.record), job_id=created.job_id)


@router.post("/{domain}/previews/{number}/stop", response_model=PreviewOut)
def stop_preview(
    domain: str,
    number: int,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewOut:
    """
    Stop a preview without removing it.

    Args:
        domain: The application.
        number: The preview's number.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The preview, stopped.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
    """
    _validated, record = _known_preview(domain, number)
    stopped = previews.stop_preview(record.domain)
    _audit(request, session, "previews.stop", f"stopped {record.domain} (#{number})")
    return _preview_out(stopped)


@router.post("/{domain}/previews/{number}/start", response_model=PreviewOut)
def start_preview(
    domain: str,
    number: int,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> PreviewOut:
    """
    Start a preview that was stopped.

    Args:
        domain: The application.
        number: The preview's number.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The preview, ready.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
    """
    _validated, record = _known_preview(domain, number)
    started = previews.start_preview(record.domain)
    _audit(request, session, "previews.start", f"started {record.domain} (#{number})")
    return _preview_out(started)


@router.get("/{domain}/previews/{number}/logs", response_model=PreviewLogsResponse)
def get_preview_logs(
    domain: str,
    number: int,
    session: Annotated[dict, Depends(get_current_session)],
    lines: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> PreviewLogsResponse:
    """
    Read what a preview's process printed, and the log of its last build.

    Args:
        domain: The application.
        number: The preview's number.
        session: The authenticated session.
        lines: How many lines of the journal.

    Returns:
        The journal's tail and the last build's log.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
    """
    _validated, record = _known_preview(domain, number)
    logs = (
        ServiceManager(verbose=False).logs(domain_to_app_name(record.domain), lines=lines)
        or "No logs available"
    )
    return PreviewLogsResponse(
        domain=record.domain, logs=logs, lines=lines, build=previews.last_build_log(record.domain)
    )


@router.delete("/{domain}/previews/{number}", response_model=JobAcceptedResponse, status_code=202)
def delete_preview(
    domain: str,
    number: int,
    request: Request,
    session: Annotated[dict, Depends(require_elevated)],
) -> JobAcceptedResponse:
    """
    Queue the removal of one preview: its application, certificate and record.

    Args:
        domain: The application previewed.
        number: The pull request number.
        request: The request, for the audit record.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        HTTPException: 404 when the application or the preview is unknown.
    """
    validated = _known_app(domain)
    record = get_store().get_preview(validated, number)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"{validated} has no preview of pull request #{number}"
        )
    job = previews.queue_preview_removal(record, reason="removed", actor=actor_label(session))
    _audit(request, session, "previews.remove", f"removing {record.domain} (#{number})")
    return JobAcceptedResponse(
        job_id=job.id,
        status=job.status.value,
        message=f"Removal of {record.domain} queued",
        job=job.to_dict(),
    )
