# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Where an application's variables go, which depends on what the application is.

A process application has its ``.env`` for the running process and a file beside each release for
what Noust says about the deployment (``NOUST_URL`` and the rest). A Docker Compose stack and a
container have neither: their containers read the project's own ``.env`` and nothing else reaches
them, so Noust writes both kinds of variable there (checklist B-46). And an application that builds
nothing, an image from a registry, has no build to give a build-only variable to (B-43).

These are the rules, in one place and with no I/O, for the readers and the writers of the file
(:mod:`noust.deployers.helpers.app_env`) and for the deployers
(:mod:`noust.deployers.helpers.deployment_variables`) to share (rule 3).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from noust.core.store import App, AppType
from noust.core.variables.predefined import RESERVED
from noust.validators.environment import EnvironmentValidationError

#: The types whose containers read the project's own ``.env``: Noust hands them what it says about
#: a deployment there, because that file is the only thing that reaches every container and every
#: ``docker compose`` command, the unit's included.
ENV_FILE_TYPES: frozenset[str] = frozenset({AppType.DOCKER_COMPOSE.value, AppType.CONTAINER.value})


def keeps_predefined_in_env(app_type: str | None) -> bool:
    """
    Tell whether an application gets ``NOUST_URL`` and the rest inside its ``.env``.

    Args:
        app_type: The application's type.

    Returns:
        True for a Compose stack and a container, whose containers load that file; the others
        have a file of their own beside each release.
    """
    return app_type in ENV_FILE_TYPES


def without_predefined(app_type: str | None, values: Mapping[str, str]) -> dict[str, str]:
    """
    Take out of a ``.env`` what Noust itself wrote there about the deployment.

    What the reader of an application's variables hands to everything that shows, exports, clones
    or edits them: the names are Noust's, not the operator's, and an import on another server
    could not set them.

    Args:
        app_type: The application's type.
        values: What the ``.env`` holds.

    Returns:
        The values, less the reserved names for a type that keeps them in the file.
    """
    if not keeps_predefined_in_env(app_type):
        return dict(values)
    return {name: value for name, value in values.items() if name not in RESERVED}


def with_predefined(
    app_type: str | None, values: Mapping[str, str], current: Mapping[str, str]
) -> dict[str, str]:
    """
    Put back into a ``.env`` about to be written what Noust had written there.

    The writer of an application's variables edits the operator's part of the file; the names
    Noust owns are carried through so an edit does not take them away from the containers.

    Args:
        app_type: The application's type.
        values: What is about to be written.
        current: What the file holds now.

    Returns:
        The values plus the reserved names the file has, for a type that keeps them there.
    """
    if not keeps_predefined_in_env(app_type):
        return dict(values)
    kept = {name: value for name, value in current.items() if name in RESERVED}
    return {**values, **kept}


def app_builds(app: App) -> bool:
    """
    Tell whether a deployment of an application builds anything.

    Args:
        app: The application's row.

    Returns:
        False for a container application that runs an image from a registry as it is; True
        for everything else.
    """
    if app.app_type != AppType.CONTAINER.value:
        return True
    from noust.core.container_store import ContainerStore

    settings = ContainerStore().get(app.domain)
    return settings is None or settings.builds


def require_buildable(domain: str, build_only: Iterable[str], *, builds: bool) -> None:
    """
    Refuse a variable only the build is given on an application that builds nothing.

    Dropping it would look like it was accepted, and it would be given to nobody.

    Args:
        domain: The application, for the message.
        build_only: The names whose audience is the build.
        builds: Whether a deployment of the application builds anything.

    Raises:
        EnvironmentValidationError: There is such a variable and nothing to give it to.
    """
    names = sorted(build_only)
    if builds or not names:
        return
    raise EnvironmentValidationError(
        f"{', '.join(names)} would be given to the build of {domain}, and nothing is built",
        details="It runs an image from a registry: the image is pulled as it is. Give the "
        "variable to the running application (the default, or --runtime-only), or deploy it "
        "from a Dockerfile if the image should be built here.",
    )
