# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/config/delivery``: the three limits a server delivers under (checklist F-28).

How many builds run at once, how many may wait and how long one may take are one
form in the console and one typed endpoint, so a value out of range is refused as
a whole before any of the three is written. They are the server's: on a central
the console's server selector aims the form at the server being looked at.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core import delivery
from noust.core.config import Config
from noust.web.api import config as config_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers


@pytest.fixture
def config_path(sandbox: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = sandbox / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    delivery.forget()
    try:
        yield path
    finally:
        Config.reset_instance()
        delivery.forget()


@pytest.fixture
def client(config_path: Path) -> TestClient:
    app = FastAPI()
    app.include_router(config_api.router, prefix="/api/config")
    install_error_handlers(app)
    app.dependency_overrides[get_current_session] = lambda: {"session_id": "test", "type": "master"}
    return TestClient(app, raise_server_exceptions=False)


def test_the_defaults_are_what_the_server_does_without_being_told(client: TestClient) -> None:
    body = client.get("/api/config/delivery").json()

    assert body == {"max_concurrent_builds": 2, "queue_limit": 25, "max_minutes": 60}


def test_the_three_limits_are_written_together(client: TestClient, config_path: Path) -> None:
    response = client.put(
        "/api/config/delivery",
        json={"max_concurrent_builds": 4, "queue_limit": 40, "max_minutes": 0},
    )

    assert response.status_code == 200, response.text
    assert client.get("/api/config/delivery").json() == {
        "max_concurrent_builds": 4,
        "queue_limit": 40,
        "max_minutes": 0,
    }
    stored = yaml.safe_load(config_path.read_text())["deploy"]
    assert (stored["max_concurrent_builds"], stored["queue_limit"], stored["max_minutes"]) == (
        4,
        40,
        0,
    )
    Config.reset_instance()
    assert delivery.DeliverySettings.read(Config()).max_concurrent_builds == 4


@pytest.mark.parametrize(
    "body",
    [
        {"max_concurrent_builds": 0, "queue_limit": 25, "max_minutes": 60},
        {"max_concurrent_builds": 17, "queue_limit": 25, "max_minutes": 60},
        {"max_concurrent_builds": 2, "queue_limit": 0, "max_minutes": 60},
        {"max_concurrent_builds": 2, "queue_limit": 1001, "max_minutes": 60},
        {"max_concurrent_builds": 2, "queue_limit": 25, "max_minutes": -1},
        {"max_concurrent_builds": 2, "queue_limit": 25, "max_minutes": 1441},
    ],
)
def test_a_value_out_of_range_is_refused_and_nothing_is_written(
    client: TestClient, config_path: Path, body: dict[str, int]
) -> None:
    response = client.put("/api/config/delivery", json=body)

    assert response.status_code == 422, response.text
    assert not config_path.exists()


def test_the_routes_declare_their_permissions() -> None:
    from noust.web.permissions import Permission
    from noust.web.permissions.routes_config import ROUTES

    assert ROUTES[("GET", "/api/config/delivery")] == Permission.SETTINGS_READ
    assert ROUTES[("PUT", "/api/config/delivery")] == Permission.SETTINGS_MANAGE
