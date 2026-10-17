# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A route that answers without a body registers on every FastAPI Noust runs on.

What is defended: FastAPI 0.115 and older (the line the CI job for pydantic 1 pins, and
older than that on Ubuntu 24.04) read ``-> None`` as a response model when the module's
annotations are strings, and then refuse ``status_code=204`` while the application
imports, so the console does not start at all. The route class every router is built with
decides that in one place; a route that does name a response model keeps it.
"""

from __future__ import annotations

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from noust.web.api.deps import NoustErrorRoute

NoneType = type(None)


def test_a_plain_router_is_what_the_older_fastapi_refuses() -> None:
    # The annotation FastAPI 0.115 builds from ``-> None`` in a module of string annotations.
    plain = APIRouter()

    with pytest.raises(AssertionError, match="204"):

        @plain.delete("/x", status_code=204)
        def remove() -> NoneType:  # type: ignore[valid-type]
            return None


@pytest.mark.parametrize("status", [204, 304])
def test_the_route_class_registers_it_without_a_body(status: int) -> None:
    router = APIRouter(route_class=NoustErrorRoute)

    @router.delete("/x", status_code=status)
    def remove() -> NoneType:  # type: ignore[valid-type]
        return None

    (route,) = router.routes
    assert route.response_model is None  # type: ignore[attr-defined]


def test_it_still_answers_204_with_nothing() -> None:
    router = APIRouter(route_class=NoustErrorRoute)

    @router.delete("/x", status_code=204)
    def remove() -> NoneType:  # type: ignore[valid-type]
        return None

    app = FastAPI()
    app.include_router(router)

    response = TestClient(app).delete("/x")

    assert response.status_code == 204
    assert response.content == b""


def test_a_response_model_somebody_named_is_kept() -> None:
    router = APIRouter(route_class=NoustErrorRoute)

    @router.get("/x", response_model=dict[str, int])
    def read() -> dict[str, int]:
        return {"a": 1}

    @router.post("/y", status_code=201)
    def create() -> dict[str, int]:
        return {"a": 1}

    first, second = router.routes
    assert first.response_model == dict[str, int]  # type: ignore[attr-defined]
    assert second.response_model == dict[str, int]  # type: ignore[attr-defined]
