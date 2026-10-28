# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/managed-settings`` as the node's own operator meets it, and how it is permissioned.

The central's side of the same routes is pinned with the real fleet token in
``test_fleet_settings_admission.py``. Here: a server's operator reads what is managed, can ask the
central to put a setting back but cannot record one as the fleet's, and every route of the area is
mapped to a permission.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import NoustStore
from noust.core.variables.model import SERVER
from noust.core.variables.store import VariableStore
from noust.fleet.settings.managed import ManagedSettings
from noust.fleet.settings.signature import SyncSignature
from noust.web.api.deps import install_error_handlers, require_auth
from noust.web.api.managed_settings import router
from noust.web.permissions.registry import permission_for_route

LOCAL = {"type": "session", "username": "yago", "account_id": 1, "permissions": ["settings.manage"]}


@pytest.fixture(autouse=True)
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router, prefix="/api/managed-settings")
    app.dependency_overrides[require_auth] = lambda: LOCAL
    return TestClient(app)


def manage_a_variable() -> None:
    VariableStore().set(SERVER, 0, "REGION", "eu", actor="op")
    ManagedSettings().record_applied(
        "variable",
        "REGION",
        central="nas",
        version=5,
        signature=SyncSignature("ana", "c0123456789ab"),
    )


def test_the_operator_of_a_server_reads_what_is_managed(client: TestClient) -> None:
    manage_a_variable()

    body = client.get("/api/managed-settings").json()

    assert [(i["name"], i["state"], i["central"]) for i in body["items"]] == [
        ("REGION", "managed", "nas")
    ]
    assert "variable" in body["kinds"]


def test_changing_it_here_shows_as_overridden_and_it_can_be_asked_back(client: TestClient) -> None:
    manage_a_variable()
    VariableStore().set(SERVER, 0, "REGION", "us", actor="op")

    asked = client.post("/api/managed-settings/variable/REGION/restore")

    assert asked.status_code == 200, asked.text
    assert asked.json()["state"] == "overridden"
    assert asked.json()["restore_requested_at"] is not None


def test_asking_back_a_setting_nobody_manages_is_a_not_found(client: TestClient) -> None:
    response = client.post("/api/managed-settings/variable/NOPE/restore")

    assert response.status_code == 404
    assert response.json()["coded"]["code"] == "fleet_settings.not_managed"


def test_a_server_cannot_record_a_setting_as_the_fleets_by_hand(client: TestClient) -> None:
    VariableStore().set(SERVER, 0, "REGION", "eu", actor="op")

    response = client.put("/api/managed-settings/variable/REGION", json={"version": 1})
    released = client.delete("/api/managed-settings/variable/REGION")

    assert response.status_code == 403 == released.status_code
    assert response.json()["error"] == "fleet_sync_required"
    assert response.json()["coded"]["code"] == "fleet_settings.sync_required"
    assert ManagedSettings().list() == []


def test_every_route_of_the_area_has_a_permission() -> None:
    routes: list[Any] = [r for r in router.routes if hasattr(r, "methods")]
    assert routes
    for route in routes:
        for method in route.methods - {"HEAD", "OPTIONS"}:
            template = "/api/managed-settings" + route.path
            assert permission_for_route(method, template) is not None, (method, template)
