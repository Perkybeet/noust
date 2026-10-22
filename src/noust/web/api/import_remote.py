# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Bringing an application over from Coolify, Dokploy or CapRover, through their API.

A client of :mod:`noust.deployers.importers`, like ``noust import --from
coolify``. Two calls and no state: ``POST /api/import/applications`` lists what
a server has (its address and a read-only token in the body), and ``POST
/api/import/proposal`` reads one application and answers the proposal and the
export document it becomes, which the console's import review takes from there
(``POST /api/apps/import``). Nothing is written to the other platform, and the
token is used for the length of the request: it is not stored, logged or
audited.

Sudo mode asks for itself: the request makes this server contact an address an
operator typed, with a credential.
"""

from __future__ import annotations

import dataclasses
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from noust.core.exceptions import ValidationError
from noust.deployers.app_export import proposal_document
from noust.deployers.importers import REMOTE_PLATFORMS
from noust.deployers.importers.base import RemoteApplication
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.api.platform_proposal import PlatformProposalResponse, platform_proposal_response

router = APIRouter(route_class=NoustErrorRoute)


class RemoteServer(BaseModel):
    """The server to read, and the token to read it with."""

    platform: str = Field(description="coolify, dokploy or caprover")
    url: str = Field(description="Its address, https://coolify.example.com", max_length=300)
    token: str = Field(
        description="A read-only API token (CapRover: the dashboard's password). Not stored",
        max_length=512,
    )


class RemoteApplicationOut(BaseModel):
    """An application on the other platform's server."""

    ref: str
    name: str
    build: str = ""
    source: str = ""
    domains: list[str] = Field(default_factory=list)
    project: str = ""


class RemoteApplicationsResponse(BaseModel):
    """What the other server has."""

    platform: str
    applications: list[RemoteApplicationOut]


class RemoteProposalRequest(RemoteServer):
    """One application to read."""

    ref: str = Field(description="Its id on the other server (from the list)", max_length=200)
    domain: str | None = Field(
        default=None,
        description="The domain to create it on here; the first one it answered on there if left out",
        max_length=253,
    )
    source: str | None = Field(
        default=None, description="The repository to deploy, when the platform names none"
    )


class RemoteProposalResponse(BaseModel):
    """One application, as Noust would deploy it."""

    proposal: PlatformProposalResponse
    document: dict[str, Any] = Field(
        description="The export document it becomes: review it, then POST /api/apps/import"
    )
    deployable: bool = Field(
        description="false for an image, a Dockerfile or a Compose application: "
        "the proposal carries the noust command that creates it"
    )


def _module(platform: str) -> Any:
    """
    Find the importer of a platform.

    Args:
        platform: The platform's name.

    Returns:
        Its module.

    Raises:
        ValidationError: There is none that reads an API.
    """
    module = REMOTE_PLATFORMS.get(platform)
    if module is None:
        raise ValidationError(
            f"{platform!r} is not read through an API",
            details=f"Choose one of: {', '.join(REMOTE_PLATFORMS)}.",
            field="platform",
        )
    return module


def _out(item: RemoteApplication) -> RemoteApplicationOut:
    """
    Args:
        item: One application of the server.

    Returns:
        Its API shape.
    """
    data = dataclasses.asdict(item)
    data["domains"] = list(item.domains)
    return RemoteApplicationOut(**data)


@router.post("/applications", response_model=RemoteApplicationsResponse)
async def list_remote_applications(
    body: RemoteServer, session: Annotated[dict, Depends(require_elevated)]
) -> RemoteApplicationsResponse:
    """
    List the applications of a Coolify or Dokploy server.

    Args:
        body: The server and its read-only token.
        session: An elevated session.

    Returns:
        What it has.

    Raises:
        ValidationError: The platform, the address or the token is not valid.
        IntegrationError: The server cannot be reached or refuses the token.
    """
    module = _module(body.platform)

    def read() -> list[RemoteApplication]:
        client = module.connect(body.url, body.token)
        found: list[RemoteApplication] = module.list_applications(client)
        return found

    applications = await run_in_threadpool(read)
    return RemoteApplicationsResponse(
        platform=body.platform, applications=[_out(item) for item in applications]
    )


@router.post("/proposal", response_model=RemoteProposalResponse)
async def read_remote_application(
    body: RemoteProposalRequest, session: Annotated[dict, Depends(require_elevated)]
) -> RemoteProposalResponse:
    """
    Read one application of a Coolify or Dokploy server as a proposal.

    Args:
        body: The server, its token, the application and, optionally, the
            domain to create it on here.
        session: An elevated session.

    Returns:
        The proposal, and the export document it becomes.

    Raises:
        ValidationError: The platform, the address or the token is not valid,
            or no domain can be derived.
        IntegrationError: The server cannot be read.
    """
    module = _module(body.platform)

    def read() -> Any:
        return module.read_application(module.connect(body.url, body.token), body.ref)

    proposal = await run_in_threadpool(read)
    domain = (body.domain or (proposal.domains[0] if proposal.domains else "")).strip()
    if not domain:
        raise ValidationError(
            "It answered on no domain there, so there is none to propose",
            details="Give the domain to create it on here.",
            field="domain",
        )
    document = proposal_document(
        proposal, domain=domain, source=body.source or proposal.source or ""
    )
    deployable = not (proposal.equivalent and proposal.app_type in ("container", "docker-compose"))
    return RemoteProposalResponse(
        proposal=cast(PlatformProposalResponse, platform_proposal_response(proposal)),
        document=document,
        deployable=deployable,
    )
