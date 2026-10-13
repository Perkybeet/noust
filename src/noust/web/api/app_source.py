# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An application's source options: how its repository is fetched and when a push deploys it.

A client of :mod:`noust.core.app_source_store`, like ``noust app source``:
``GET`` says what applies (submodules, Git LFS, shallow clone, watch paths,
skip markers), ``PUT`` changes the keys it is given and leaves the others, and
``DELETE`` goes back to the defaults. Validation and the audit record live in
the store module, so the console, the CLI and ``noust apply`` cannot disagree.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import app_source_store
from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.auth import actor_label
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class SourceOptionsResponse(BaseModel):
    """An application's source options."""

    domain: str
    submodules: bool = Field(
        description="Initialise the submodules and put their files in the release"
    )
    lfs: Literal["auto", "on", "off"] = Field(
        description="Git LFS: auto fetches the files when the repository tracks any, on always "
        "does, off never does"
    )
    shallow: bool = Field(description="Fetch only the latest commit (off keeps the full history)")
    watch_paths: list[str] = Field(
        description="Globs a push must touch to deploy; '!' excludes and the last match wins. "
        "Empty means every push deploys"
    )
    skip_markers: bool = Field(
        description="Skip a push whose commits all say [skip ci] or [skip cd]"
    )
    customised: bool = Field(description="Whether the operator changed anything from the defaults")


class SourceOptionsRequest(BaseModel):
    """Source options to change. A key left out keeps its value."""

    submodules: bool | None = None
    lfs: Literal["auto", "on", "off"] | None = None
    shallow: bool | None = None
    watch_paths: list[str] | None = None
    skip_markers: bool | None = None


def _response(domain: str, options: app_source_store.SourceOptions) -> SourceOptionsResponse:
    """
    Args:
        domain: The application.
        options: Its options.

    Returns:
        The API model.
    """
    return SourceOptionsResponse(domain=domain, **options.to_dict())


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


@router.get("/{domain}/source-options", response_model=SourceOptionsResponse)
def get_source_options(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SourceOptionsResponse:
    """
    Show how an application's repository is fetched and when a push deploys it.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The options in force.

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    return _response(validated, app_source_store.get_options(validated))


@router.put("/{domain}/source-options", response_model=SourceOptionsResponse)
def put_source_options(
    domain: str,
    body: SourceOptionsRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> SourceOptionsResponse:
    """
    Change some of an application's source options, keeping the others.

    The change applies to the next fetch and to the next delivery; nothing is
    rebuilt now. Every value is validated before anything is stored.

    Args:
        domain: Domain of the application.
        body: The keys to change.
        session: The authenticated session.

    Returns:
        The options now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A value is not acceptable (400, with the field).
    """
    validated = _known(domain)
    given = {key: value for key, value in dump_model(body).items() if value is not None}
    if not given:
        raise ValidationError(
            "No source option was given",
            details="Give at least one of: " + ", ".join(app_source_store.OPTION_KEYS) + ".",
            field="source-options",
        )
    options = app_source_store.set_options(validated, actor=actor_label(session), **given)
    return _response(validated, options)


@router.delete("/{domain}/source-options", response_model=SourceOptionsResponse)
def delete_source_options(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> SourceOptionsResponse:
    """
    Go back to the default source options.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The options now in force (the defaults).

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    app_source_store.reset_options(validated, actor=actor_label(session))
    return _response(validated, app_source_store.get_options(validated))
