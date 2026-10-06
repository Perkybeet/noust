# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The console's exposure over HTTP: ``/api/console/exposure``.

The routes are a thin layer over the exposure manager; what is pinned here is
what the layer itself owes: sudo mode for what changes the way in, the checks
before a job is queued (a refusal is the response, not a failed job), the job
that does the work, and the permission each route declares.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.exceptions import SecurityError
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.web.api import console_exposure as api
from noust.web.auth import CSRF_HEADER_NAME
from noust.web.jobs import JobStatus, get_job_manager
from noust.web.permissions import Permission
from noust.web.permissions.registry import permission_for_route
from noust.web.server import create_app, get_token_manager
from tests.test_web_auth import make_config


class FakeManager:
    """Records what the routes ask of the exposure and answers as scripted."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.refuse: SecurityError | None = None

    def status(self) -> dict[str, Any]:
        return {"exposed": False, "ens_allows": True, "problems": []}

    def plan(self, domain: str, **kwargs: Any) -> list[str]:
        self.calls.append(("plan", {"domain": domain, **kwargs}))
        if self.refuse is not None:
            raise self.refuse
        return ["write the nginx site", "obtain a certificate"]

    def expose(self, domain: str, *, progress: Any, **kwargs: Any) -> Any:
        self.calls.append(("expose", {"domain": domain, **kwargs}))
        progress("Writing the nginx site")
        return SimpleNamespace(
            url=f"https://{domain}",
            to_dict=lambda: {"domain": domain, "url": f"https://{domain}", "removed": []},
        )

    def unexpose(self, *, progress: Any, **kwargs: Any) -> Any:
        self.calls.append(("unexpose", dict(kwargs)))
        progress("Removing the nginx site")
        return SimpleNamespace(
            removed=["nginx site of console.example.com"],
            to_dict=lambda: {"domain": "console.example.com", "removed": ["nginx site"]},
        )


@pytest.fixture(autouse=True)
def fresh_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    Config.reset_instance()
    NoustStore.reset_instance()
    yield
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> FakeManager:
    fake = FakeManager()
    monkeypatch.setattr(api, "_manager", lambda: fake)
    return fake


@pytest.fixture
def client(sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


def sign_in(client: TestClient) -> str:
    master = get_token_manager().generate_master_token()
    response = client.post("/api/auth/login", json={"token": master})
    assert response.status_code == 200, response.text
    client.headers[CSRF_HEADER_NAME] = response.json()["csrf_token"]
    return master


def elevate(client: TestClient, master: str) -> None:
    response = client.post("/api/auth/elevate", json={"token": master})
    assert response.status_code == 200, response.text


def wait_for(job_id: str) -> Any:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        job = get_job_manager().get_job(job_id)
        if job is not None and job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
            return job
        time.sleep(0.05)
    raise AssertionError("the job never finished")


BODY = {"domain": "console.example.com", "allow_ips": ["203.0.113.0/24"]}


def test_nobody_anonymous_reads_it(client: TestClient, manager: FakeManager) -> None:
    assert client.get("/api/console/exposure").status_code == 401


def test_the_state_is_readable_by_whoever_signs_in(
    client: TestClient, manager: FakeManager
) -> None:
    sign_in(client)

    response = client.get("/api/console/exposure")

    assert response.status_code == 200, response.text
    assert response.json() == {"exposed": False, "ens_allows": True, "problems": []}


def test_publishing_asks_for_sudo_mode_first(client: TestClient, manager: FakeManager) -> None:
    sign_in(client)

    response = client.post("/api/console/exposure", json=BODY)

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"
    assert manager.calls == []


def test_unpublishing_asks_for_sudo_mode_too(client: TestClient, manager: FakeManager) -> None:
    sign_in(client)

    response = client.delete("/api/console/exposure")

    assert response.status_code == 403
    assert response.json()["error"] == "elevation_required"


def test_publishing_is_a_job_that_restarts_the_console_later(
    client: TestClient, manager: FakeManager
) -> None:
    elevate(client, sign_in(client))

    response = client.post("/api/console/exposure", json=BODY)

    assert response.status_code == 202, response.text
    job = wait_for(response.json()["job_id"])
    assert job.status == JobStatus.COMPLETED
    assert job.result == {
        "domain": "console.example.com",
        "url": "https://console.example.com",
        "removed": [],
    }
    # The request was answered by the console that is about to restart: the
    # restart is scheduled, never made in the middle of it.
    expose = next(kwargs for name, kwargs in manager.calls if name == "expose")
    assert expose["restart"] == "later"
    assert expose["allow_ips"] == ["203.0.113.0/24"]
    assert "Writing the nginx site" in [entry.message for entry in job.logs]


def test_a_refusal_is_the_response_not_a_failed_job(
    client: TestClient, manager: FakeManager
) -> None:
    manager.refuse = SecurityError("The ENS profile keeps the console off the network")
    elevate(client, sign_in(client))

    response = client.post("/api/console/exposure", json=BODY)

    assert response.status_code in (400, 403, 409), response.text
    assert "ENS profile" in response.text
    assert [name for name, _ in manager.calls] == ["plan"]


def test_the_preview_changes_nothing_and_needs_no_sudo_mode(
    client: TestClient, manager: FakeManager
) -> None:
    sign_in(client)

    response = client.post("/api/console/exposure/preview", json=BODY)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "domain": "console.example.com",
        "steps": ["write the nginx site", "obtain a certificate"],
    }
    assert [name for name, _ in manager.calls] == ["plan"]


def test_unpublishing_is_a_job_too(client: TestClient, manager: FakeManager) -> None:
    elevate(client, sign_in(client))

    response = client.delete("/api/console/exposure")

    assert response.status_code == 202, response.text
    job = wait_for(response.json()["job_id"])
    assert job.status == JobStatus.COMPLETED
    assert ("unexpose", {"restart": "later"}) in manager.calls


def test_the_body_is_checked_before_anything_else(client: TestClient, manager: FakeManager) -> None:
    elevate(client, sign_in(client))

    assert client.post("/api/console/exposure", json={"domain": ""}).status_code == 422
    assert client.post("/api/console/exposure", json={}).status_code == 422
    assert manager.calls == []


def test_changing_the_way_in_is_host_access_and_reading_it_is_not() -> None:
    """A central holds host access on a node only where the node allowed it."""
    assert permission_for_route("GET", "/api/console/exposure") == Permission.SERVER_READ
    for method, path in (
        ("POST", "/api/console/exposure"),
        ("DELETE", "/api/console/exposure"),
        ("POST", "/api/console/exposure/preview"),
    ):
        assert permission_for_route(method, path) == Permission.SERVER_HOST_ACCESS


def test_publishing_the_console_needs_a_second_person_when_the_policy_asks() -> None:
    from noust.core.accounts.approvals import rule_for

    rule = rule_for("POST", "/api/console/exposure", [Permission.SERVER_HOST_ACCESS], BODY, {})

    assert rule is not None and rule.action == "console.expose"
    # Taking it off, and looking at it, are not what a second person is for.
    assert (
        rule_for("DELETE", "/api/console/exposure", [Permission.SERVER_HOST_ACCESS], None, {})
        is None
    )
    assert rule_for("GET", "/api/console/exposure", [Permission.SERVER_READ], None, {}) is None
