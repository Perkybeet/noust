# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
A first deployment that failed, over the API (item 141).

``POST /api/apps`` remembers what it was asked to deploy; ``GET /api/first-deploys`` says which of
those failed and left no application behind, so the console can show the failure on the page of
a domain that has none, and offer to try again. The list is held to the teams that own the project
the deployment was meant for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.store import App, JobRecord, NoustStore
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app as build_app
from noust.web.server import get_token_manager

PAYLOAD = {
    "domain": "shop.example.com",
    "source": "https://ana:hunter2@git.example.com/shop.git",
    "app_type": "nextjs",
    "branch": "main",
}


@pytest.fixture
def store(tmp_path: Path) -> Any:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    try:
        yield instance
    finally:
        instance.close()
        NoustStore.reset_instance()


@pytest.fixture
def app(tmp_path: Path, store: Any, runner: object) -> FastAPI:
    return build_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


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


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch, store: Any) -> list[dict[str, Any]]:
    """Queue nothing: record the job the way the manager would, as a job that has not ended."""
    captured: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> Any:
        captured.append(kwargs)
        job_id = f"aaa{len(captured)}"
        store.create_job(
            JobRecord(
                id=job_id,
                type="deploy",
                name=kwargs["name"],
                domain=kwargs["metadata"]["domain"],
                status="running",
            )
        )

        class Queued:
            id = job_id
            status = type("Status", (), {"value": "pending"})()

            def to_dict(self) -> dict[str, Any]:
                return {"id": job_id}

        return Queued()

    manager = type("FakeJobs", (), {"create_job": staticmethod(create_job)})()
    monkeypatch.setattr("noust.web.api.apps.get_job_manager", lambda: manager)
    return captured


def fail(store: Any, job_id: str, error: str = "Release 1 did not pass its health check") -> None:
    store.update_job(job_id, status="failed", error=error, finished_at="2026-11-10T10:00:00")


class TestWhatIsRemembered:
    def test_a_deployment_from_a_source_is_remembered_without_its_credential(
        self, client: TestClient, store: Any, queued: list[dict[str, Any]]
    ) -> None:
        assert client.post("/api/apps", json=PAYLOAD).status_code == 202
        fail(store, "aaa1")

        [item] = client.get("/api/first-deploys").json()["items"]

        assert item["job_id"] == "aaa1"
        assert item["domain"] == "shop.example.com"
        assert item["origin"] == "source"
        assert item["branch"] == "main"
        assert item["app_type"] == "nextjs"
        assert "hunter2" not in item["source"]
        assert item["error"] == "Release 1 did not pass its health check"

    def test_a_deployment_that_is_still_running_is_not_listed(
        self, client: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        client.post("/api/apps", json=PAYLOAD)

        assert client.get("/api/first-deploys").json()["items"] == []

    def test_a_recipe_is_remembered_by_its_name(
        self, client: TestClient, store: Any, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            "/api/apps", json={"domain": "kuma.example.com", "recipe": "uptime-kuma"}
        )
        assert response.status_code == 202, response.text
        fail(store, "aaa1")

        [item] = client.get("/api/first-deploys", params={"domain": "kuma.example.com"}).json()[
            "items"
        ]

        assert (item["origin"], item["recipe"]) == ("recipe", "uptime-kuma")

    def test_an_image_is_remembered_as_one(
        self, client: TestClient, store: Any, queued: list[dict[str, Any]]
    ) -> None:
        response = client.post(
            "/api/apps",
            json={
                "domain": "img.example.com",
                "container": {"method": "image", "image": "nginx:1.27"},
            },
        )
        assert response.status_code == 202, response.text
        fail(store, "aaa1")

        [item] = client.get("/api/first-deploys").json()["items"]

        assert (item["origin"], item["source"]) == ("image", "nginx:1.27")

    def test_a_refused_request_leaves_nothing_behind(
        self, client: TestClient, store: Any, queued: list[dict[str, Any]]
    ) -> None:
        store.create_app(App(domain="shop.example.com", app_type="nodejs", app_path="/srv/shop"))

        assert client.post("/api/apps", json=PAYLOAD).status_code == 409

        with store._transaction() as cursor:
            cursor.execute("SELECT COUNT(*) FROM first_deploys")
            assert cursor.fetchone()[0] == 0


class TestWhoMayAsk:
    def test_listing_demands_a_session(self, anonymous: TestClient) -> None:
        assert anonymous.get("/api/first-deploys").status_code in (401, 403)

    def test_dismissing_demands_a_session(self, anonymous: TestClient) -> None:
        assert anonymous.post("/api/first-deploys/aaa1/dismiss").status_code in (401, 403)

    def test_a_dismissed_failure_is_no_longer_listed(
        self, client: TestClient, store: Any, queued: list[dict[str, Any]]
    ) -> None:
        client.post("/api/apps", json=PAYLOAD)
        fail(store, "aaa1")

        response = client.post("/api/first-deploys/aaa1/dismiss")

        assert response.status_code == 204, response.text
        assert client.get("/api/first-deploys").json()["items"] == []

    def test_dismissing_what_is_not_there_is_a_404(self, client: TestClient) -> None:
        assert client.post("/api/first-deploys/abc123/dismiss").status_code == 404

    def test_the_route_ids_are_judged(self, client: TestClient) -> None:
        assert client.post("/api/first-deploys/nothing/dismiss").status_code == 400
