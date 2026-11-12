# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the gate to Docker refuses with, and how each refusal reaches a person.

Each class is a base the API already maps, so that the gate needs no table of its own: a Docker that
is not there or does not answer is a state of the server (409), an object a principal cannot see is
answered as one that does not exist (404, the teams' rule, I-04), and what belongs to an application
or to a database engine is a request that is well made and goes through another door (409, with the
door in the sentence).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noust.core.exceptions import DockerError, PrerequisiteError
from noust.core.teams.model import ResourceNotFoundError

if TYPE_CHECKING:
    from noust.core.docker.cleanup import CleanupPlan

__all__ = [
    "DockerNotFoundError",
    "DockerOwnedError",
    "DockerPlanChangedError",
    "DockerRefusedError",
    "DockerUnavailableError",
]


class DockerUnavailableError(DockerError, PrerequisiteError):
    """
    Docker is not installed on this machine, or its daemon does not answer.

    The failing tool's own words are in ``output``: a Docker error is never paraphrased.
    """


class DockerNotFoundError(DockerError, ResourceNotFoundError):
    """
    There is no such container, image, volume or network, or not one this principal may see.

    One error for both on purpose, like :class:`~noust.core.teams.model.ResourceNotFoundError`: a
    member of a team who names the container of another team's application is answered exactly as
    if it did not exist.
    """


class DockerOwnedError(DockerError, PrerequisiteError):
    """
    The object belongs to an application, a database engine or Noust itself, which changes it.

    Nothing was changed. ``details`` names the door to go through (a command that exists), because
    a container is not stopped from under the application that keeps it running: its health gate,
    its way back and its site would not know.

    Attributes:
        owner_kind: ``app``, ``database`` or ``noust``.
        subject: The application's domain, the instance's name, or what part of Noust it is.
    """

    def __init__(
        self, message: str, details: str = "", *, owner_kind: str = "", subject: str = ""
    ) -> None:
        """
        Args:
            message: What was refused.
            details: Where to go instead.
            owner_kind: The kind of owner (``app``, ``database`` or ``noust``).
            subject: Which one.
        """
        super().__init__(message, details)
        self.owner_kind = owner_kind
        self.subject = subject


class DockerPlanChangedError(DockerError, PrerequisiteError):
    """
    A cleanup plan is not the one that was shown, so it was not applied.

    Something came into the plan or left it between the moment a person looked at it and the moment
    they confirmed (a container started, an image was pulled): what they agreed to is not what would
    be removed. Nothing was changed; ``plan`` is the plan as it is now, to be shown again.

    Attributes:
        plan: The plan as it is now.
    """

    def __init__(self, message: str, details: str = "", *, plan: CleanupPlan) -> None:
        """
        Args:
            message: What happened.
            details: What to do.
            plan: The plan as it is now.
        """
        super().__init__(message, details)
        self.plan = plan


class DockerRefusedError(DockerError, PrerequisiteError):
    """
    Docker was asked to do something well formed and refused (a container that is running cannot be
    removed, a network still has a container on it).

    A state of the machine and not a fault of Noust: the request is answered as a conflict, and Docker's
    own words are in ``output``, never paraphrased.
    """
