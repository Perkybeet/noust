# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's build block: see it, set the operator's own, clear it.

A client of :mod:`noust.deployers.helpers.build_config`, like ``noust app
build``: ``GET`` says which commands, base directory, publish directory and
runtime the next deployment uses and where they come from (the operator's,
which win whole, or the running code's ``noust.yaml``); ``PUT`` stores the
operator's block, validated first, and ``DELETE`` removes it so the
repository's apply again. The block's commands run in the build sandbox and as
the application's unit, so writing one is root-equivalent and asks for sudo
mode; the module records every change in the audit trail.
"""

from __future__ import annotations

from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers.helpers import build_cache
from noust.deployers.helpers.build_config import (
    BUILD_KEYS,
    AppBuild,
    describe_build,
    set_operator_build,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label

router = APIRouter(route_class=NoustErrorRoute)


class AppBuildResponse(BaseModel):
    """The build block an application's next deployment uses, and where it comes from."""

    domain: str
    source: str = Field(
        description="operator: the operator's own, replacing noust.yaml; repo: the "
        "noust.yaml of the code that runs now; none"
    )
    base_directory: str | None = Field(
        default=None, description="The subdirectory of the repository the project lives in"
    )
    install: str | None = Field(default=None, description="Replaces the type's install command")
    build: str | None = Field(default=None, description="Replaces the type's build command")
    start: str | None = Field(
        default=None, description="Replaces the type's start command; ${PORT} is the port"
    )
    publish_directory: str | None = Field(
        default=None, description="Where a static build leaves the files the web server serves"
    )
    runtime: str | None = Field(default=None, description="The pinned runtime, such as node@22")
    cache: bool | None = Field(
        default=None,
        description="false builds every deployment without the build cache; unset keeps it",
    )
    document: str | None = Field(
        default=None, description="The operator's document as stored, when there is one"
    )
    repository_error: str | None = Field(
        default=None,
        description="Why the running code's noust.yaml is not valid, when it is not; the next "
        "deployment fails with it",
    )
    applied: dict[str, Any] | None = Field(
        default=None,
        description="What the application's unit was last written from (command, directory, "
        "PATH and runtime), when a build block or a pinned runtime shaped it",
    )


class AppBuildRequest(BaseModel):
    """The operator's build block. A key left out is not set: the block replaces the repository's whole."""

    base_directory: str | None = Field(default=None, max_length=200)
    install: str | None = Field(default=None, max_length=4096)
    build: str | None = Field(default=None, max_length=4096)
    start: str | None = Field(default=None, max_length=4096)
    publish_directory: str | None = Field(default=None, max_length=200)
    runtime: str | None = Field(default=None, max_length=64)
    cache: bool | None = None


def _response(described: AppBuild) -> AppBuildResponse:
    """
    Args:
        described: What applies.

    Returns:
        The API model.
    """
    data = described.to_dict()
    return AppBuildResponse(**data)


def _known(domain: str) -> str:
    """
    Validate a domain and refuse one nothing is deployed at.

    Args:
        domain: The domain as the client sent it.

    Returns:
        The validated domain.

    Raises:
        HTTPException: 404.
    """
    validated = strict_domain(domain)
    if scoped_store().get_app(validated) is None:
        raise HTTPException(status_code=404, detail=f"Application not found: {validated}")
    return validated


@router.get("/{domain}/build", response_model=AppBuildResponse)
def get_app_build(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppBuildResponse:
    """
    Show the build block the next deployment of an application uses. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The block and where it comes from.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return _response(describe_build(_known(domain)))


@router.put("/{domain}/build", response_model=AppBuildResponse)
def put_app_build(
    domain: str,
    body: AppBuildRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> AppBuildResponse:
    """
    Set the operator's build block, replacing the repository's noust.yaml whole.

    The block is validated before anything is stored; a refusal names the
    field (``build.base_directory``). Its commands run in the build sandbox and
    as the application's unit, so this needs sudo mode. It takes effect on the
    next deployment or update, which also writes the unit again when the start
    command, the directory or the runtime changed.

    Args:
        domain: Domain of the application.
        body: The block.
        session: The authenticated, elevated session.

    Returns:
        The block now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: The block is not valid, or sets what the application's
            type cannot do (400, with the field).
    """
    validated = _known(domain)
    given = {key: getattr(body, key) for key in BUILD_KEYS if getattr(body, key) is not None}
    if not given:
        raise ValidationError(
            "The build block sets nothing",
            details="Give at least one of: " + ", ".join(BUILD_KEYS) + ". To go back to the "
            "repository's noust.yaml, delete the operator's block.",
            field="build",
        )
    document = yaml.safe_dump({"build": given}, sort_keys=False)
    set_operator_build(validated, document, actor=actor_label(session))
    return _response(describe_build(validated))


@router.delete("/{domain}/build", response_model=AppBuildResponse)
def delete_app_build(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> AppBuildResponse:
    """
    Remove the operator's build block, so the repository's noust.yaml applies again.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The block now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    set_operator_build(validated, None, actor=actor_label(session))
    return _response(describe_build(validated))


class BuildCacheResponse(BaseModel):
    """What an application's build cache holds."""

    domain: str
    path: str = Field(description="Where the package managers' cache for this application is kept")
    exists: bool
    size_bytes: int = Field(description="Bytes it holds; when emptied, the bytes that were freed")


@router.get("/{domain}/build-cache", response_model=BuildCacheResponse)
def get_build_cache(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> BuildCacheResponse:
    """
    Say how much the package managers keep between this application's builds.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The cache.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return BuildCacheResponse(**build_cache.describe(_known(domain)).to_dict())


@router.delete("/{domain}/build-cache", response_model=BuildCacheResponse)
def delete_build_cache(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> BuildCacheResponse:
    """
    Empty the build cache of an application; the next build downloads what it needs again.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The cache as it was: ``size_bytes`` is what was freed.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    return BuildCacheResponse(**build_cache.clear(_known(domain)).to_dict())
