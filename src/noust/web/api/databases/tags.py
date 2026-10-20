# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/databases/{engine}/{name}/tags``: free labels on a database (M-07).

A client of :class:`~noust.core.resource_tags.ResourceTags`, like ``noust db tag``. The database
is found through the database service, so one that is not there is a 404, and the scope table
(a resource under ``/api/databases/databases/{engine}/{name}``) already answers 404 for a
database of a team the caller is not in; the labels themselves are kept by engine and name.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from noust.core.app_tags import MAX_TAGS
from noust.core.audit import record as audit_record
from noust.core.resource_tags import KIND_DATABASE, ResourceTags, database_key
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.deps import NoustErrorRoute
from noust.web.permissions.principal import actor_of

router = APIRouter(route_class=NoustErrorRoute)


class DatabaseTagsResponse(BaseModel):
    """
    The tags of one database.

    Attributes:
        engine: The engine it is on, as the listing names it.
        database: Its name.
        tags: Its tags, sorted.
        max_tags: How many a database may carry.
    """

    engine: str
    database: str
    tags: list[str]
    max_tags: int = MAX_TAGS


class SetDatabaseTagsRequest(BaseModel):
    """The tags a database is to carry; any it has that are not here are removed."""

    tags: list[str] = Field(default_factory=list)


@router.get("/databases/{engine}/{name}/tags", response_model=DatabaseTagsResponse)
def get_database_tags(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> DatabaseTagsResponse:
    """
    Read the tags of a database.

    Args:
        engine: Engine name.
        name: Database name.
        session: The authenticated session.

    Returns:
        Its tags.

    Raises:
        DatabaseNotFoundError: When no such database exists.
    """
    view = service(session).get(engine, name)
    return DatabaseTagsResponse(
        engine=view.engine,
        database=view.name,
        tags=ResourceTags().of(KIND_DATABASE, database_key(view.engine, view.name)),
    )


@router.put("/databases/{engine}/{name}/tags", response_model=DatabaseTagsResponse)
def put_database_tags(
    engine: str,
    name: str,
    body: SetDatabaseTagsRequest,
    session: Annotated[dict, Depends(get_current_session)],
) -> DatabaseTagsResponse:
    """
    Make a database carry exactly these tags.

    Args:
        engine: Engine name.
        name: Database name.
        body: The tags.
        session: The authenticated session.

    Returns:
        The tags it carries now.

    Raises:
        DatabaseNotFoundError: When no such database exists.
        ValidationError: A tag is not one, or there are too many (400).
    """
    view = service(session).get(engine, name)
    key = database_key(view.engine, view.name)
    tags = ResourceTags().set(KIND_DATABASE, key, body.tags)
    audit_record(
        "databases.tags.set",
        actor=actor_of(session),
        target=f"database:{key}",
        details={"tags": tags},
    )
    return DatabaseTagsResponse(engine=view.engine, database=view.name, tags=tags)
