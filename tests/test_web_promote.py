# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Promoting a release over HTTP and from the terminal (checklist B-78).

The promotion itself is tested in ``test_promote.py``; pinned here are the
shapes, who may ask, that both applications must be ones the caller sees, and
that the work is a job.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.store import NoustStore
from noust.deployers.releases import Release
from noust.web.api import app_promote
from noust.web.auth import STATE_DIR_ENV
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    captured: list[dict[str, Any]] = []

    class Manager:
        def create_job(self, **kwargs: Any) -> Any:
            captured.append(kwargs)
            return SimpleNamespace(
                id=f"job-{len(captured)}",
                status=SimpleNamespace(value="pending"),
                to_dict=lambda: {"id": f"job-{len(captured)}"},
            )

    monkeypatch.setattr(app_promote, "get_job_manager", Manager)
    return captured


def plan_for(source: str, target: str, release: str | None = None) -> Any:
    chosen = Release(
        id=release or "20261013-100000-abc1234",
        path=Path("/x"),
        commit="abc1234",
        created_at="2026-10-13T10:00:00+00:00",
        active=True,
    )
    return SimpleNamespace(release=chosen, warnings=[])


class TestPromoting:
    def test_it_queues_a_job_for_the_target(
        self, world: World, queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(app_promote, "plan_promotion", plan_for)

        response = world.client.post(
            f"/api/apps/{ACME}/promote",
            json={"target": ACME, "release": "20261013-100000-abc1234"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 202, response.text
        assert queued[0]["kwargs"]["release"] == "20261013-100000-abc1234"
        assert queued[0]["metadata"]["domain"] == ACME

    def test_a_refusal_of_the_plan_is_a_400_before_anything_is_queued(
        self, world: World, queued: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/promote", json={"target": ACME}, headers=world.as_("alice")
        )

        assert response.status_code == 400
        assert "itself" in response.text
        assert queued == []

    def test_an_application_the_caller_does_not_see_is_not_found(
        self, world: World, queued: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/promote", json={"target": GLOBEX}, headers=world.as_("alice")
        )

        assert response.status_code == 404
        assert queued == []

    def test_a_viewer_cannot_promote(self, world: World, queued: list[dict[str, Any]]) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/promote", json={"target": ACME}, headers=world.as_("dana")
        )

        assert response.status_code == 403

    def test_where_a_release_can_go(self, world: World) -> None:
        response = world.client.get(f"/api/apps/{ACME}/promotion", headers=world.as_("alice"))

        assert response.status_code == 200, response.text
        assert response.json()["domain"] == ACME and response.json()["targets"] == []

    def test_an_unknown_application_has_nowhere_to_go(self, world: World) -> None:
        response = world.client.get(f"/api/apps/{GLOBEX}/promotion", headers=world.as_("alice"))

        assert response.status_code == 404


class TestCloning:
    @pytest.fixture(autouse=True)
    def clone_plan(self, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
        calls: list[dict[str, Any]] = []

        def fake_plan(domain: str, new: str, **kwargs: Any) -> Any:
            calls.append({"domain": domain, "new": new, **kwargs})
            create = SimpleNamespace(port=4000, app_type="nodejs")
            return SimpleNamespace(
                source=SimpleNamespace(domain=domain),
                plan=SimpleNamespace(
                    domain=new, steps=[], create=create, exported_domain=domain, document={}
                ),
                extras=[],
                database=None,
            )

        monkeypatch.setattr(app_promote, "plan_clone", fake_plan)
        return calls

    def test_it_queues_a_deploy_job_in_the_originals_project(
        self, world: World, queued: list[dict[str, Any]], clone_plan: list[dict[str, Any]]
    ) -> None:
        original = world.store.get_app(ACME)
        assert original is not None

        response = world.client.post(
            f"/api/apps/{ACME}/clone",
            json={"domain": "copy.acme.example", "database": "empty"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 202, response.text
        assert queued[0]["ownership"] == (original.project_id, original.environment_id)
        assert queued[0]["metadata"]["clone"] is True
        # A team admin does not get the server's parts (cron jobs, backup schedule).
        assert clone_plan[0]["server_parts"] is False
        assert clone_plan[0]["database"] == "empty"

    def test_an_administrator_gets_the_whole_clone(
        self, world: World, queued: list[dict[str, Any]], clone_plan: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/clone",
            json={"domain": "copy.acme.example"},
            headers=world.as_("admin"),
        )

        assert response.status_code == 202, response.text
        assert clone_plan[0]["server_parts"] is True

    def test_a_domain_that_is_taken_is_a_409(
        self, world: World, queued: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/clone", json={"domain": GLOBEX}, headers=world.as_("alice")
        )

        assert response.status_code == 409
        assert queued == []

    def test_someone_elses_application_is_not_found_and_a_viewer_cannot(
        self, world: World, queued: list[dict[str, Any]]
    ) -> None:
        other = world.client.post(
            f"/api/apps/{GLOBEX}/clone",
            json={"domain": "x.acme.example"},
            headers=world.as_("alice"),
        )
        viewer = world.client.post(
            f"/api/apps/{ACME}/clone", json={"domain": "x.acme.example"}, headers=world.as_("dana")
        )

        assert other.status_code == 404 and viewer.status_code == 403
        assert queued == []

    def test_naming_a_project_the_caller_cannot_manage_is_refused(
        self, world: World, queued: list[dict[str, Any]]
    ) -> None:
        response = world.client.post(
            f"/api/apps/{ACME}/clone",
            json={"domain": "x.acme.example", "team": "globex"},
            headers=world.as_("alice"),
        )

        assert response.status_code == 404
        assert queued == []


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def state(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, store: NoustStore) -> None:
        directory = tmp_path / "state"
        directory.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))

    @pytest.fixture
    def store(self, tmp_path: Path) -> Any:
        NoustStore.reset_instance()
        instance = NoustStore(tmp_path / "noust.db")
        yield instance
        NoustStore.reset_instance()

    def run(self, *args: str, code: int = 0) -> str:
        result = CliRunner().invoke(root_cli, list(args))
        assert result.exit_code == code, f"{result.output} {result.exception}"
        return f"{result.output}{result.exception or ''}"

    def test_promotion_says_when_there_is_nowhere_to_go(self, store: NoustStore) -> None:
        from noust.core.store import App

        store.create_app(App(domain="a.example.com", app_type="nodejs", layout="releases"))

        out = self.run("app", "promotion", "a.example.com")

        assert "no sibling" in out
        assert json.loads(self.run("--json", "app", "promotion", "a.example.com"))["targets"] == []

    def test_promote_asks_first_and_refuses_what_the_plan_refuses(self, store: NoustStore) -> None:
        out = self.run("app", "promote", "a.example.com", "--to", "b.example.com", "--yes", code=1)

        assert "not found" in out.lower()
