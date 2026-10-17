# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A container application's image, build and runtime settings: see them, change them.

A client of :mod:`noust.deployers.helpers.container_settings`, like
``noust app container``: ``GET`` says how the application is built and run
(never the registry's password), and ``PUT`` changes what the request names.
A change is written to the store and takes effect with the next deployment
(``POST /api/apps/{domain}/update``), which writes the compose file again;
nothing is recreated by the request.

``PUT`` carries the secrets the settings need: the registry's password and
the values of the build secrets. They go to the secret store, never into the
settings, and no response or audit record repeats them.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from noust.core.teams import scoped_store
from noust.deployers.helpers.container_settings import (
    ContainerView,
    change_container,
    describe_container,
)
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute, strict_domain
from noust.web.auth import actor_label
from noust.web.pydantic_compat import dump_model

router = APIRouter(route_class=NoustErrorRoute)


class ContainerSettingsResponse(BaseModel):
    """How a container application is built and run."""

    domain: str
    method: str = Field(description="dockerfile, railpack or image")
    image: str | None = Field(default=None, description="The registry reference, for image")
    dockerfile: str | None = Field(
        default=None, description="The Dockerfile, relative to the repository; null finds it"
    )
    context: str = Field(description="The build context, relative to the repository")
    target: str | None = Field(default=None, description="The stage of a multi-stage Dockerfile")
    build_args: dict[str, str] = Field(description="Non-secret build arguments")
    build_secrets: list[str] = Field(
        description="The ids of the secrets a build mounts; their values are never shown"
    )
    container_port: int = Field(description="Where the process listens inside the container")
    volumes: list[str] = Field(description="Container paths kept in named volumes")
    restart_policy: str
    registry_server: str | None = Field(
        default=None, description="The registry the account is for, when there is an account"
    )
    registry_username: str | None = None
    registry_password_set: bool = Field(
        description="Whether a password is kept for the account; it is never shown"
    )
    entrypoint: list[str] | None = Field(
        default=None,
        description="The words that replace the image's ENTRYPOINT; null keeps the image's, "
        "an empty list clears it",
    )
    command: list[str] | None = Field(
        default=None,
        description="The words that replace the image's CMD (the entrypoint's arguments); null "
        "keeps the image's, an empty list clears it",
    )
    digest: str | None = Field(
        default=None, description="The digest the last deployment of a registry image resolved"
    )
    release: str | None = Field(default=None, description="The release that serves, if any")


class ContainerSettingsRequest(BaseModel):
    """
    The settings of a container application, for a new one or to change one.

    Everything is optional. On a change, what is omitted stays as it is, and
    ``clear`` names what goes back to its default.
    """

    method: str | None = Field(
        default=None,
        description="dockerfile, railpack or image. An existing application can change "
        "between dockerfile and railpack, never to or from image",
    )
    image: str | None = Field(default=None, description="name[:tag][@sha256:digest]")
    dockerfile: str | None = Field(default=None, description="Relative to the repository")
    context: str | None = Field(default=None, description="Relative to the repository")
    target: str | None = None
    build_args: dict[str, str] | None = Field(
        default=None, description="Replaces the build arguments; none of them a secret"
    )
    build_secrets: dict[str, str | None] | None = Field(
        default=None,
        description="Build secret id to its value, kept in the secret store; null removes one",
    )
    container_port: int | None = None
    volumes: list[str] | None = Field(
        default=None, description="Replaces the volumes: absolute paths in the container"
    )
    restart_policy: str | None = None
    entrypoint: list[str] | None = Field(
        default=None,
        description="Replaces the image's ENTRYPOINT: the program and its arguments, one word "
        "each, run without a shell. An empty list clears the image's own",
    )
    command: list[str] | None = Field(
        default=None,
        description="Replaces the image's CMD: the entrypoint's arguments, one word each, run "
        "without a shell. An empty list clears the image's own",
    )
    registry_server: str | None = None
    registry_username: str | None = None
    registry_password: str | None = Field(
        default=None,
        description="The registry account's password, kept in the secret store",
        repr=False,
    )
    clear: list[str] = Field(
        default_factory=list,
        description="Settings to reset: dockerfile, target, container_port, registry_server, "
        "command, entrypoint",
    )
    clear_registry: bool = Field(default=False, description="Remove the registry account")


#: What ``clear`` may name: the settings that have a default to go back to.
_CLEARABLE = (
    "dockerfile",
    "target",
    "container_port",
    "registry_server",
    "command",
    "entrypoint",
)


def request_to_changes(body: ContainerSettingsRequest) -> dict[str, Any]:
    """
    Turn a request into the vocabulary of :mod:`noust.deployers.helpers.container_settings`.

    Args:
        body: The request.

    Returns:
        The settings and secrets it names, and nothing else.

    Raises:
        ValidationError: ``clear`` names something that cannot be cleared.
    """
    from noust.core.exceptions import ValidationError

    given = dump_model(body)
    changes: dict[str, Any] = {
        name: value
        for name, value in given.items()
        if value is not None and name not in ("clear", "clear_registry", "build_secrets")
    }
    for name in given["clear"]:
        if name not in _CLEARABLE:
            raise ValidationError(
                f"{name!r} cannot be cleared",
                details="Clearable settings: " + ", ".join(_CLEARABLE) + ".",
                field="clear",
            )
        changes[name] = None
    if given["build_secrets"] is not None:
        changes["build_secret_values"] = given["build_secrets"]
    if given["clear_registry"]:
        changes["clear_registry"] = True
    return changes


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


def _response(view: ContainerView) -> ContainerSettingsResponse:
    """
    Args:
        view: What the settings are.

    Returns:
        The API model.
    """
    return ContainerSettingsResponse(domain=view.domain, **view.settings, release=view.release)


@router.get("/{domain}/container", response_model=ContainerSettingsResponse)
def get_app_container(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ContainerSettingsResponse:
    """
    Show how a container application is built and run. Changes nothing.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        The settings, without the registry's password.

    Raises:
        HTTPException: 404 when the application is unknown.
        ValidationError: It is not a container application (400).
    """
    return _response(describe_container(_known(domain)))


@router.put("/{domain}/container", response_model=ContainerSettingsResponse)
def put_app_container(
    domain: str,
    body: ContainerSettingsRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> ContainerSettingsResponse:
    """
    Change how a container application is built and run.

    What the request names changes and the rest stays. The change is checked
    whole before anything is written, and takes effect with the next
    deployment of the application.

    Args:
        domain: Domain of the application.
        body: What to change.
        session: The authenticated session.

    Returns:
        The settings now.

    Raises:
        ValidationError: A value is not valid (400, with the field), or the
            change moves between an image and a repository.
        AppBusyError: Another operation is running on the application (409).
    """
    validated = _known(domain)
    change_container(validated, request_to_changes(body), actor=actor_label(session))
    return _response(describe_container(validated))


class ImageCheckResponse(BaseModel):
    """
    What looking at the tag of a registry image found.

    Attributes:
        domain: The application.
        image: The image it runs, as recorded.
        deployed: The digest the last deployment resolved; null when none is recorded.
        current: The digest the tag is now; null when it is pinned or the registry gave none.
        changed: Whether an update would run something new.
        reason: One sentence saying why.
    """

    domain: str
    image: str
    deployed: str | None = None
    current: str | None = None
    changed: bool
    reason: str


@router.post("/{domain}/container/check", response_model=ImageCheckResponse)
def check_app_image(
    domain: str, session: Annotated[dict, Depends(get_current_session)]
) -> ImageCheckResponse:
    """
    Ask the registry whether the tag an application runs points at a new image.

    Pulls the tag (one request when the host has it, the layers when it moved) and compares the
    digest with the one that runs. Deploys nothing: ``POST /api/apps/{domain}/update`` does.

    Args:
        domain: Domain of the application.
        session: The authenticated session.

    Returns:
        What was found.

    Raises:
        ValidationError: It does not run a registry image (400).
        DockerError: The registry refused the account or did not answer.
    """
    from noust.managers.image_updates import check_image

    return ImageCheckResponse(**check_image(_known(domain)).to_dict())
