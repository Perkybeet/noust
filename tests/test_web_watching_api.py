# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``GET /api/alerts/watching``: what Noust watches and by which channels it says so (item 159).

The question of the root of Alerts, answered once by the server (``noust.monitor.watching``) so the
console keeps no list of its own: which watches are on, which are off and worth offering, and which
channels would take what each of them says, with no credential in it.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.store import NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager

SLACK = "https://hooks.slack.test/services/T0/B0/prod-secret"


@pytest.fixture
def config_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    path = tmp_path / "etc" / "noust" / "config.yaml"
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", path)
    Config.reset_instance()
    try:
        yield path
    finally:
        Config.reset_instance()


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: NoustStore, config_path: Path, runner: object) -> FastAPI:
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    signed_in = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    response = signed_in.post("/api/auth/login", json={"token": token})
    assert response.status_code == 200, response.text
    signed_in.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return signed_in


@pytest.fixture
def anonymous(app: FastAPI) -> TestClient:
    return TestClient(app, client=("testclient", 50000), follow_redirects=False)


@pytest.fixture(autouse=True)
def monitor_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unit systemd would be asked about: the test machine has none."""
    monkeypatch.setattr(
        "noust.monitor.collector.monitor_service_probe",
        lambda: {"installed": True, "enabled": True, "active": True},
    )


def watch(body: dict[str, Any], name: str) -> dict[str, Any]:
    [found] = [item for item in body["watches"] if item["id"] == name]
    return found


class TestTheAnswer:
    def test_it_needs_a_session(self, anonymous: TestClient) -> None:
        assert anonymous.get("/api/alerts/watching").status_code == 401

    def test_a_fresh_server_watches_what_it_always_did_and_offers_the_registration_of_domains(
        self, client: TestClient
    ) -> None:
        response = client.get("/api/alerts/watching")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["watcher"] == {"state": "running", "fix": None}
        assert body["notifications"] == {"enabled": False, "paused_until": None, "channels": []}
        assert watch(body, "apps_down")["state"] == "on"
        assert watch(body, "domain_expiry")["state"] == "off"
        assert watch(body, "domain_expiry")["offer"] == {
            "key": "monitor.domain_expiry",
            "value": True,
        }

    def test_the_order_is_the_tables(self, client: TestClient) -> None:
        from noust.monitor.watching import WATCHES

        body = client.get("/api/alerts/watching").json()

        assert [item["id"] for item in body["watches"]] == [item.id for item in WATCHES]

    def test_what_the_operator_configured_is_what_it_says(
        self, client: TestClient, config_path: Path
    ) -> None:
        config = Config()
        config.set("notifications.enabled", True)
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.set("monitor.disk_threshold", 85)
        config.save()

        body = client.get("/api/alerts/watching").json()

        assert body["notifications"]["channels"] == [{"name": "slack", "type": "slack"}]
        assert [channel["name"] for channel in watch(body, "disk")["reach"]] == ["slack"]
        assert watch(body, "disk")["params"] == {"percent": 85}

    def test_no_credential_ever_comes_back(self, client: TestClient, config_path: Path) -> None:
        config = Config()
        config.set("notifications.enabled", True)
        config.set("notifications.channels.slack", {"webhook_url": SLACK})
        config.save()

        text = client.get("/api/alerts/watching").text

        assert "prod-secret" not in text
        assert "hooks.slack.test" not in text

    def test_a_monitor_that_does_not_run_says_how_to_start_it(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "noust.monitor.collector.monitor_service_probe",
            lambda: {"installed": True, "enabled": False, "active": False},
        )

        body = client.get("/api/alerts/watching").json()

        assert body["watcher"] == {"state": "disabled", "fix": "noust monitor enable"}


class TestItsPlaceInTheContract:
    def test_the_route_declares_its_permission(self, client: TestClient) -> None:
        schema = client.get("/api/openapi.json").json()

        operation = schema["paths"]["/api/alerts/watching"]["get"]
        assert operation["x-noust-permission"] == "server.read"

    def test_the_answer_is_in_the_published_schema(self, client: TestClient) -> None:
        schema = client.get("/api/openapi.json").json()

        operation = schema["paths"]["/api/alerts/watching"]["get"]
        reference = operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert reference.endswith("WatchingResponse")
