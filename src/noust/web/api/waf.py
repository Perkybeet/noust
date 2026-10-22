# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The web application firewall over the API.

A thin translation of :mod:`noust.deployers.access` (what a site's policy says: the mode and the
rules left out go through ``PUT /api/apps/{domain}/policy`` like every other field) and of
:mod:`noust.managers.waf` (what the server has, what the rules saw lately). Installing the packages
is the server's: sudo mode, and the instance scope, because it runs the package manager as root.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from noust.core.teams import scoped_store
from noust.deployers import access
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.api.scope import require_instance_scope
from noust.web.permissions import Permission

#: Mounted at ``/apps``: the paths under ``/{domain}/waf``.
router = APIRouter(route_class=NoustErrorRoute)

#: Mounted at ``/waf``: what belongs to the server.
server_router = APIRouter(route_class=NoustErrorRoute)


class WafModel(BaseModel):
    """
    Whether this server can run the WAF, and what stands in the way.

    Attributes:
        available: nginx has the ModSecurity module loaded and the Core Rule Set is installed.
        webserver: ``nginx`` or ``apache``.
        module_loaded: nginx has loaded the module.
        module_installed: The module's file is on disk.
        rules: The Core Rule Set was found.
        problem: What is missing, when something is.
        hint: How to fix it.
        installable: Noust can install the packages here (Debian and Ubuntu).
        packages: The packages it would install.
        anchor: The shared rules are loaded.
    """

    available: bool
    webserver: str
    module_loaded: bool
    module_installed: bool
    rules: bool
    problem: str = ""
    hint: str = ""
    installable: bool
    packages: list[str]
    anchor: bool


class RuleHit(BaseModel):
    """
    A rule that matched a request.

    Attributes:
        id: The rule's number.
        message: What it says.
        severity: Its severity, 0 (emergency) to 7.
    """

    id: str
    message: str
    severity: str = ""


class WafEventModel(BaseModel):
    """
    A request the rules judged.

    Attributes:
        time: When, as ModSecurity wrote it.
        client: The client address.
        host: The host the request named.
        method: The HTTP method.
        uri: The request target.
        status: The status the site answered.
        blocked: The rules refused it.
        would_block: The rules scored it high enough to refuse, and the site is in detect mode:
            block mode would have refused it.
        rules: The rules that matched.
    """

    time: str
    client: str
    host: str
    method: str
    uri: str
    status: int
    blocked: bool
    would_block: bool
    rules: list[RuleHit]


class NoisyRule(BaseModel):
    """
    A rule that matched often.

    Attributes:
        id: The rule's number.
        message: What it says.
        count: How many of the requests read it matched.
    """

    id: str
    message: str
    count: int


class WafEventsResponse(BaseModel):
    """
    What the rules judged lately for an application.

    Attributes:
        events: The requests, newest first.
        noisy: The rules that matched most often: the ones to look at when a legitimate
            request is refused, and to leave out of the site if they are wrong for it.
    """

    events: list[WafEventModel]
    noisy: list[NoisyRule]


class InstallResponse(BaseModel):
    """
    What installing the WAF did.

    Attributes:
        packages: The packages installed.
    """

    packages: list[str]


def _require_app(domain: str) -> str:
    """
    Validate an application's domain and require that it is deployed.

    Args:
        domain: The domain from the path.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404 when nothing is deployed there.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/waf", response_model=WafModel)
def get_waf(
    domain: str, session: Annotated[dict[str, Any], Depends(get_current_session)]
) -> WafModel:
    """
    Say whether this application's server can run the WAF.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.

    Returns:
        What is available, what is missing and how to fix it.
    """
    return WafModel(**access.waf_support(_require_app(domain)).to_dict())


@router.get("/{domain}/waf/events", response_model=WafEventsResponse)
def get_waf_events(
    domain: str,
    session: Annotated[dict[str, Any], Depends(get_current_session)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> WafEventsResponse:
    """
    Show what the rules judged lately for this application.

    Args:
        domain: The application's primary domain.
        session: Authenticated session, injected.
        limit: The most requests returned.

    Returns:
        The requests, newest first, and the rules that matched most often.
    """
    events, noisy = access.waf_events(_require_app(domain), limit=limit)
    return WafEventsResponse(
        events=[WafEventModel(**event.to_dict()) for event in events],
        noisy=[
            NoisyRule(id=rule_id, message=message, count=count) for rule_id, message, count in noisy
        ],
    )


@server_router.post("/install", response_model=InstallResponse)
def post_install(session: Annotated[dict[str, Any], Depends(require_elevated)]) -> InstallResponse:
    """
    Install nginx's ModSecurity module and the OWASP Core Rule Set from the distribution.

    Needs sudo mode and the instance scope: it runs the package manager as root.

    Args:
        session: Authenticated and elevated session, injected.

    Returns:
        The packages installed.

    Raises:
        UnsupportedHostError: The server is not Debian or Ubuntu.
    """
    require_instance_scope(Permission.SERVER_MANAGE)
    return InstallResponse(packages=access.install_waf())
