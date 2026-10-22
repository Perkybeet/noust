# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags on applications, and deploying every application that carries one.

A client of :class:`~noust.core.app_tags.AppTags` and
:mod:`noust.managers.tag_deploys`, like ``noust app tag``. Which applications a
caller sees is the scoped store's: a tag listed or deployed here covers only
the applications the caller can see, so ``prod`` in one team is not ``prod`` in
another.

``POST /api/tags/{tag}/deploy`` is also the call a pipeline makes after a build:
with an API token that holds ``apps.deploy`` it updates every application that
carries the tag, which is what Coolify's ``/deploy?tag=`` does. It is
``POST /api/jobs/update-many`` with one tag (the one implementation of updating
a group), so it skips what the caller may not deploy where it lives.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.app_tags import MAX_TAGS, AppTags, validate_tag
from noust.core.audit import record as audit_record
from noust.core.exceptions import NoustError, ValidationError
from noust.core.resource_tags import KIND_DATABASE, KIND_SERVICE, ResourceTags, database_key
from noust.core.teams import scoped_store
from noust.managers import tag_deploys
from noust.web.api.apps import restart_application
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.api.jobs import UpdateManyRequest, update_group
from noust.web.api.services import restart_refusal, restart_unit
from noust.web.permissions import Permission
from noust.web.permissions.principal import actor_of
from noust.web.permissions.team_roles import permissions_at

#: How many things one restart by tag may touch; a tag that reaches more is narrowed first.
MAX_RESTART = 100

#: ``/api/apps/{domain}/tags``.
app_router = APIRouter(route_class=NoustErrorRoute)
#: ``/api/tags``.
router = APIRouter(route_class=NoustErrorRoute)


class TagsResponse(BaseModel):
    """
    The tags of one application.

    Attributes:
        domain: The application.
        tags: Its tags, sorted.
        max_tags: How many an application may carry.
    """

    domain: str
    tags: list[str]
    max_tags: int = MAX_TAGS


class SetTagsRequest(BaseModel):
    """The tags an application is to carry; any it has that are not here are removed."""

    tags: list[str] = Field(default_factory=list)


class TagOut(BaseModel):
    """
    One tag and the applications that carry it.

    Attributes:
        tag: The tag.
        applications: Their domains, among those the caller sees.
    """

    tag: str
    applications: list[str]


class TagListResponse(BaseModel):
    """Every tag the caller's applications carry."""

    tags: list[TagOut]
    total: int


class DeployTagRequest(BaseModel):
    """
    Deploy every application that carries a tag.

    Attributes:
        force: Rebuild an application even when its branch has nothing new.
    """

    force: bool = False


class TagDeployOut(BaseModel):
    """
    What came of one application.

    Attributes:
        domain: The application.
        outcome: ``queued`` (a job will update it), ``skipped`` or ``failed``.
        detail: Why, for a skip or a failure.
        job_id: The job, when one was queued.
    """

    domain: str
    outcome: str
    detail: str = ""
    job_id: str | None = None


class DeployTagResponse(BaseModel):
    """What deploying a tag did."""

    tag: str
    results: list[TagDeployOut]
    queued: int


class RestartTagRequest(BaseModel):
    """
    Restart everything that carries a tag.

    Attributes:
        dry_run: Say what would be restarted, and what would not and why, and restart nothing.
    """

    dry_run: bool = False


class TagRestartOut(BaseModel):
    """
    What came of one thing that carries the tag.

    Attributes:
        kind: ``application``, ``service`` or ``database``.
        name: A domain, a unit, or ``engine/database``.
        outcome: ``restarted``, ``would_restart`` (a dry run), ``skipped`` or ``failed``.
        detail: Why, for a skip or a failure.
    """

    kind: str
    name: str
    outcome: str
    detail: str = ""


class RestartTagResponse(BaseModel):
    """
    What restarting a tag did.

    Attributes:
        tag: The tag.
        dry_run: Whether nothing was restarted because it was only asked.
        results: One row per thing that carries it that the caller sees.
        restarted: How many were restarted.
    """

    tag: str
    dry_run: bool
    results: list[TagRestartOut]
    restarted: int


