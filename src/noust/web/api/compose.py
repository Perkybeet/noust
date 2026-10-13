# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose document typed into the console, looked at before it runs.

``POST /api/compose/analyze`` is :func:`noust.deployers.compose_paste.analyze_document`
and ``noust compose check``: the services, the variables the document reads
(which have a default and which need a value) and what refuses or warns about
it. It runs nothing and writes nothing. Deploying the document is
``POST /api/apps`` with ``compose_document``, which analyses it again - the
preview is a convenience, the guard is the deployment's.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.deployers.compose_paste import MAX_DOCUMENT_BYTES, analyze_document
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class ComposeAnalyzeRequest(BaseModel):
    """
    A Compose document to look at.

    Attributes:
        document: The document's text, up to 256 KiB.
    """

    document: str = Field(max_length=MAX_DOCUMENT_BYTES)


class ComposeServiceOut(BaseModel):
    """
    One service of the document.

    Attributes:
        name: The service.
        image: Its image as written; None when it builds one.
        publishes: The host ports it publishes (``8080:80/tcp``; ``*`` when
            Docker picks the host port).
        depends_on: The services it waits for.
    """

    name: str
    image: str | None = None
    publishes: list[str] = []
    depends_on: list[str] = []


class ComposeVariableOut(BaseModel):
    """
    One variable the document reads.

    Attributes:
        name: The variable.
        default: The value used when none is given, if the document names one.
        required: Whether a value should be given: it has no default, or the
            document stops without one.
        message: The document's own message for a required variable.
        services: The services that use it.
    """

    name: str
    default: str | None = None
    required: bool = False
    message: str | None = None
    services: list[str] = []


class ComposeAnalysisOut(BaseModel):
    """
    What a document is, before it runs.

    Attributes:
        deployable: Whether nothing refuses it.
        services: Its services.
        variables: Its variables.
        web: The services that publish a port.
        refused: Why it cannot be deployed as it is.
        warned: What is worth knowing.
    """

    deployable: bool
    services: list[ComposeServiceOut]
    variables: list[ComposeVariableOut]
    web: list[str]
    refused: list[str]
    warned: list[str]


@router.post("/analyze", response_model=ComposeAnalysisOut)
def analyze_compose(
    body: ComposeAnalyzeRequest, session: Annotated[dict, Depends(get_current_session)]
) -> ComposeAnalysisOut:
    """
    Look at a Compose document without running or saving it.

    Args:
        body: The document.
        session: The authenticated session.

    Returns:
        Its services and variables, and what refuses or warns about it.

    Raises:
        ValidationError: It is not a Compose document: too large, not YAML,
            no services (400).
    """
    found = analyze_document(body.document)
    return ComposeAnalysisOut(
        deployable=found.deployable,
        services=[
            ComposeServiceOut(
                name=item.name,
                image=item.image,
                publishes=list(item.publishes),
                depends_on=list(item.depends_on),
            )
            for item in found.services
        ],
        variables=[
            ComposeVariableOut(
                name=item.name,
                default=item.default,
                required=item.required,
                message=item.message,
                services=list(item.services),
            )
            for item in found.variables
        ],
        web=list(found.web),
        refused=list(found.refused),
        warned=list(found.warned),
    )
