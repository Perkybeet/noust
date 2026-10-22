# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/map``: how this server fits together, as a read-only graph (checklist M-69).

Names, applications, databases, scheduled work and the data an application keeps, in fixed layers,
with what connects them. Generated from the server on every read: it has no coordinates to store and
none to move, so it is the same picture for the same server. A client of
:mod:`noust.managers.machine_map`; the console draws it with the layered layout of the site diagram.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from noust.managers.machine_map import default_builder
from noust.web.api.auth import get_current_session
from noust.web.api.deps import NoustErrorRoute

router = APIRouter(route_class=NoustErrorRoute)


class MapNodeOut(BaseModel):
    """One thing on the map."""

    id: str
    kind: str
    label: str
    detail: str | None = None
    state: str | None = None
    state_word: str | None = None
    group: str | None = None
    domain: str | None = None
    engine: str | None = None


class MapLayerOut(BaseModel):
    """One column of the map."""

    id: str
    kind: str
    nodes: list[MapNodeOut]


class MapEdgeOut(BaseModel):
    """A relation between two things: ``serves``, ``uses``, ``runs`` or ``keeps``."""

    source: str
    target: str
    relation: str


class MapOut(BaseModel):
    """The map of what the caller sees."""

    layers: list[MapLayerOut]
    edges: list[MapEdgeOut]
    truncated: bool
    projects: int


@router.get("", response_model=MapOut)
def read_map(
    session: Annotated[dict, Depends(get_current_session)],
    project: Annotated[str | None, Query(max_length=64)] = None,
    environment: Annotated[str | None, Query(max_length=64)] = None,
    team: Annotated[str | None, Query(max_length=64)] = None,
) -> MapOut:
    """
    Read how this server fits together.

    Args:
        session: The authenticated session.
        project: Only the projects with this slug.
        environment: Only the environments with this slug.
        team: Only this team's.

    Returns:
        The layers left to right, each with its nodes, and every relation. A team member's map
        has its team's applications and databases and nothing else. ``truncated`` says nodes were
        left out for size.
    """
    return MapOut(
        **default_builder().build(project=project, environment=environment, team=team).to_dict()
    )
