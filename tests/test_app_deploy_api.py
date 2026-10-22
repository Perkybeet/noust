# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Deploying from a pipeline (checklist A-09): a URL per application, a call for several.

``POST /api/apps/{domain}/deploy`` and ``POST /api/deploy`` queue the update every
other door queues; what is tested here is what they add: the options (force,
no cache, a commit or a tag, an image tag, a pull request), the results of a
batch, the scope a deploy token needs, and that a pipeline never gets a GET.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.core.container_store import ContainerStore
from noust.core.exceptions import QueueFullError
from noust.core.store import App, NoustStore
from noust.web.api import app_deploy
from noust.web.api import jobs as jobs_api
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "shop.example.com"


@pytest.fixture
def store(tmp_path: Path):
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for name in (DOMAIN, "blog.example.com"):
        instance.create_app(
            App(domain=name, app_type="nodejs", source="x", app_path=str(tmp_path / name))
        )
    yield instance
    NoustStore.reset_instance()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The update jobs created, instead of run."""
    created: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> Any:
            created.append(kwargs)
            return SimpleNamespace(id=f"job-{len(created)}")

    monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Manager())
    # Both the deploy call and the update job it queues are tested without git.
    monkeypatch.setattr(
        jobs_api,
        "check_upstream",
        lambda domain, **_k: SimpleNamespace(has_new_commits=True, summary="new"),
    )
    return created


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    server = FastAPI()
    install_error_handlers(server)
    server.include_router(app_deploy.router, prefix="/api/apps")
    server.include_router(app_deploy.batch_router, prefix="/api")
    session = {"type": "token", "session_id": "token:ci", "scope": "deploy", "name": "ci"}
    server.dependency_overrides[get_current_session] = lambda: session
    return TestClient(server)


class TestOneApplication:
    def test_an_empty_body_is_an_update(self, client: TestClient, queued: list) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/deploy")

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["results"] == [
            {"domain": DOMAIN, "status": "queued", "job_id": "job-1", "detail": None}
        ]
        assert queued[0]["kwargs"] == {"domain": DOMAIN}
        assert queued[0]["actor"]

    def test_nothing_new_is_a_success_a_pipeline_can_read(
        self, client: TestClient, queued: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=False, summary="same commit"),
        )

        response = client.post(f"/api/apps/{DOMAIN}/deploy")

        assert response.status_code == 200
        assert response.json()["results"][0]["status"] == "nothing_new"
        assert queued == []

    def test_force_deploys_it_anyway(
        self, client: TestClient, queued: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=False, summary="same commit"),
        )

        response = client.post(f"/api/apps/{DOMAIN}/deploy", json={"force": True})

        assert response.status_code == 202
        assert len(queued) == 1

    def test_no_cache_is_passed_on_and_implies_force(
        self, client: TestClient, queued: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=False, summary="same commit"),
        )

        response = client.post(f"/api/apps/{DOMAIN}/deploy", json={"no_cache": True})

        assert response.status_code == 202
        assert queued[0]["kwargs"]["no_cache"] is True

    def test_a_commit_is_exactly_what_is_deployed(self, client: TestClient, queued: list) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/deploy", json={"commit": "abc1234"})

        assert response.status_code == 202
        assert queued[0]["kwargs"] == {"domain": DOMAIN, "commit": "abc1234"}
        # An exact commit is its own job: it never merges into a waiting update.
        assert queued[0]["coalesce_key"] is None

    def test_a_tag(self, client: TestClient, queued: list) -> None:
        client.post(f"/api/apps/{DOMAIN}/deploy", json={"tag": "v1.2.0"})
        assert queued[0]["kwargs"]["tag"] == "v1.2.0"

    def test_a_second_request_joins_the_update_that_waits(
        self, client: TestClient, queued: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Waiting:
            def create_job(self, **kwargs: Any) -> Any:
                kwargs["on_merge"](SimpleNamespace(id="job-0"))
                return SimpleNamespace(id="job-0")

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Waiting())

        body = client.post(f"/api/apps/{DOMAIN}/deploy").json()

        assert body["results"][0]["status"] == "joined"
        assert "already waiting" in body["message"]

    def test_a_full_queue_is_a_429_the_pipeline_retries(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Full:
            def create_job(self, **kwargs: Any) -> Any:
                raise QueueFullError("The deploy queue is full (20 waiting)", limit=20)

        monkeypatch.setattr(jobs_api, "get_job_manager", lambda: Full())
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=True, summary="new"),
        )

        response = client.post(f"/api/apps/{DOMAIN}/deploy")

        assert response.status_code == 429
        assert response.json()["error"] == "queue_full"

    def test_an_unknown_application_is_a_404(self, client: TestClient, queued: list) -> None:
        assert client.post("/api/apps/nobody.example.com/deploy").status_code == 404

    @pytest.mark.parametrize(
        ("body", "field"),
        [
            ({"commit": "abc1234", "tag": "v1"}, "tag"),
            ({"pull_request": 4, "commit": "abc1234"}, "pull_request"),
            ({"pull_request": 0}, "pull_request"),
            ({"image_tag": "1.2", "commit": "abc1234"}, "image_tag"),
        ],
    )
    def test_options_that_ask_for_two_things_are_refused(
        self, client: TestClient, queued: list, body: dict, field: str
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/deploy", json=body)

        assert response.status_code == 400, response.text
        assert queued == []

    def test_a_get_deploys_nothing(self, client: TestClient, queued: list) -> None:
        assert client.get(f"/api/apps/{DOMAIN}/deploy").status_code == 405
        assert queued == []


class TestAnImageTag:
    def make_image_app(self, store: NoustStore, tmp_path: Path) -> None:
        from noust.core.container_store import ContainerSettings

        store.create_app(
            App(
                domain="img.example.com",
                app_type="container",
                source="ghcr.io/acme/app:1.0.0",
                app_path=str(tmp_path / "img"),
            )
        )
        ContainerStore(store).save(
            "img.example.com",
            ContainerSettings(method="image", image="ghcr.io/acme/app:1.0.0@sha256:" + "a" * 64),
            actor="test",
        )

    def test_the_tag_replaces_the_one_the_image_ran_and_drops_its_digest(
        self, client: TestClient, queued: list, store: NoustStore, tmp_path: Path
    ) -> None:
        self.make_image_app(store, tmp_path)

        response = client.post("/api/apps/img.example.com/deploy", json={"image_tag": "1.4.2"})

        assert response.status_code == 202, response.text
        assert ContainerStore(store).get("img.example.com").image == "ghcr.io/acme/app:1.4.2"
        assert len(queued) == 1

    def test_a_tag_that_is_not_one_is_refused_before_anything_changes(
        self, client: TestClient, queued: list, store: NoustStore, tmp_path: Path
    ) -> None:
        self.make_image_app(store, tmp_path)

        response = client.post("/api/apps/img.example.com/deploy", json={"image_tag": "-x --y"})

        assert response.status_code == 400
        assert (
            ContainerStore(store).get("img.example.com").image.startswith("ghcr.io/acme/app:1.0.0")
        )
        assert queued == []

    def test_an_application_that_runs_no_image_cannot_take_one(
        self, client: TestClient, queued: list
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/deploy", json={"image_tag": "1.2"})

        assert response.status_code == 400
        assert queued == []


class TestAPullRequest:
    def test_its_preview_is_rebuilt(
        self, client: TestClient, queued: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[str, int]] = []
        monkeypatch.setattr(
            app_deploy,
            "rebuild_preview",
            lambda domain, number, actor: (
                seen.append((domain, number)) or SimpleNamespace(id="job-p")
            ),
        )

        response = client.post(f"/api/apps/{DOMAIN}/deploy", json={"pull_request": 7})

        assert response.status_code == 202
        assert seen == [(DOMAIN, 7)]
        assert response.json()["results"][0]["job_id"] == "job-p"
        assert queued == []

    def test_without_such_a_preview_it_says_so(self, client: TestClient, store: NoustStore) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/deploy", json={"pull_request": 99})

        assert response.status_code == 400
        assert "no preview of pull request #99" in response.text


class TestSeveralApplications:
    def test_each_is_its_own_job_and_its_own_result(self, client: TestClient, queued: list) -> None:
        response = client.post(
            "/api/deploy",
            json={"apps": [DOMAIN, "blog.example.com", "ghost.example.com", DOMAIN]},
        )

        assert response.status_code == 202, response.text
        results = {item["domain"]: item["status"] for item in response.json()["results"]}
        assert results == {
            DOMAIN: "queued",
            "blog.example.com": "queued",
            "ghost.example.com": "not_found",
        }
        assert len(queued) == 2
        assert response.json()["message"] == "2 of 3 applications queued"

    def test_options_are_shared(self, client: TestClient, queued: list) -> None:
        client.post("/api/deploy", json={"apps": [DOMAIN, "blog.example.com"], "no_cache": True})
        assert all(job["kwargs"]["no_cache"] is True for job in queued)

    def test_the_list_cannot_be_empty_or_long(self, client: TestClient, queued: list) -> None:
        assert client.post("/api/deploy", json={"apps": []}).status_code == 422
        many = [f"app{i}.example.com" for i in range(app_deploy.MAX_BATCH + 1)]
        assert client.post("/api/deploy", json={"apps": many}).status_code == 400

    def test_nothing_queued_is_a_200(
        self, client: TestClient, queued: list, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            jobs_api,
            "check_upstream",
            lambda domain, **_k: SimpleNamespace(has_new_commits=False, summary="same"),
        )
        response = client.post("/api/deploy", json={"apps": [DOMAIN]})
        assert response.status_code == 200


class TestWhoMayCall:
    def test_both_routes_are_deployments(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.routes_app_deploy import ROUTES

        assert ROUTES[("POST", "/api/apps/{domain}/deploy")] == Permission.APPS_DEPLOY
        assert ROUTES[("POST", "/api/deploy")] == Permission.APPS_DEPLOY

    def test_a_deploy_token_may_call_them_and_a_read_token_may_not(self) -> None:
        from noust.web.auth import DEPLOY_SCOPE_PATHS, DEPLOY_SCOPE_PATTERNS

        assert "/api/deploy" in DEPLOY_SCOPE_PATHS
        assert any(p.match("/api/apps/shop.example.com/deploy") for p in DEPLOY_SCOPE_PATTERNS)
        assert not any(p.match("/api/apps/shop.example.com/delete") for p in DEPLOY_SCOPE_PATTERNS)

    def test_the_batch_belongs_to_the_instance(self) -> None:
        from noust.web.permissions.scopes import INSTANCE, RESOURCE, rule_for

        assert rule_for("POST", "/api/deploy").scoping == INSTANCE
        assert rule_for("POST", "/api/apps/{domain}/deploy").scoping == RESOURCE
