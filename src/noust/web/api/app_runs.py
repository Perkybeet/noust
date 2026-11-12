# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How an application runs: ``GET /api/apps/{domain}/runs``.

The application's page says whether it is a service of the system, a container (built from its
Dockerfile, by Railpack, or an image from a registry) or a Compose stack, and what serves it. A client
of :func:`noust.deployers.how_it_runs.describe_runs` (rule 3): the console, the CLI and a script read
one answer. What decides the form of a *new* application is ``POST /api/apps/inspect`` and its
``ways``; an application that exists does not change form (it is deployed again and its domain moved).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.deployers.how_it_runs import describe_runs
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, known_app_row

router = APIRouter(route_class=NoustErrorRoute)


class AppRunsResponse(BaseModel):
    """
    How an application runs.

    Attributes:
        domain: The application.
        form: ``server`` (directly on the server, a service of the system) or ``container``.
        via: For a container, how it is built: ``dockerfile``, ``railpack`` or ``image`` (from a
            registry); ``compose`` for a Compose stack. Null for a service.
        image: For a container application, the image that serves: the registry's reference, or
            the one Noust built for the active release. Null when none serves yet, and for a stack.
    """

    domain: str
    form: Literal["server", "container"]
    via: Literal["dockerfile", "railpack", "image", "compose"] | None = Field(default=None)
    image: str | None = Field(default=None)


@router.get("/{domain}/runs", response_model=AppRunsResponse)
def get_app_runs(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppRunsResponse:
    """
    Say how an application runs. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Its form and, for a container, how it is built and which image serves.

    Raises:
        HTTPException: 404 when the application is unknown, or outside the caller's teams.
        ValidationError: Noust adopted it and does not run it (400): it has no form of its own.
    """
    app = known_app_row(domain)
    runs = describe_runs(app)
    if runs is None:
        raise ValidationError(
            f"{app.domain} is an adopted application: Noust watches it and does not run it",
            details="How it runs is how its owner set it up.",
        )
    return AppRunsResponse(
        domain=app.domain,
        form=runs.form,  # type: ignore[arg-type]
        via=runs.via,  # type: ignore[arg-type]
        image=runs.image,
    )
