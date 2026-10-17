# Copyright (c) 2024-2026 Yago Lopez Prado
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

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.app_tags import MAX_TAGS, AppTags, validate_tag
from noust.core.audit import record as audit_record
from noust.core.teams import scoped_store
from noust.managers import tag_deploys
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.api.jobs import UpdateManyRequest, update_group
from noust.web.permissions.principal import actor_of

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
