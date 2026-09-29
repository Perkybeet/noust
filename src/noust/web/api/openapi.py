# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The API's own schema, as JSON.

``FastAPI(openapi_url=None)`` in :mod:`noust.web.server` keeps the schema off
the unauthenticated ``/openapi.json`` FastAPI would otherwise serve by
default - a map of every endpoint on an API that runs systemd as root is not
something an anonymous caller gets for free. This is the same document,
served instead at ``GET /api/openapi.json`` behind the same session or token
every other endpoint requires.

``scripts/export_openapi.py`` calls ``app.openapi()`` directly rather than
this route, so the committed ``panel/openapi.json`` - the one input of the
console's generated types - does not depend on a server listening anywhere.
This route exists for tooling that only has network access, and for an
operator who wants to see what the panel they are running actually exposes.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi import routing as fastapi_routing
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from noust.web.api.deps import NoustErrorRoute, require_elevated
from noust.web.auth import require_auth

router = APIRouter(route_class=NoustErrorRoute)

#: Marks an operation that demands sudo mode. A central reads it from each
#: node's schema and asks for its own operator's confirmation before
#: forwarding, so the node stays the one source of truth for what is
#: destructive, and a central that is older or newer than the node still asks
#: for exactly what that node asks for.
ELEVATION_EXTENSION = "x-noust-requires-elevation"


def _dependency_calls(dependant: Dependant) -> Iterator[Callable[..., Any] | None]:
    """
    Walk every dependency a route resolves, however deeply nested.

    Args:
        dependant: FastAPI's dependency graph of a route, which already
            includes the dependencies of every router it was included through.

    Yields:
        The callable of each dependency, depth first.
    """
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def api_routes(routes: list[Any]) -> Iterator[Any]:
    """
    Every API route of an application, as the schema sees it.

    FastAPI 0.13x keeps an included router as one opaque node and resolves
    its routes - with the prefixes and dependencies of every router they were
    included through - on demand; ``iter_route_contexts`` is what its own
    schema generator walks. Older releases, the ones Debian and Ubuntu ship,
    flatten ``app.routes`` into plain ``APIRoute`` objects instead.

    Args:
        routes: The application's routes.

    Yields:
        Objects with the ``APIRoute`` interface: ``path_format``,
        ``methods``, ``include_in_schema`` and ``dependant``.
    """
    iterate = getattr(fastapi_routing, "iter_route_contexts", None)
    if iterate is None:
        yield from (route for route in routes if isinstance(route, APIRoute))
        return
    for context in iterate(routes):
        if isinstance(context.original_route, APIRoute):
            yield context


def route_requires_elevation(route: Any) -> bool:
    """
    Report whether a route depends on :func:`~noust.web.api.deps.require_elevated`.

    Read from the dependency graph FastAPI resolves at request time, not from a
    list: a route that gains the dependency is marked the moment it does.

    Args:
        route: An ``APIRoute``, or a route context from :func:`api_routes`.

    Returns:
        True when ``require_elevated`` is anywhere in its dependency graph.
    """
    return any(call is require_elevated for call in _dependency_calls(route.dependant))


def annotate_elevation(schema: dict[str, Any], routes: list[Any]) -> dict[str, Any]:
    """
    Mark every operation whose route requires sudo mode.

    Args:
        schema: The OpenAPI document. Modified in place.
        routes: The application's routes.

    Returns:
        The same document.
    """
    paths: dict[str, Any] = schema.get("paths", {})
    for route in api_routes(routes):
        if not route.include_in_schema:
            continue
        if not route_requires_elevation(route):
            continue
        operations = paths.get(route.path_format, {})
        for method in route.methods:
            operation = operations.get(method.lower())
            if isinstance(operation, dict):
                operation[ELEVATION_EXTENSION] = True
    return schema


def install_openapi(app: FastAPI) -> None:
    """
    Make ``app.openapi()`` carry the elevation extension.

    Wrapped rather than rebuilt so the document stays FastAPI's own, cached
    exactly as FastAPI caches it, whoever asks: this route, the export
    script or a test.

    Args:
        app: The application.
    """
    build = app.openapi

    def openapi() -> dict[str, Any]:
        """
        Returns:
            The application's OpenAPI document, annotated.
        """
        return annotate_elevation(build(), app.routes)

    # FastAPI documents overriding app.openapi as the way to customise the
    # schema; mypy objects to assigning to a method, hence setattr.
    setattr(app, "openapi", openapi)  # noqa: B010


@router.get("/openapi.json", response_model=dict[str, Any])
def get_openapi_schema(
    request: Request, session: dict[str, Any] = Depends(require_auth)
) -> dict[str, Any]:
    """
    Serve the application's own OpenAPI document.

    Args:
        request: The incoming request, used to reach the application that
            owns the route table ``app.openapi()`` walks.
        session: Authenticated session, injected. Any credential that clears
            ``require_auth`` may read this; the schema names endpoints and
            shapes, not secrets.

    Returns:
        The OpenAPI document, the same one ``scripts/export_openapi.py``
        writes to ``panel/openapi.json``.
    """
    # Request.app is typed Any by Starlette - it has no way to know the
    # ASGI app in scope["app"] is a FastAPI and not a bare Starlette. The
    # explicit annotation, not a cast, is what tells mypy .openapi() exists
    # and returns dict[str, Any] rather than Any.
    app: FastAPI = request.app
    return app.openapi()
