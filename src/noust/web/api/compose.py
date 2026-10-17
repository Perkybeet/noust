# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose document typed into the console, looked at before it runs, and a Coolify template
turned into one.

``POST /api/compose/analyze`` is :func:`noust.deployers.compose_paste.analyze_document`
and ``noust compose check``: the services, the variables the document reads
(which have a default and which need a value) and what refuses or warns about
it. It runs nothing and writes nothing. Deploying the document is
``POST /api/apps`` with ``compose_document``, which analyses it again - the
preview is a convenience, the guard is the deployment's.

``POST /api/compose/import`` is :func:`noust.recipes.coolify.convert` and ``noust compose import``:
a Coolify service template in, a Compose document out, with the values of the variables Coolify
would have filled in (made here, so the secrets are new) and what was changed or left. It writes
nothing; the document goes through ``POST /api/compose/analyze`` and ``POST /api/apps`` like any
other, with the values as ``env_vars``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.deployers.compose_paste import MAX_DOCUMENT_BYTES, PastedCompose, analyze_document
from noust.recipes.coolify import MAX_TEMPLATE_BYTES, convert
from noust.validators.domain import validate_domain
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
    return _analysis_out(analyze_document(body.document))


def _analysis_out(found: PastedCompose) -> ComposeAnalysisOut:
    """
    Args:
        found: What :func:`~noust.deployers.compose_paste.analyze_document` made of a document.

    Returns:
        The same as the API's model.
    """
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


class CoolifyImportRequest(BaseModel):
    """
    A Coolify service template to convert.

    Attributes:
        document: The template's text, up to 256 KiB.
        domain: The name the stack will be served on; without one the addresses are left to give.
        port: The host port the web service is published on (loopback); the container's own
            port by default.
    """

    document: str = Field(max_length=MAX_TEMPLATE_BYTES)
    domain: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)


class ConvertedVariableOut(BaseModel):
    """
    A variable the converted document reads that Coolify would have filled in.

    Attributes:
        name: The variable.
        origin: ``generated``, ``domain`` or ``ask``.
        secret: Its value must not be shown.
        note: A sentence for the operator.
    """

    name: str
    origin: str
    secret: bool = False
    note: str = ""


class CoolifyImportOut(BaseModel):
    """
    A template, converted.

    Attributes:
        document: The Compose document.
        values: The values of the variables made here, by name: new secrets among them. Give
            them as ``env_vars`` when the document is deployed.
        variables: How each variable gets its value.
        routed_service: The service the domain goes to.
        routed_port: Its container port.
        metadata: The template's header.
        adjustments: What was changed to make it a Noust document.
        warnings: What was not translated and why.
        analysis: The same look any pasted document gets.
    """

    document: str
    values: dict[str, str]
    variables: list[ConvertedVariableOut]
    routed_service: str | None = None
    routed_port: int | None = None
    metadata: dict[str, str]
    adjustments: list[str]
    warnings: list[str]
    analysis: ComposeAnalysisOut


@router.post("/import", response_model=CoolifyImportOut)
def import_coolify(
    body: CoolifyImportRequest, session: Annotated[dict, Depends(get_current_session)]
) -> CoolifyImportOut:
    """
    Convert a Coolify service template to a Compose document Noust deploys.

    Nothing is run, fetched or saved. The values it makes (generated secrets, the addresses of the
    domain) are in the answer and nowhere else: a second request makes new ones.

    Args:
        body: The template, and optionally the domain and the host port.
        session: The authenticated session.

    Returns:
        The document, the values, and what was changed or left.

    Raises:
        ValidationError: It is not a Compose template, or the domain is not a name (400).
    """
    domain = validate_domain(body.domain) if body.domain else None
    result = convert(body.document, domain=domain, port=body.port)
    found = analyze_document(result.document)
    return CoolifyImportOut(
        document=result.document,
        values=result.values,
        variables=[ConvertedVariableOut(**variable.to_dict()) for variable in result.variables],
        routed_service=result.routed[0] if result.routed else None,
        routed_port=result.routed[1] if result.routed else None,
        metadata=result.metadata,
        adjustments=result.adjustments,
        warnings=result.warnings,
        analysis=_analysis_out(found),
    )
