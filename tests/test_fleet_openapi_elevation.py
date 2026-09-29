# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``x-noust-requires-elevation``: a node's schema says which calls need sudo mode.

A central asks its own operator to confirm before forwarding exactly those,
so the mark must be on every operation behind ``require_elevated`` and on no
other. It is derived from the dependency graph; these tests hold it against
the source of each endpoint, which is a second, independent reading.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import APIRouter, Depends, FastAPI

from noust.core.runner import FakeRunner
from noust.web.api.deps import require_elevated
from noust.web.api.openapi import (
    ELEVATION_EXTENSION,
    api_routes,
    install_openapi,
    route_requires_elevation,
)
from noust.web.server import create_app
from tests.test_web_auth import make_config


@pytest.fixture
def app(sandbox: Path, runner: FakeRunner) -> FastAPI:
    """
    Returns:
        The real application.
    """
    return create_app(make_config(sandbox))


def marked(schema: dict[str, Any]) -> set[tuple[str, str]]:
    """
    Args:
        schema: An OpenAPI document.

    Returns:
        ``(METHOD, path)`` of every operation carrying the extension.
    """
    return {
        (method.upper(), path)
        for path, item in schema["paths"].items()
        for method, operation in item.items()
        if isinstance(operation, dict) and operation.get(ELEVATION_EXTENSION) is True
    }


def schema_routes(app: FastAPI) -> list[Any]:
    """
    Args:
        app: The application.

    Returns:
        Every API route that is in the schema, nested routers included.
    """
    return [route for route in api_routes(app.routes) if route.include_in_schema]


class TestTheRealSchema:
    def test_every_route_behind_sudo_mode_is_marked_and_no_other(self, app: FastAPI) -> None:
        expected = {
            (method, route.path_format)
            for route in schema_routes(app)
            if route_requires_elevation(route)
            for method in route.methods
        }

        assert marked(app.openapi()) == expected

    def test_the_mark_agrees_with_what_each_endpoint_declares(self, app: FastAPI) -> None:
        # An independent reading: the endpoint's own signature.
        declared = {
            (method, route.path_format)
            for route in schema_routes(app)
            if "Depends(require_elevated)" in inspect.getsource(route.endpoint)
            for method in route.methods
        }

        assert declared, "no endpoint declares require_elevated: the reading is broken"
        assert declared <= marked(app.openapi())

    def test_known_operations(self, app: FastAPI) -> None:
        schema = marked(app.openapi())

        assert ("DELETE", "/api/apps/{domain}") in schema
        assert ("POST", "/api/auth/tokens") in schema
        assert ("POST", "/api/nodes") in schema
        assert ("DELETE", "/api/nodes/{node}") in schema
        assert ("GET", "/api/apps") not in schema
        assert ("GET", "/api/nodes") not in schema

    def test_only_true_is_ever_written(self, app: FastAPI) -> None:
        for item in app.openapi()["paths"].values():
            for operation in item.values():
                if isinstance(operation, dict) and ELEVATION_EXTENSION in operation:
                    assert operation[ELEVATION_EXTENSION] is True

    def test_the_proxy_is_not_in_the_schema(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert not any(path.startswith("/api/nodes/{node}/api") for path in paths)
        assert "/api/nodes/{node}/events" not in paths


async def nested(
    session: Annotated[dict[str, Any], Depends(require_elevated)],
) -> dict[str, Any]:
    """
    A dependency that reaches sudo mode through another.

    Args:
        session: The elevated session.

    Returns:
        The session.
    """
    return session


def plain() -> None:
    """A dependency with nothing to do with sudo mode."""
    return None


class TestTheGraph:
    """The walk finds the dependency however it is reached."""

    def build(self) -> FastAPI:
        app = FastAPI()

        @app.delete("/direct")
        def direct(session: Annotated[dict[str, Any], Depends(require_elevated)]) -> None:
            return None

        @app.post("/nested")
        def through(session: Annotated[dict[str, Any], Depends(nested)]) -> None:
            return None

        @app.get("/free")
        def free(value: Annotated[None, Depends(plain)]) -> None:
            return None

        guarded = APIRouter(dependencies=[Depends(require_elevated)])

        @guarded.put("/router-level")
        def router_level() -> None:
            return None

        app.include_router(guarded)
        install_openapi(app)
        return app

    def test_direct_nested_and_router_level(self) -> None:
        assert marked(self.build().openapi()) == {
            ("DELETE", "/direct"),
            ("POST", "/nested"),
            ("PUT", "/router-level"),
        }

    def test_the_document_is_cached_and_stays_marked(self) -> None:
        app = self.build()

        first = app.openapi()
        second = app.openapi()

        assert first is second
        assert ("DELETE", "/direct") in marked(second)