def _known(domain: str) -> str:
    """
    Args:
        domain: From the path.

    Returns:
        The validated domain of an application the caller sees.

    Raises:
        HTTPException: 404 when it is not there for the caller.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@app_router.get("/{domain}/tags", response_model=TagsResponse)
def get_app_tags(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> TagsResponse:
    """
    Read the tags of an application.

    Args:
        domain: The application.
        session: The authenticated session.

    Returns:
        Its tags.
    """
    validated = _known(domain)
    return TagsResponse(domain=validated, tags=AppTags().of(validated))


@app_router.put("/{domain}/tags", response_model=TagsResponse)
def put_app_tags(
    domain: str, body: SetTagsRequest, session: Annotated[dict, Depends(get_current_session)]
) -> TagsResponse:
    """
    Make an application carry exactly these tags.

    Args:
        domain: The application.
        body: The tags.
        session: The authenticated session.

    Returns:
        The tags it carries now.

    Raises:
        ValidationError: A tag is not one, or there are too many (400).
    """
    validated = _known(domain)
    tags = AppTags().set(validated, body.tags)
    audit_record(
        "apps.tags.set",
        actor=actor_of(session),
        target=f"app:{validated}",
        details={"tags": tags},
    )
    return TagsResponse(domain=validated, tags=tags)


@router.get("", response_model=TagListResponse)
def list_tags(session: Annotated[dict, Depends(get_current_session)]) -> TagListResponse:
    """
    List the tags the caller's applications carry, with those applications.

    Args:
        session: The authenticated session.

    Returns:
        Them, by tag.
    """
    visible = {app.domain for app in scoped_store().list_apps()}
    grouped: dict[str, list[str]] = {}
    for domain, tags in AppTags().by_application().items():
        if domain not in visible:
            continue
        for tag in tags:
            grouped.setdefault(tag, []).append(domain)
    out = [TagOut(tag=tag, applications=sorted(apps)) for tag, apps in sorted(grouped.items())]
    return TagListResponse(tags=out, total=len(out))


def _failure(exc: NoustError) -> str:
    """
    Args:
        exc: What refused.

    Returns:
        Its own words, with how to fix it when it says.
    """
    return f"{exc.message} {exc.details}".strip() if exc.details else exc.message


@router.post("/{tag}/restart", response_model=RestartTagResponse)
def restart_tag(
    tag: str,
    session: Annotated[dict, Depends(get_current_session)],
    body: RestartTagRequest | None = None,
) -> RestartTagResponse:
    """
    Restart every application and service that carries a tag.

    What a caller reaches is what it sees: a team's applications, never another team's, and the
    server's units only for the instance. A database that carries the tag is listed and left,
    because a database is restarted with its engine, which every database on it shares. One that
    fails does not stop the others; ``dry_run`` says what would happen first.

    Args:
        tag: The tag.
        session: The authenticated session.
        body: ``dry_run`` to restart nothing.

    Returns:
        What came of each.

    Raises:
        ValidationError: The tag is not one, or it reaches more than :data:`MAX_RESTART` things.
        HTTPException: 404 when nothing the caller sees carries it.
    """
    wanted = validate_tag(tag)
    dry_run = bool(body and body.dry_run)
    scoped = scoped_store()
    apps = tag_deploys.applications_with(wanted)
    units: list[str] = []
    databases = ResourceTags().with_tag(KIND_DATABASE, wanted)
    if scoped.scope.sees_all:
        # The server's units are the instance's: a team never learns they exist.
        units = ResourceTags().with_tag(KIND_SERVICE, wanted)
    else:
        mine = {database_key(row.engine, row.name) for row in scoped.list_databases()}
        databases = [key for key in databases if key in mine]
    if not (apps or units or databases):
        raise HTTPException(status_code=404, detail=f"Nothing you see carries the tag {wanted}")
    if len(apps) + len(units) > MAX_RESTART:
        raise ValidationError(
            f"That is {len(apps) + len(units)} things; a request restarts at most {MAX_RESTART}",
            details="Give them a narrower tag.",
            field="tag",
        )
    results: list[TagRestartOut] = []
    for app in apps:
        results.append(_restart_application(app, session, dry_run))
    for unit in units:
        results.append(_restart_service(unit, dry_run))
    for key in databases:
        results.append(
            TagRestartOut(
                kind="database",
                name=key,
                outcome=tag_deploys.SKIPPED,
                detail="A database is restarted with its engine, which other databases share: "
                "POST /api/databases/engines/{engine}/restart.",
            )
        )
    restarted = sum(1 for row in results if row.outcome == "restarted")
    audit_record(
        "apps.tags.restart",
        actor=actor_of(session),
        target=f"tag:{wanted}",
        details={
            "dry_run": dry_run,
            "restarted": [row.name for row in results if row.outcome == "restarted"],
            "failed": [row.name for row in results if row.outcome == "failed"],
        },
    )
    return RestartTagResponse(tag=wanted, dry_run=dry_run, results=results, restarted=restarted)


def _restart_application(app: Any, session: dict, dry_run: bool) -> TagRestartOut:
    """
    Restart one application of a tag, if the caller may where it lives.

    Args:
        app: The application.
        session: The authenticated session.
        dry_run: Only say what would happen.

    Returns:
        What came of it.
    """
    placed = scoped_store().placement_of_project(app.project_id, app.environment_id)
    held = (
        permissions_at(
            session,
            placed.team_id if placed else None,
            placed.project_id if placed else None,
            (placed.environment_id or None) if placed else None,
        )
        if "team_roles" in session
        else {Permission.APPS_OPERATE}
    )
    if Permission.APPS_OPERATE not in held:
        return TagRestartOut(
            kind="application",
            name=app.domain,
            outcome=tag_deploys.SKIPPED,
            detail="You may not restart it.",
        )
    if dry_run:
        return TagRestartOut(kind="application", name=app.domain, outcome="would_restart")
    try:
        restart_application(app.domain)
    except HTTPException as exc:
        # The only one raised is "no unit": an application that is not running as a service.
        return TagRestartOut(
            kind="application",
            name=app.domain,
            outcome=tag_deploys.SKIPPED,
            detail=str(exc.detail),
        )
    except NoustError as exc:
        return TagRestartOut(
            kind="application", name=app.domain, outcome=tag_deploys.FAILED, detail=_failure(exc)
        )
    return TagRestartOut(kind="application", name=app.domain, outcome="restarted")


def _restart_service(unit: str, dry_run: bool) -> TagRestartOut:
    """
    Restart one unit of a tag.

    Args:
        unit: The unit.
        dry_run: Only say what would happen.

    Returns:
        What came of it.
    """
    refusal = restart_refusal(unit)
    if refusal is not None:
        return TagRestartOut(kind="service", name=unit, outcome=tag_deploys.SKIPPED, detail=refusal)
    if dry_run:
        return TagRestartOut(kind="service", name=unit, outcome="would_restart")
    try:
        restart_unit(unit)
    except HTTPException as exc:
        return TagRestartOut(
            kind="service", name=unit, outcome=tag_deploys.SKIPPED, detail=str(exc.detail)
        )
    except NoustError as exc:
        return TagRestartOut(
            kind="service", name=unit, outcome=tag_deploys.FAILED, detail=_failure(exc)
        )
    return TagRestartOut(kind="service", name=unit, outcome="restarted")


@router.post("/{tag}/deploy", response_model=DeployTagResponse, status_code=202)
def deploy_tag(
    tag: str,
    session: Annotated[dict, Depends(get_current_session)],
    body: DeployTagRequest | None = None,
) -> DeployTagResponse:
    """
    Update every application that carries a tag, each as a job.

    Args:
        tag: The tag.
        session: The authenticated session.
        body: ``force`` to rebuild applications with nothing new.

    Returns:
        What came of each application; ``queued`` ones have a job to follow.

    Raises:
        HTTPException: 404 when no application the caller sees carries it.
    """
    wanted = validate_tag(tag)
    # The one "update a group": the same gates, merges and skips as a project's.
    group = update_group(UpdateManyRequest(tags=[wanted], force=bool(body and body.force)), session)
    if not group.items:
        raise HTTPException(status_code=404, detail=f"No application carries the tag {wanted}")
    out = [
        TagDeployOut(
            domain=item.domain,
            outcome=tag_deploys.QUEUED if item.job_id else tag_deploys.SKIPPED,
            detail=item.reason
            or ("an update was already waiting" if item.status == "joined" else ""),
            job_id=item.job_id,
        )
        for item in group.items
    ]
    audit_record(
        "apps.tags.deploy",
        actor=actor_of(session),
        target=f"tag:{wanted}",
        details={"applications": [r.domain for r in out], "queued": group.queued},
    )
    return DeployTagResponse(tag=wanted, results=out, queued=group.queued)
