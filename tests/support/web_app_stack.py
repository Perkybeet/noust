# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The parts of ``tests/test_web_app_stack.py`` that other tests build on."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.web.api import app_stack as app_stack_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers, require_elevated


def make_client(*, elevated: bool = True) -> TestClient:
    """A client for the router alone, authenticated, elevated or not."""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(app_stack_api.router, prefix="/api/apps")
    session = {"type": "session", "session_id": "s", "scope": "admin", "name": "alice"}
    app.dependency_overrides[get_current_session] = lambda: session

    def elevation() -> dict[str, Any]:
        if not elevated:
            raise HTTPException(status_code=403, detail={"error": "elevation_required"})
        return session

    app.dependency_overrides[require_elevated] = elevation
    return TestClient(app)
