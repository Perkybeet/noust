# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A Docker Compose stack's file, profiles and extra arguments: see them, change them.

A client of :mod:`noust.deployers.helpers.compose_settings`, like ``noust app
compose``. ``GET`` says what the operator chose (nothing, for a stack that
still runs the file and profiles its unit recorded), ``PUT`` changes the keys it
is given and writes the unit again when the file or the profiles changed, and
``DELETE`` forgets the choices. The arguments are a closed list of flags of
``docker compose build`` and ``up`` (see :mod:`noust.core.app_compose_store`),
never a command line given to a shell. They run as root, so changing them asks
for sudo mode.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core import app_compose_store
from noust.core.app_compose_store import BUILD_SWITCHES, BUILD_VALUED, UP_SWITCHES, UP_VALUED
from noust.core.exceptions import ValidationError
from noust.core.teams import scoped_store
from noust.deployers.helpers import compose_variables
from noust.deployers.helpers.compose_settings import (
    change_compose,
    describe_compose,
    reset_compose,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, require_elevated, strict_domain
from noust.web.auth import actor_label
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class ComposeSettingsResponse(BaseModel):
    """What a Compose stack is run with."""

    domain: str
    compose_file: str | None = Field(
        default=None, description="The compose file, relative to the application; null finds one"
    )
    profiles: list[str] = Field(default_factory=list, description="The profiles to activate")
    build_args: list[str] = Field(
        default_factory=list, description="Extra arguments of docker compose build"
    )
    up_args: list[str] = Field(
        default_factory=list, description="Extra arguments of docker compose up"
    )
    services: list[str] = Field(
        default_factory=list, description="The services to build and start; empty for all"
    )
    customised: bool = Field(description="Whether the operator chose anything")
    allowed_build_args: list[str] = Field(description="The flags build_args may use")
    allowed_up_args: list[str] = Field(description="The flags up_args may use")


class ComposeSettingsRequest(BaseModel):
    """Settings to change. A key left out keeps its value; an empty list clears one."""

    compose_file: str | None = Field(default=None, max_length=300)
    profiles: list[str] | None = None
    build_args: list[str] | None = None
    up_args: list[str] | None = None
    services: list[str] | None = None


class ComposeVariableModel(BaseModel):
    """A variable the compose file asks for, and whether the stack has it. Never its value."""

    name: str
    status: str = Field(
        description="set (a value exists), default (the file has a default), unset (no value and "
        "no default: Compose uses an empty string) or missing (the file requires it)"
    )
    required: bool = Field(description="Whether the file requires a value (${NAME:?message})")
    default: str | None = Field(default=None, description="The default the file gives")
    message: str = Field(
        default="", description="What the file says when a required value is missing"
    )
    source: str | None = Field(
        default=None, description="Where the value comes from when set: env (the .env) or process"
    )
    used_in: list[str] = Field(
        default_factory=list, description="The services, or top-level keys, that use it"
    )


class ComposeVariablesResponse(BaseModel):
    """The variables a stack's compose file asks for."""

    domain: str
    file: str = Field(description="The compose file read")
    env_file: str | None = Field(default=None, description="The .env the values were read from")
    variables: list[ComposeVariableModel] = Field(default_factory=list)
    missing: list[str] = Field(
        default_factory=list, description="The required variables nothing gives: a deploy refuses"
    )
    unset: list[str] = Field(
        default_factory=list, description="The variables with no value and no default"
    )


def _response(domain: str, options: app_compose_store.ComposeOptions) -> ComposeSettingsResponse:
    """
    Args:
        domain: The stack.
        options: Its options.

    Returns:
        The API model.
    """
    return ComposeSettingsResponse(
        domain=domain,
        **options.to_dict(),
        allowed_build_args=sorted(BUILD_SWITCHES | set(BUILD_VALUED)),
        allowed_up_args=sorted(UP_SWITCHES | set(UP_VALUED)),
    )


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


@router.get("/{domain}/compose", response_model=ComposeSettingsResponse)
def get_compose_settings(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ComposeSettingsResponse:
    """
    Show what a Compose stack is run with.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The options, and the flags the arguments may use.

    Raises:
        HTTPException: 404 when the application is unknown.
        NoustError: It is not a Compose stack.
    """
    validated = _known(domain)
    return _response(validated, describe_compose(validated))


@router.get("/{domain}/compose/variables", response_model=ComposeVariablesResponse)
def get_compose_variables(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ComposeVariablesResponse:
    """
    Show the variables a stack's compose file asks for and which of them it has.

    Reads the file and the ``.env`` and runs nothing; a value is never part of the answer, only
    whether there is one. Edit them with ``PUT /api/apps/{domain}/env``.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        Every variable with its state, and the ones a deploy would refuse over.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: It is not a Compose stack (400).
        DeploymentError: Its compose file cannot be found or read.
    """
    validated = _known(domain)
    app = scoped_store().get_app(validated)
    if app is None or app.app_type != "docker-compose":
        raise ValidationError(
            f"{validated} is not a Docker Compose application",
            details="Only a stack deployed with --type docker-compose has a compose file.",
            field="domain",
        )
    report = compose_variables.report_for(app)
    return ComposeVariablesResponse(domain=validated, **report.to_dict())


@router.put("/{domain}/compose", response_model=ComposeSettingsResponse)
def put_compose_settings(
    domain: str,
    body: ComposeSettingsRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ComposeSettingsResponse:
    """
    Change what a Compose stack is run with, keeping the keys that are not given.

    A new file or new profiles are written to the stack's unit as well; nothing
    is rebuilt now, the next update uses the new settings.

    Args:
        domain: Domain of the application.
        body: The keys to change.
        session: An elevated session: the arguments run as root.

    Returns:
        The options now in force.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: A value is not acceptable (400, with the field).
        DeploymentError: The compose file is not in the stack's directory.
        AppBusyError: Another operation is running on the stack.
    """
    validated = _known(domain)
    given = {key: value for key, value in dump_model(body).items() if value is not None}
    if not given:
        raise ValidationError(
            "No compose setting was given",
            details="Give at least one of: compose_file, profiles, build_args, up_args, services.",
            field="compose",
        )
    options = change_compose(validated, given, actor=actor_label(session))
    return _response(validated, options)


@router.delete("/{domain}/compose", response_model=ComposeSettingsResponse)
def delete_compose_settings(
    domain: str, session: Annotated[dict, Depends(require_elevated)]
) -> ComposeSettingsResponse:
    """
    Forget what the operator chose; the unit's own record of the file applies again.

    Args:
        domain: Domain of the application.
        session: An elevated session.

    Returns:
        The options now in force (none).

    Raises:
        HTTPException: 404 when the application is unknown.
    """
    validated = _known(domain)
    reset_compose(validated)
    return _response(validated, describe_compose(validated))
