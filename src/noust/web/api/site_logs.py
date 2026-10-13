# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/apps/{domain}/site-logs``: what the web server logged for a site.

A thin client of :class:`~noust.managers.site_logs.SiteLogReader`. Mounted under
``/apps``: it owns only this one path below ``/{domain}``. The application is
looked up through the team-scoped store, so another team's site reads as absent,
and what comes back is masked like every other log (checklist G-11, G-12).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from noust.core.redact import scrubber_for
from noust.core.teams import scoped_store
from noust.managers.site_logs import KINDS, MAX_LINES, SiteLogReader
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain

router = APIRouter(route_class=NoustErrorRoute)


class SiteLogResponse(BaseModel):
    """
    The end of one of a site's logs.

    Attributes:
        domain: The site.
        kind: ``access`` or ``error``.
        path: The file read, or where it was looked for.
        available: Whether the web server wrote one.
        lines: The last lines that matched, oldest first.
        size_bytes: The file's size.
        truncated: Earlier lines exist that were not returned.
        matched: How many lines matched the filter among those read.
    """

    domain: str
    kind: str
    path: str | None = None
    available: bool
    lines: list[str] = Field(default_factory=list)
    size_bytes: int = 0
    truncated: bool = False
    matched: int = 0


@router.get("/{domain}/site-logs", response_model=SiteLogResponse)
def get_site_logs(
    domain: str,
    session: Annotated[dict, Depends(get_current_session)],
    kind: Annotated[str, Query(description="`access` or `error`.")] = "access",
    lines: Annotated[int, Query(ge=1, le=MAX_LINES)] = 200,
    contains: Annotated[
        str | None, Query(max_length=200, description="Plain text to look for.")
    ] = None,
) -> SiteLogResponse:
    """
    Read the end of a site's access or error log.

    Args:
        domain: The site's domain.
        session: The authenticated session.
        kind: ``access`` or ``error``.
        lines: How many lines at most.
        contains: Keep only lines with this text, whatever its case.

    Returns:
        The log; ``available`` is false when the web server wrote none.

    Raises:
        HTTPException: 404 when there is no such application, 400 for a kind
            that is not one of the two.
    """
    validated = strict_domain(domain)
    app = scoped_store().get_app(validated)
    if app is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail=f"kind must be one of: {', '.join(KINDS)}")
    reader = SiteLogReader(app.webserver, scrub=scrubber_for(validated).scrub)
    log = reader.read(validated, kind, lines=lines, contains=contains)
    return SiteLogResponse(
        domain=log.domain,
        kind=log.kind,
        path=log.path,
        available=log.available,
        lines=log.lines,
        size_bytes=log.size_bytes,
        truncated=log.truncated,
        matched=log.matched,
    )
