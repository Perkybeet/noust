# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
First deployments that failed: ``/api/first-deploys`` (item 141).

A first deployment that fails undoes the application's own row on purpose, so the page of that
domain finds nothing to show. This is the one place that says what happened: what the operator
asked for (to start the wizard again with it), how the job failed, verbatim, and when. A thin client
of :class:`noust.core.first_deploys.FirstDeploys`, which holds the rules and the team scope.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from noust.core.deploy_failures import coded as deploy_failure
from noust.core.exceptions import ValidationError
from noust.core.first_deploys import FirstDeploy, FirstDeploys
from noust.core.teams import ownership_target
from noust.web.api.auth import get_current_session
from noust.web.api.deps import CodedText, NoustErrorRoute

logger = logging.getLogger(__name__)

router = APIRouter(route_class=NoustErrorRoute)

Session = Annotated[dict, Depends(get_current_session)]

#: What a job's id looks like (see ``noust.web.jobs``): judged before it reaches a query.
_JOB_ID = re.compile(r"[0-9a-f]{1,36}")


class FirstDeployOut(BaseModel):
    """
    One first deployment that failed.

    Attributes:
        job_id: The deployment's job: its log is at ``/api/jobs/{job_id}/log``.
        domain: The domain it was for.
        origin: How the application was to start: ``source``, ``image``, ``compose`` or ``recipe``.
        source: What to read again (an address, a path or an image), with every credential
            masked; null for a recipe or a typed document.
        branch: The branch asked for, when one was.
        app_type: The type asked for (``auto`` or a name).
        recipe: The recipe's name, for a recipe.
        created_at: When it was asked for.
        error: How it failed, verbatim, as the job kept it.
        error_coded: Its first line as a sentence of Noust's, when it is one, for the console to
            say in its own language.
        finished_at: When the job ended.
    """

    job_id: str
    domain: str
    origin: str
    source: str | None = None
    branch: str | None = None
    app_type: str | None = None
    recipe: str | None = None
    created_at: str
    error: str | None = None
    error_coded: CodedText | None = None
    finished_at: str | None = None

    @classmethod
    def of(cls, attempt: FirstDeploy) -> FirstDeployOut:
        """
        Args:
            attempt: A failed first deployment.

        Returns:
            Its answer.
        """
        sentence = deploy_failure(attempt.error)
        return cls(
            job_id=attempt.job_id,
            domain=attempt.domain,
            origin=attempt.origin,
            source=attempt.source,
            branch=attempt.branch,
            app_type=attempt.app_type,
            recipe=attempt.recipe,
            created_at=attempt.created_at,
            error=attempt.error,
            error_coded=CodedText(**sentence.to_dict()) if sentence is not None else None,
            finished_at=attempt.finished_at,
        )


class FirstDeploysResponse(BaseModel):
    """
    The first deployments that failed and are still the last word on their domain.

    Attributes:
        items: Newest first, of the projects the caller sees.
    """

    items: list[FirstDeployOut]


@router.get("", response_model=FirstDeploysResponse)
def list_first_deploys(
    session: Session,
    domain: Annotated[str | None, Query(description="Only this domain's.")] = None,
) -> FirstDeploysResponse:
    """
    List the first deployments that failed and left no application behind.

    A domain with an application of its own, one whose newest attempt is running or worked, one
    that was dismissed and one older than two weeks are not listed.

    Args:
        session: The authenticated session.
        domain: Only this domain's.

    Returns:
        The failures the caller may see.
    """
    return FirstDeploysResponse(
        items=[FirstDeployOut.of(found) for found in FirstDeploys().failed(domain=domain)]
    )


@router.post("/{attempt}/dismiss", status_code=204)
def dismiss_first_deploy(attempt: str, session: Session) -> Response:
    """
    Put a failed first deployment away: it stops being listed, here and in what needs attention.

    Args:
        attempt: The deployment's job id: the failure is the job's.
        session: The authenticated session.

    Returns:
        An empty answer.

    Raises:
        ValidationError: 400 when the id is not a job's.
        HTTPException: 404 when there is no such failure, or it belongs to a project the caller
            does not see (which reads as absent).
    """
    if not _JOB_ID.fullmatch(attempt):
        raise ValidationError(
            f"Invalid job id: {attempt!r}",
            details="Job ids are hexadecimal, as returned when the deployment was queued.",
        )
    if not FirstDeploys().dismiss(attempt):
        raise HTTPException(status_code=404, detail=f"No failed first deployment: {attempt}")
    return Response(status_code=204)


def remember_attempt(
    job_id: str,
    domain: str,
    *,
    owner: tuple[int, int],
    source: str | None,
    branch: str | None,
    app_type: str,
    recipe: str | None = None,
    image: str | None = None,
    typed_document: bool = False,
) -> None:
    """
    Remember what a first deployment was asked to do, for the page of its domain if it fails.

    Called by ``POST /api/apps`` once the job is queued. The deployment is already on its way, so
    a store that cannot be written is logged and the request still succeeds: the failure then has
    no page of its own, which is how it was before this record existed.

    Args:
        job_id: The deployment's job.
        domain: The domain it is for.
        owner: The ``(project_id, environment_id)`` it was queued for.
        source: The repository, archive or folder, as it was typed.
        branch: The branch asked for.
        app_type: The type asked for (``auto`` or a name).
        recipe: The recipe's name, when the application is one.
        image: The registry image, when the application starts from one.
        typed_document: The application is a Compose document that was typed in.
    """
    if recipe is not None:
        origin, what = "recipe", None
    elif typed_document:
        origin, what = "compose", None
    elif image:
        origin, what = "image", image
    else:
        origin, what = "source", source
    try:
        with ownership_target(*owner):
            FirstDeploys().record(
                job_id,
                domain,
                origin=origin,
                source=what,
                branch=branch if origin == "source" else None,
                app_type=app_type,
                recipe=recipe,
            )
    except sqlite3.Error as exc:
        logger.warning("Could not remember the first deployment of %s: %s", domain, exc)
