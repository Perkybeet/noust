# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Adopting a site that already runs on nginx or Apache (checklist B-85).

``GET /api/apps/native-sites`` lists the sites on the server that no application records and says
what each is: a unit the operator wrote behind the web server, PHP, or files.
``POST /api/apps/adopt-site`` registers one as it is. A client of
:mod:`noust.deployers.native_adopt`, like ``noust app discover`` and ``noust app adopt-site``:
``preview: true`` answers what the adoption would record and changes nothing, which is what the
console shows before asking. Adopting writes only the store, but it makes a unit Noust may restart,
so it needs sudo mode.

``GET /api/apps/{domain}/adoption`` says what an adopted application is, which the console shows
on its page: the unit, the site and the directory that are the operator's.

Mounted before ``/apps/{domain}``, which would otherwise read ``native-sites`` as a domain.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.deployers import native_adopt
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class NativeSiteModel(BaseModel):
    """A site on the server that no application records."""

    site: str = Field(description="The file in the web server's sites directory")
    webserver: Literal["nginx", "apache"]
    config_path: str
    enabled: bool
    domains: list[str] = Field(description="The names it answers on")
    kind: Literal["proxy", "php", "static"] | None = Field(
        description="A unit behind the web server, PHP, or files; None when it cannot be adopted"
    )
    root: str | None = Field(description="The directory it serves, when it names one")
    upstream: str | None = Field(description="What it hands requests to")
    port: int | None = Field(description="The local port it proxies to")
    unit: str | None = Field(description="The unit that listens on that port, proposed")
    unit_active: bool | None
    ssl: bool
    adoptable: bool
    reason: str | None = Field(description="Why it cannot be adopted, or what to know about it")


class NativeSitesResponse(BaseModel):
    """The sites that could be adopted."""

    sites: list[NativeSiteModel]


class AdoptSiteRequest(BaseModel):
    """The site to adopt, and what is not found by looking."""

    domain: str = Field(description="The domain the site answers on")
    site: str | None = Field(
        default=None, description="The site file serving the domain; found when omitted"
    )
    unit: str | None = Field(
        default=None,
        description="The unit the site is in front of; the one that listens on its port when omitted",
    )
    path: str | None = Field(
        default=None,
        description="The directory of its files; the unit's WorkingDirectory or the site's root",
    )
    preview: bool = Field(default=False, description="Answer what would be done; change nothing")


class AdoptSiteResponse(BaseModel):
    """What an adoption found, and whether it was recorded."""

    domain: str
    app_name: str
    kind: Literal["proxy", "php", "static"]
    site: str
    site_name: str | None = Field(
        description="That file's name, recorded because it is not the domain"
    )
    webserver: Literal["nginx", "apache"]
    enabled: bool
    ssl: bool
    root: str
    port: int | None
    upstream: str | None
    unit: str | None = Field(description="The operator's unit; none for PHP and static sites")
    unit_active: bool | None
    warnings: list[str]
    adopted: bool = Field(description="False for a preview or a rehearsal")


@router.get("/native-sites", response_model=NativeSitesResponse)
def list_native_sites(
    session: Annotated[dict, Depends(get_current_session)],
) -> NativeSitesResponse:
    """
    List the sites that no application records, and what each one is.

    Args:
        session: The authenticated session.

    Returns:
        One entry per site the operator wrote, adoptable or with the reason it is not.
    """
    return NativeSitesResponse(
        sites=[NativeSiteModel(**site.to_dict()) for site in native_adopt.discover()]
    )


@router.post("/adopt-site", response_model=AdoptSiteResponse)
def adopt_site(
    body: AdoptSiteRequest, session: Annotated[dict, Depends(require_elevated)]
) -> AdoptSiteResponse:
    """
    Adopt a site that already runs, or preview the adoption.

    Args:
        body: The site.
        session: The authenticated, elevated session.

    Returns:
        What was found and done.

    Raises:
        DeploymentError: The domain is an application, no site serves it, the site serves
            nothing Noust can adopt, the unit cannot be one, or the directory is not the site's.
    """
    plan = native_adopt.plan_adoption(body.domain, site=body.site, unit=body.unit, path=body.path)
    if body.preview:
        return AdoptSiteResponse(**plan.to_dict(), adopted=False)
    result = native_adopt.adopt(plan, actor=actor_label(session))
    return AdoptSiteResponse(**result.to_dict())


class AdoptionResponse(BaseModel):
    """What an adopted application is: the operator's unit, site and directory."""

    kind: Literal["proxy", "php", "static"]
    unit: str | None = Field(
        description="The unit Noust starts, stops and restarts, never rewrites"
    )
    webserver: Literal["nginx", "apache"]
    site_file: str = Field(description="The operator's site file, never rewritten")
    root: str | None
    upstream: str | None
    adopted_by: str | None
    adopted_at: str | None


@router.get("/{domain}/adoption", response_model=AdoptionResponse)
def get_adoption(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AdoptionResponse:
    """
    Say what an adopted application is.

    Args:
        domain: The application's domain.
        session: The authenticated session.

    Returns:
        Its unit, site and directory.

    Raises:
        HTTPException: 404 when the application does not exist, is outside the caller's teams, or
            was not adopted.
    """
    validated = strict_domain(domain)
    app = scoped_store().get_app(validated)
    record = native_adopt.adoption_of(validated) if app is not None else None
    if app is None or record is None:
        raise HTTPException(status_code=404, detail=f"No adopted site: {validated}")
    return AdoptionResponse(**record.__dict__)
