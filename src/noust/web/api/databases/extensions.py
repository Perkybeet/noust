# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/databases/{engine}/{name}/extensions``: the PostgreSQL extensions of a database.

Which extensions can be loaded is what the server has on disk (the host's packages, an instance's
image), read from its own ``pg_available_extensions``; loading and unloading one is
``CREATE EXTENSION`` and ``DROP EXTENSION`` as the cluster's superuser. All of it is
:class:`~noust.managers.database.extensions.Extensions`, which ``noust db extension`` calls too.

A database belongs to a team, but loading an extension that is not *trusted* runs its script with
the superuser's rights, so that one is the server's: the route asks for the whole server's scope
for it, as every route whose body can ask for something the server owns does.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from noust.managers.database.extensions import Extensions
from noust.web.api.auth import get_current_session
from noust.web.api.databases.common import service
from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.api.scope import require_instance_scope
from noust.web.permissions import Permission

router = APIRouter(route_class=NoustErrorRoute)


class ExtensionRequest(BaseModel):
    """
    An extension to load.

    Attributes:
        name: The extension, one the server offers (``vector``, ``postgis``).
        cascade: Also load the extensions it needs.
    """

    name: str = Field(..., max_length=63)
    cascade: bool = False


class ExtensionResponse(BaseModel):
    """
    One extension the server can load.

    Attributes:
        installed_version: The version loaded in this database; null when it is not.
        trusted: Whether a database owner may load it; otherwise only the server's
            administrator may.
        requires: The extensions it needs loaded first.
    """

    name: str
    default_version: str
    installed_version: str | None = None
    installed: bool = False
    comment: str = ""
    trusted: bool = False
    requires: list[str] = Field(default_factory=list)


class ExtensionListResponse(BaseModel):
    """
    The extensions a database could hold, and the ones it does.

    Attributes:
        server_version: PostgreSQL's version.
        extensions: Everything the server offers.
        source: Where an extension that is not in the list would come from.
    """

    engine: str
    database: str
    server_version: str | None = None
    extensions: list[ExtensionResponse] = Field(default_factory=list)
    source: str = ""


def _extensions(session: dict[str, Any]) -> Extensions:
    """
    Build what a request uses.

    Args:
        session: The authenticated session, whose actor the audit trail names.

    Returns:
        The manager over the request's database service.
    """
    return Extensions(service(session))


@router.get("/databases/{engine}/{name}/extensions", response_model=ExtensionListResponse)
def list_extensions(
    engine: str, name: str, session: Annotated[dict, Depends(get_current_session)]
) -> ExtensionListResponse:
    """
    List the extensions the server offers, marking those loaded in a database.

    Args:
        engine: The PostgreSQL (``postgresql``, ``postgresql@pg17``).
        name: The database.
        session: The authenticated session.

    Returns:
        The list.

    Raises:
        ValidationError: The engine is not PostgreSQL.
        DatabaseNotFoundError: There is no such database.
    """
    return ExtensionListResponse(**_extensions(session).available(engine, name).to_dict())


@router.post("/databases/{engine}/{name}/extensions", response_model=ExtensionResponse)
def add_extension(
    engine: str,
    name: str,
    request: ExtensionRequest,
    session: Annotated[dict, Depends(require_elevated)],
) -> ExtensionResponse:
    """
    Load an extension into a database, with sudo mode.

    Args:
        engine: The PostgreSQL.
        name: The database.
        request: The extension.
        session: The authenticated, elevated session.

    Returns:
        The extension as it is afterwards.

    Raises:
        ValidationError: The server does not offer it; the error says where one comes from.
        PermissionDenied: 403 ``instance_scope_required`` for an extension that is not
            trusted, when the caller does not see every team.
    """
    extensions = _extensions(session)
    if not extensions.get(engine, name, request.name).trusted:
        require_instance_scope(Permission.DATABASES_MANAGE)
    info = extensions.add(engine, name, request.name, cascade=request.cascade)
    return ExtensionResponse(**info.to_dict())


@router.delete(
    "/databases/{engine}/{name}/extensions/{extension}", response_model=ExtensionResponse
)
def remove_extension(
    engine: str,
    name: str,
    extension: str,
    session: Annotated[dict, Depends(require_elevated)],
    cascade: Annotated[bool, Query(description="Also drop what depends on it")] = False,
) -> ExtensionResponse:
    """
    Unload an extension from a database, with sudo mode.

    Args:
        engine: The PostgreSQL.
        name: The database.
        extension: The extension.
        session: The authenticated, elevated session.
        cascade: Also drop what depends on it; PostgreSQL refuses without it.

    Returns:
        The extension as it is afterwards.

    Raises:
        ValidationError: The server does not offer it.
        DatabaseNotFoundError: It is not loaded in the database.
        DatabaseQueryError: PostgreSQL refused; its message names what depends on it.
    """
    info = _extensions(session).remove(engine, name, extension, cascade=cascade)
    return ExtensionResponse(**info.to_dict())
