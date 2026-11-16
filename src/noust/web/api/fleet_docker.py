# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/docker``: the Docker zone's actions on every server of the fleet (item 189).

A thin layer (rule 3). Looking for new versions of the images in use is a bulk action of the fleet engine
(:mod:`noust.web.fleet_docker_actions`), planned and queued exactly as ``POST /api/fleet/actions`` plans and
queues the others, so one confirmation of sudo mode covers the job and each server answers for the operator
with its own permissions: the central asks every server for the look through its tunnel and puts what they
found side by side, and re-implements nothing a server does. Whoever changes Docker may ask for it on every
server; each server still holds the operator to its own ceiling for its central.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from noust.web.api.deps import NoustErrorRoute
from noust.web.api.fleet import ActionOut, Session, _plan_and_start
from noust.web.pydantic_compat import bounded_list

router = APIRouter(route_class=NoustErrorRoute)


class DockerFleetCheckIn(BaseModel):
    """
    Look for new versions of the images in use on several servers.

    Attributes:
        nodes: The servers to ask; every server of the fleet when none is named and no label is given.
        labels: Servers carrying every one of these labels are added.
        pull: Download what moved to compare, which a Docker without buildx needs. Nothing is downloaded
            without it.
        plan: Only say which servers would be asked and which cannot take part.
    """

    nodes: list[str] = bounded_list(200)
    labels: dict[str, str] = Field(default_factory=dict)
    pull: bool = False
    plan: bool = False


@router.post("/images/check", response_model=ActionOut)
async def check_images(
    request: Request, body: DockerFleetCheckIn, session: Session
) -> dict[str, Any]:
    """
    Ask every server's registries whether the images in use have a new version, as one job.

    Each server is asked for its own look (``POST /api/docker/images/check``) through its tunnel, as the
    operator, and its job is followed; what each found is kept in short in the record of that server (how
    many images, how many are new and which), and the whole of it is on the server. A server that runs a
    Noust before 4.3 and one with no Docker are shown in the plan as unable to take part, not as failures.
    The central's own Docker, when it has one, is looked at with the look of its own zone. Needs the
    permission to change Docker; sudo mode is asked when a server's own schema asks for it, and one
    confirmation covers the job.

    Args:
        request: The request.
        body: Which servers, and whether to download to compare.
        session: The authenticated session.

    Returns:
        The plan and, unless ``plan``, the job: its ``result`` is the state of each server as it runs.

    Raises:
        ValidationError: A server is not one of this central's, or none can take part.
    """
    from noust.fleet.nodes import NodeManager
    from noust.web.fleet_docker_actions import check_request

    # Naming none is the whole fleet: this is the fleet's look, and a page that wants one server asks that
    # server for its own.
    nodes = body.nodes or ([] if body.labels else [record.name for record in NodeManager().list()])
    return await _plan_and_start(
        request, session, check_request(nodes, body.labels, pull=body.pull), body.plan
    )
