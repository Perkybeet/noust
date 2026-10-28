# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/recipes/test_api.py`` that other tests build on."""

from __future__ import annotations

from fastapi import FastAPI

from noust.web.api import recipes as recipes_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers


def small_app() -> FastAPI:
    """The recipes router alone, behind a session the test supplies."""
    api = FastAPI()
    install_error_handlers(api)
    api.include_router(recipes_api.router, prefix="/api/recipes")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    api.dependency_overrides[get_current_session] = lambda: session
    return api
