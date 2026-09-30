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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from noust.core.store import PreviewRecord, PreviewSettings, get_store
from noust.managers import previews
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
        exclude_env: Variables never copied to a preview.
    """

    base_domain: str
    max_previews: int
    ttl_hours: int
    allow_bots: bool
    exclude_env: list[str]
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
        provider: ``github``, ``gitlab`` or ``gitea``.
        repository: ``owner/repo``.
        status: ``pending``, ``deploying``, ``ready``, ``failed`` or
            ``removing``.
        error: Why the last build failed.
        expires_at: When it is removed unless pushed to again.
    """

    domain: str
    url: str
    number: int
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
    """

    base_domain: str
    max_previews: int = previews.DEFAULT_MAX_PREVIEWS
    ttl_hours: int = previews.DEFAULT_TTL_HOURS
    allow_bots: bool | None = None
    exclude_env: list[str] | None = None


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
    return PreviewSettingsOut(
        base_domain=settings.base_domain,
        max_previews=settings.max_previews,
        ttl_hours=settings.ttl_hours,
        allow_bots=settings.allow_bots,
        exclude_env=list(settings.exclude_env),
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
    return PreviewOut(
        domain=record.domain,
        url=previews.preview_url(record.domain),
        number=record.number,
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
    if get_store().get_app(validated) is None:
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
    previews on. A preview builds in the sandbox (as ``noust-build``, in the
    strict network profile: dependencies install with the network and without
    the secrets, the build runs without a network), with a copy of the
    application's environment minus ``exclude_env``; only pull requests from
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
    )
    _audit(
        request,
        session,
        "previews.settings",
        f"previews on under {stored.base_domain}, at most {stored.max_previews}, "
        f"{stored.ttl_hours} h, bots {'allowed' if stored.allow_bots else 'refused'}, "
        f"{len(stored.exclude_env)} variable(s) excluded",
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
