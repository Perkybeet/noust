# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
How a command reaches a service of a Compose stack from outside it: a scheduled task.

A scheduled task of a stack runs in the container of one of its services
(``docker compose exec -T <service> <argv>``), not on the host, where the
program it names may not exist and the data it needs is not. The unit that runs
it is the stack's own, in miniature: root (the Docker socket is root's), the
application's directory, the project the stack runs under (``-p``) and the
compose file its own unit names (``COMPOSE_FILE``), so the task reaches exactly
the containers the stack's unit started, an adopted stack's included.

The command is an argument vector, as everywhere (checklist B-65): nothing here
puts it behind ``sh -c``, so a container without a shell runs a task as well as
one with.
"""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from noust.core.exceptions import ServiceError, ValidationError
from noust.core.store import App, is_compose_backed
from noust.deployers.docker_compose import (
    COMPOSE_FILE_PRIORITY,
    compose_file_from_unit,
    compose_project_name,
)
from noust.deployers.helpers.container_compose import COMPOSE_FILE as CONTAINER_FILE
from noust.deployers.helpers.layout import app_root
from noust.terminal.targets import validate_service


@dataclass(frozen=True)
class ComposeExec:
    """
    What a unit needs to run a command in a service of a stack.

    Attributes:
        prefix: The program and its arguments up to the command:
            ``docker compose -p <project> exec -T <service>``.
        environment: Variables the unit sets (``COMPOSE_FILE``).
        working_directory: The application's directory.
    """

    prefix: tuple[str, ...]
    environment: dict[str, str]
    working_directory: Path


def compose_exec(app: App, service: str, unit_text: str | None) -> ComposeExec:
    """
    Describe how to run a command in one service of an application's stack.

    Args:
        app: A Compose or container application.
        service: The service the command runs in.
        unit_text: The text of the application's own unit, which names the
            compose file when it is not the default; None when it has none.

    Returns:
        The prefix, environment and directory.

    Raises:
        ValidationError: The application is not a stack, or the service is not
            a name Compose accepts.
        ServiceError: Docker is not installed.
    """
    if not is_compose_backed(app.app_type):
        raise ValidationError(
            f"{app.domain} is not a stack of services: a task cannot run in a service of it",
            details="Leave the service out to run the task on this server.",
            field="service",
        )
    checked = validate_service(service)
    if checked is None:
        raise ValidationError("Name the service the task runs in", field="service")
    docker = shutil.which("docker")
    if docker is None:
        raise ServiceError(
            "Docker is not installed on this server",
            details="A task that runs in a service of a stack needs the Docker client.",
        )
    root = app_root(app)
    environment: dict[str, str] = {}
    compose_file = compose_file_from_unit(unit_text)
    if compose_file:
        environment["COMPOSE_FILE"] = compose_file
    project = app.compose_project or compose_project_name(root, _compose_path(root, compose_file))
    prefix: list[str] = [docker, "compose"]
    if project:
        prefix += ["-p", project]
    prefix += ["exec", "-T", checked]
    return ComposeExec(prefix=tuple(prefix), environment=environment, working_directory=root)


def _compose_path(root: Path, compose_file: str | None) -> Path | None:
    """
    Find the compose file a stack runs from, to derive its project name.

    Args:
        root: The application's directory.
        compose_file: What its unit names, relative to ``root``.

    Returns:
        The file, or None when none is found (the project name then follows
        the directory).
    """
    if compose_file:
        return root / compose_file
    names: Sequence[str] = (CONTAINER_FILE, *COMPOSE_FILE_PRIORITY)
    return next((root / name for name in names if (root / name).is_file()), None)
