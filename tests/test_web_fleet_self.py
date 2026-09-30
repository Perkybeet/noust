"""
Tests for ``GET /api/auth/fleet/self``: a node publishes its ceiling, and only reads it.

What is pinned: the ceiling in force (the default on a server that never set
one), the calling central's own token name and central, nothing about other
tokens, no route that writes it, and its permission in the route map.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.fs import RecordingFileSystem
from noust.core.store import NoustStore
from noust.web.api.deps import require_auth
from noust.web.api.fleet_self import router


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    NoustStore.reset_instance()
    store = NoustStore(tmp_path / "noust.db", fs=RecordingFileSystem())
    monkeypatch.setattr("noust.core.store.get_store", lambda *a, **k: store)
    yield store
    NoustStore.reset_instance()


def _client(session: dict[str, Any]) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/auth/fleet")
    app.dependency_overrides[require_auth] = lambda: session
    return TestClient(app)


class TestFleetSelf:
    def test_the_default_for_a_local_operator(self, store):
        body = _client({"type": "session"}).get("/api/auth/fleet/self").json()
        assert body == {
            "level": "admin",
            "host_access": False,
            "fleet": False,
            "token_name": None,
            "central": None,
            "updated_at": None,
        }

    def test_a_central_sees_its_own_token_and_the_ceiling(self, store):
        store.set_fleet_access("read", True, updated_by="cli:root")
        session = {"type": "api_token", "fleet": True, "token_name": "fleet-nas.2"}

        body = _client(session).get("/api/auth/fleet/self").json()

        assert (body["level"], body["host_access"]) == ("read", True)
        assert (body["token_name"], body["central"]) == ("fleet-nas.2", "nas")
        assert body["updated_at"]

    def test_nothing_writes_it(self, store):
        client = _client({"fleet": True, "token_name": "fleet-nas"})
        for method in ("post", "put", "patch", "delete"):
            assert getattr(client, method)("/api/auth/fleet/self").status_code == 405

    def test_its_permission_is_mapped(self):
        from noust.web.permissions.routes_fleet_self import ROUTES

        assert ROUTES == {("GET", "/api/auth/fleet/self"): "self"}
