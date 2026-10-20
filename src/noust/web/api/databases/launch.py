# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances/{name}/launch``: what an instance's container is started with.

An instance is started with the command Noust works out for its engine. An operator may add
arguments after it, or write the whole command instead; both are columns of the instance's row
and a new Compose file (:class:`~noust.managers.database.instance_launch.InstanceLaunch`, which
``noust db instance launch`` calls too). Reading and planning change nothing and answer at once;
changing restarts the engine, asks for sudo mode, needs the operator to accept that Noust did not
test their words, and runs as a job.

Words are JSON strings, an argument vector: never a shell line, which nothing here would run.
These are the server's, not a team's: an instance is nobody's.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.exceptions import ValidationError
from noust.managers.database.instance_launch import InstanceLaunch
from noust.managers.database.managed import ManagedInstances
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import queue, service
from noust.web.api.databases.jobs import launch_job
from noust.web.api.deps import JobAcceptedResponse, NoustErrorRoute, require_elevated
from noust.web.jobs import JobType

router = APIRouter(route_class=NoustErrorRoute)


class LaunchRequest(BaseModel):
    """
    A change of what an instance's container is started with.

    Exactly one of ``command``, ``args`` and ``reset`` is given.

    Attributes:
        command: The container's whole command, instead of the engine's: Noust adds nothing to
            it (the memory share, the TLS arguments), so it is refused for an instance that
            serves TLS.
        args: Arguments added after the ones Noust passes the engine. A password is never
            one: a command line is visible to every local user.
        reset: Take the command and the arguments back to what Noust passes.
        accept_untested: The operator accepts that Noust has not tested their words. Without
            it, setting them is a 400 ``confirmation_required`` carrying what is accepted;
            taking them back needs nothing.
    """

    command: list[str] | None = Field(default=None, max_length=64)
    args: list[str] | None = Field(default=None, max_length=64)
    reset: bool = False
    accept_untested: bool = False

    def words(self) -> dict[str, Any]:
        """
        Returns:
            What the manager takes.
        """
        return {"command": self.command, "args": self.args, "reset": self.reset}


class LaunchStatusResponse(BaseModel):
    """
    What an instance's container is started with.

    Attributes:
        command: The operator's whole command, or empty.
        args: The operator's arguments, or empty.
        customised: Anything about the container is the operator's: the image, the command or
            the arguments.
        custom_image: The image is the operator's.
        effective: What the container runs; empty when the image's own command is left.
    """

    name: str
    key: str
    command: list[str] = Field(default_factory=list)
    args: list[str] = Field(default_factory=list)
    customised: bool
    custom_image: bool
    effective: list[str] = Field(default_factory=list)


class LaunchPlanResponse(BaseModel):
    """
    What a change would do. Nothing was changed.

    Attributes:
        changes: The settings that would change, each with ``setting``, ``from`` and ``to``.
        effective: The command the container would be started with afterwards.
        restarts: Whether the engine restarts.
        requires_confirmation: The words are the operator's: ``accept_untested`` is needed.
        apps: The applications that lose their connections while it restarts.
        blockers: What stops the change.
        warnings: What the operator should know first, what is not tested included.
    """

    name: str
    changes: list[dict[str, Any]] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    restarts: bool
    requires_confirmation: bool
    apps: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


def _launch(session: dict[str, Any]) -> InstanceLaunch:
    """
    Build what a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The launch manager over the request's database service.
    """
    return InstanceLaunch(ManagedInstances(service(session)))


@router.get("/instances/{name}/launch", response_model=LaunchStatusResponse)
def get_launch(
    name: str, session: Annotated[dict, Depends(get_current_session)]
) -> LaunchStatusResponse:
    """
    Say what an instance's container is started with.

    Args:
        name: The instance.
        session: The authenticated session.

    Returns:
        The operator's command and arguments, and what the container runs.

    Raises:
        DatabaseNotFoundError: There is no such instance.
    """
    return LaunchStatusResponse(**_launch(session).status(name))


@router.post("/instances/{name}/launch/plan", response_model=LaunchPlanResponse)
def plan_launch(
    name: str, request: LaunchRequest, session: Annotated[dict, Depends(get_current_session)]
) -> LaunchPlanResponse:
    """
    Say what a change would do. Changes nothing.

    Args:
        name: The instance.
        request: The change.
        session: The authenticated session.

    Returns:
        The plan, with the applications that lose their connections and what blocks it.

    Raises:
        ValidationError: Nothing or more than one thing is asked, or a word is not valid.
        DatabaseNotFoundError: There is no such instance.
    """
    plan = _launch(session).plan(name, **request.words())
    return LaunchPlanResponse(**plan.to_dict())


@router.put("/instances/{name}/launch", response_model=JobAcceptedResponse, status_code=202)
def set_launch(
    name: str, request: LaunchRequest, session: Annotated[dict, Depends(require_elevated)]
) -> JobAcceptedResponse:
    """
    Queue changing what an instance's container is started with, with sudo mode.

    The engine restarts on the new words; when it does not come up the previous ones are put
    back and the job fails with the engine's own log. What cannot succeed, and words the
    operator did not accept the price of, are refused here and not in a failed job.

    Args:
        name: The instance.
        request: The change.
        session: The authenticated, elevated session.

    Returns:
        The queued job.

    Raises:
        ValidationError: A word is not valid, or the instance cannot be changed now.
        ConfirmationRequired: Words of the operator's, without ``accept_untested``.
        DatabaseNotFoundError: There is no such instance.
    """
    launch = _launch(session)
    plan = launch.plan(name, **request.words())
    if plan.blockers:
        raise ValidationError(
            f"{name} cannot be changed now",
            details="\n".join(f"- {blocker}" for blocker in plan.blockers),
            field="state",
        )
    plan.require_accepted(request.accept_untested)
    return queue(
        session,
        job_type=JobType.DATABASE,
        name=f"Change what {name} is started with",
        description=f"Restarting {name} on its new command",
        func=launch_job,
        kwargs={
            "name": name,
            "command": request.command,
            "args": request.args,
            "reset": request.reset,
        },
        metadata={"instance": name, "action": "launch"},
        message=f"{name} will restart on its new command",
    )
