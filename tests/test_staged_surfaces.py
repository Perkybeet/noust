# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Build without serving, through the command line and the API (checklist B-18).

The building and the promoting are ``test_staged_release.py``'s; here are the doors.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner, Result
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import deploy_freeze
from noust.core.store import App, AppLayout, NoustStore, ReleaseRecord
from noust.deployers import lifecycle, staged
from noust.web.api import app_staged
from noust.web.api.auth import get_current_session
from noust.web.api.deps import install_error_handlers

DOMAIN = "shop.example.com"
WAITING = "20261016-101500-abc1234"
SERVED = "20261015-101500-def5678"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    root = tmp_path / "apps" / "shop-example-com"
    for name in (WAITING, SERVED):
        (root / "releases" / name).mkdir(parents=True)
    (root / "current").symlink_to(root / "releases" / SERVED)
    app = instance.create_app(
        App(
            domain=DOMAIN,
            app_type="nodejs",
            source="https://github.com/you/app.git",
            port=3000,
            app_path=str(root),
            layout=AppLayout.RELEASES.value,
        )
    )
    for name, status in ((WAITING, "built"), (SERVED, "active")):
        instance.record_release(
            ReleaseRecord(
                id=name,
                app_id=app.id or 0,
                git_commit=name.split("-")[2],
                created_at="2026-10-16T10:15:00+00:00",
                status=status,
                path=str(root / "releases" / name),
            )
        )
    yield instance
    instance.close()
    NoustStore.reset_instance()


class TestTheCommandLine:
    def invoke(self, *args: str) -> Result:
        return CliRunner().invoke(root_cli, list(args))

    def test_update_build_only_stages_and_does_not_update(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[dict[str, Any]] = []

        def fake(domain: str, **kwargs: Any) -> Any:
            asked.append({"domain": domain, **{k: v for k, v in kwargs.items() if k != "logger"}})
            return staged.StagedBuild(domain, WAITING, "abc1234")

        monkeypatch.setattr(staged, "stage_build", fake)
        monkeypatch.setattr(
            lifecycle, "update_app", lambda *a, **k: pytest.fail("nothing is updated")
        )

        result = self.invoke("update", DOMAIN, "--build-only", "--commit", "abc1234")

        assert result.exit_code == 0, result.output
        assert asked[0]["domain"] == DOMAIN and asked[0]["commit"] == "abc1234"

    def test_build_only_is_not_for_a_tag_or_a_new_source(self, store: NoustStore) -> None:
        for extra in (["--tag", "v1"], ["--source", "https://github.com/x/y.git"]):
            result = self.invoke("update", DOMAIN, "--build-only", *extra)
            assert result.exit_code != 0, extra

    def test_promote_makes_the_release_serve(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[tuple[str, str | None, dict[str, Any]]] = []

        def fake(domain: str, release: str | None = None, **kwargs: Any) -> Any:
            asked.append((domain, release, kwargs))
            return staged.Promoted(domain, WAITING, SERVED, 12)

        monkeypatch.setattr(staged, "promote_staged", fake)

        result = self.invoke(
            "releases", "promote", DOMAIN, WAITING, "--during-freeze", "--schema-changed-ok"
        )

        assert result.exit_code == 0, result.output
        assert f"Release {WAITING} of {DOMAIN} is serving" in result.output
        assert asked[0][0:2] == (DOMAIN, WAITING)
        assert asked[0][2]["frozen_ok"] is True and asked[0][2]["schema_changed_ok"] is True

    def test_promote_prints_json(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            staged, "promote_staged", lambda *a, **k: staged.Promoted(DOMAIN, WAITING, SERVED, 12)
        )

        result = self.invoke("releases", "promote", DOMAIN, "--json")

        body = json.loads(result.output)
        assert body["release_id"] == WAITING and body["previous_id"] == SERVED

    def test_the_listing_says_which_release_waits(self, store: NoustStore) -> None:
        result = self.invoke("releases", "list", DOMAIN)

        assert f"noust releases promote {DOMAIN} {WAITING}" in result.output


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    made: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> Any:
            made.append(kwargs)
            return SimpleNamespace(
                id=f"job-{len(made)}",
                status=SimpleNamespace(value="pending"),
                to_dict=lambda: {"id": f"job-{len(made)}"},
            )

    monkeypatch.setattr(app_staged, "get_job_manager", lambda: Manager())
    return made


@pytest.fixture
def client(store: NoustStore) -> TestClient:
    server = FastAPI()
    install_error_handlers(server)
    server.include_router(app_staged.router, prefix="/api/apps")
    server.dependency_overrides[get_current_session] = lambda: {"sid": "abcdef012345"}
    return TestClient(server)


class TestTheApi:
    def test_staging_queues_a_build_job(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/releases/stage", json={"commit": "abc1234"})

        assert response.status_code == 202, response.text
        assert jobs[0]["func"] is app_staged.stage_release_job
        assert jobs[0]["kwargs"]["commit"] == "abc1234"
        assert jobs[0]["metadata"] == {"domain": DOMAIN, "staged": True}

    def test_promoting_queues_a_job_for_a_release_that_waits(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/releases/{WAITING}/promote", json={})

        assert response.status_code == 202, response.text
        assert jobs[0]["func"] is app_staged.promote_release_job
        assert jobs[0]["kwargs"] == {
            "domain": DOMAIN,
            "release_id": WAITING,
            "schema_changed_ok": False,
            "frozen_ok": False,
        }

    def test_a_release_that_already_served_is_not_promoted(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        response = client.post(f"/api/apps/{DOMAIN}/releases/{SERVED}/promote", json={})

        assert response.status_code == 404 and jobs == []

    def test_a_freeze_holds_the_promotion_until_a_person_says_so(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        deploy_freeze.create("incident 412")

        held = client.post(f"/api/apps/{DOMAIN}/releases/{WAITING}/promote", json={})
        allowed = client.post(
            f"/api/apps/{DOMAIN}/releases/{WAITING}/promote", json={"frozen_ok": True}
        )

        assert held.status_code == 409 and held.json()["error"] == "deploys_frozen"
        assert allowed.status_code == 202 and len(jobs) == 1

    def test_staging_is_not_held_by_a_freeze(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        deploy_freeze.create("incident 412")

        assert client.post(f"/api/apps/{DOMAIN}/releases/stage", json={}).status_code == 202

    def test_what_is_not_a_release_or_an_application_is_refused(
        self, client: TestClient, jobs: list[dict[str, Any]]
    ) -> None:
        assert client.post(f"/api/apps/{DOMAIN}/releases/..%2Fx/promote", json={}).status_code in (
            400,
            404,
        )
        assert client.post("/api/apps/gone.example.com/releases/stage", json={}).status_code == 404
        assert jobs == []

    def test_the_listing_marks_it(self, store: NoustStore) -> None:
        from noust.web.api.apps import router as apps_router

        server = FastAPI()
        install_error_handlers(server)
        server.include_router(apps_router, prefix="/api/apps")
        server.dependency_overrides[get_current_session] = lambda: {"sid": "abcdef012345"}

        body = TestClient(server).get(f"/api/apps/{DOMAIN}/releases").json()

        by_id = {item["id"]: item for item in body["items"]}
        assert by_id[WAITING]["waiting"] is True and by_id[SERVED]["waiting"] is False
