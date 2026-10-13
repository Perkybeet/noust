# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags on applications, the list filtered by one, and deploying a tag (checklist B-79).

The store and the tag manager are tested against a store of their own, the API
and the CLI against the two-team world, since what a tag covers depends on who
asks.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.core.app_tags import MAX_TAGS, AppTags, validate_tag, validate_tags
from noust.core.exceptions import NoustError, ValidationError
from noust.core.logger import Logger
from noust.core.store import App, NoustStore
from noust.managers import tag_deploys
from noust.web.auth import STATE_DIR_ENV
from tests.teams_support import World, build_world

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@pytest.fixture
def store(tmp_path: Path) -> Iterator[NoustStore]:
    NoustStore.reset_instance()
    instance = NoustStore(tmp_path / "noust.db")
    for domain in ("a.example.com", "b.example.com", "c.example.com"):
        instance.create_app(App(domain=domain, app_type="nodejs", app_path=f"/tmp/{domain}"))
    yield instance
    instance.close()
    NoustStore.reset_instance()


class TestTheTags:
    def test_a_tag_is_lower_case_and_trimmed(self) -> None:
        assert validate_tag("  Prod ") == "prod"
        assert validate_tags(["Prod", "prod", "customer-a"]) == ["prod", "customer-a"]

    @pytest.mark.parametrize("tag", ["", "-x", "x-", "a b", "a/b", "x" * 33, "ñ"])
    def test_what_is_not_a_tag_is_refused(self, tag: str) -> None:
        with pytest.raises(ValidationError):
            validate_tag(tag)

    def test_an_application_carries_a_limited_number(self) -> None:
        with pytest.raises(ValidationError, match=str(MAX_TAGS)):
            validate_tags([f"t{i}" for i in range(MAX_TAGS + 1)])

    def test_set_add_remove(self, store: NoustStore) -> None:
        tags = AppTags(store)

        assert tags.set("a.example.com", ["prod", "eu"]) == ["eu", "prod"]
        assert tags.add("a.example.com", ["nightly"]) == ["eu", "nightly", "prod"]
        assert tags.remove("a.example.com", ["eu", "never-had"]) == ["nightly", "prod"]
        assert tags.of("a.example.com") == ["nightly", "prod"]
        assert tags.of("b.example.com") == []

    def test_an_application_that_does_not_exist_cannot_be_tagged(self, store: NoustStore) -> None:
        with pytest.raises(ValidationError, match="No application"):
            AppTags(store).set("ghost.example.com", ["prod"])

    def test_the_tags_go_with_the_application(self, store: NoustStore) -> None:
        tags = AppTags(store)
        tags.set("a.example.com", ["prod"])

        store.delete_app("a.example.com")

        assert tags.by_application() == {}

    def test_tags_survive_a_full_row_write_of_the_application(self, store: NoustStore) -> None:
        tags = AppTags(store)
        tags.set("a.example.com", ["prod"])
        app = store.get_app("a.example.com")
        assert app is not None

        app.status = "running"
        store.update_app(app)

        assert tags.of("a.example.com") == ["prod"]

    def test_copy_gives_another_application_the_same_tags(self, store: NoustStore) -> None:
        tags = AppTags(store)
        tags.set("a.example.com", ["prod", "eu"])
        tags.set("b.example.com", ["old"])

        assert tags.copy("a.example.com", "b.example.com") == ["eu", "prod"]


class TestDeployingATag:
    def test_it_updates_the_applications_that_carry_it_in_order(self, store: NoustStore) -> None:
        tags = AppTags(store)
        tags.set("b.example.com", ["prod"])
        tags.set("a.example.com", ["prod"])
        tags.set("c.example.com", ["staging"])
        updated: list[str] = []

        outcomes = tag_deploys.deploy_tag_now(
            "prod", logger=Logger(), force=True, update=updated.append
        )

        assert updated == ["a.example.com", "b.example.com"]
        assert [(o.domain, o.outcome) for o in outcomes] == [
            ("a.example.com", "updated"),
            ("b.example.com", "updated"),
        ]

    def test_a_failure_does_not_stop_the_others_unless_asked(self, store: NoustStore) -> None:
        tags = AppTags(store)
        for domain in ("a.example.com", "b.example.com", "c.example.com"):
            tags.set(domain, ["prod"])

        def update(domain: str) -> None:
            if domain == "a.example.com":
                raise NoustError("the build failed")

        carried_on = tag_deploys.deploy_tag_now("prod", logger=Logger(), force=True, update=update)
        stopped = tag_deploys.deploy_tag_now(
            "prod", logger=Logger(), force=True, stop_on_failure=True, update=update
        )

        assert [o.outcome for o in carried_on] == ["failed", "updated", "updated"]
        assert [o.outcome for o in stopped] == ["failed", "skipped", "skipped"]
        assert carried_on[0].detail == "the build failed"

    def test_an_application_with_nothing_new_is_skipped_unless_forced(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from types import SimpleNamespace

        from noust.deployers import lifecycle

        AppTags(store).set("a.example.com", ["prod"])
        monkeypatch.setattr(
            lifecycle,
            "check_upstream",
            lambda domain, logger=None: SimpleNamespace(
                has_new_commits=False, summary="No new commits on main since abc1234, which is live"
            ),
        )
        updated: list[str] = []

        skipped = tag_deploys.deploy_tag_now("prod", logger=Logger(), update=updated.append)
        forced = tag_deploys.deploy_tag_now(
            "prod", logger=Logger(), force=True, update=updated.append
        )

        assert skipped[0].outcome == "skipped" and "No new commits" in skipped[0].detail
        assert forced[0].outcome == "updated" and updated == ["a.example.com"]

    def test_previews_are_never_deployed_from_a_tag(self, store: NoustStore) -> None:
        store.create_app(
            App(
                domain="pr-1-a.previews.example.com",
                app_type="nodejs",
                app_path="/tmp/pr",
                preview_parent="a.example.com",
            )
        )
        AppTags(store).set("pr-1-a.previews.example.com", ["prod"])

        assert tag_deploys.applications_with("prod") == []


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


class TestTheApi:
    def test_an_application_is_tagged_and_listed_by_its_tag(self, world: World) -> None:
        put = world.client.put(
            f"/api/apps/{ACME}/tags", json={"tags": ["Prod", "eu"]}, headers=world.as_("alice")
        )

        assert put.status_code == 200, put.text
        assert put.json()["tags"] == ["eu", "prod"]
        listed = world.client.get("/api/apps?tag=prod", headers=world.as_("alice")).json()
        assert [a["domain"] for a in listed["apps"]] == [ACME]
        assert listed["apps"][0]["tags"] == ["eu", "prod"]
        assert (
            world.client.get("/api/apps?tag=other", headers=world.as_("alice")).json()["apps"] == []
        )

    def test_a_tag_is_the_callers_own(self, world: World) -> None:
        world.client.put(
            f"/api/apps/{ACME}/tags", json={"tags": ["prod"]}, headers=world.as_("alice")
        )
        world.client.put(
            f"/api/apps/{GLOBEX}/tags", json={"tags": ["prod"]}, headers=world.as_("bob")
        )

        mine = world.client.get("/api/tags", headers=world.as_("alice")).json()
        everything = world.client.get("/api/tags", headers=world.as_("admin")).json()

        assert mine["tags"] == [{"tag": "prod", "applications": [ACME]}]
        assert everything["tags"][0]["applications"] == [GLOBEX, ACME]

    def test_another_teams_application_cannot_be_tagged(self, world: World) -> None:
        response = world.client.put(
            f"/api/apps/{GLOBEX}/tags", json={"tags": ["prod"]}, headers=world.as_("alice")
        )

        assert response.status_code == 404

    def test_a_viewer_reads_and_cannot_tag(self, world: World) -> None:
        assert (
            world.client.get(f"/api/apps/{ACME}/tags", headers=world.as_("dana")).status_code == 200
        )
        assert (
            world.client.put(
                f"/api/apps/{ACME}/tags", json={"tags": ["x"]}, headers=world.as_("dana")
            ).status_code
            == 403
        )

    def test_a_bad_tag_is_a_400(self, world: World) -> None:
        response = world.client.put(
            f"/api/apps/{ACME}/tags", json={"tags": ["not a tag"]}, headers=world.as_("alice")
        )

        assert response.status_code == 400

    def test_deploying_a_tag_queues_one_update_per_application_the_caller_sees(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world.client.put(
            f"/api/apps/{ACME}/tags", json={"tags": ["prod"]}, headers=world.as_("alice")
        )
        world.client.put(
            f"/api/apps/{GLOBEX}/tags", json={"tags": ["prod"]}, headers=world.as_("bob")
        )
        queued: list[dict[str, Any]] = []

        class Manager:
            def create_job(self, **kwargs: Any) -> Any:
                queued.append(kwargs)
                return type("J", (), {"id": f"job-{len(queued)}"})()

        monkeypatch.setattr("noust.web.jobs.get_job_manager", Manager)

        response = world.client.post(
            "/api/tags/prod/deploy", json={"force": True}, headers=world.as_("alice")
        )

        assert response.status_code == 202, response.text
        assert [r["domain"] for r in response.json()["results"]] == [ACME]
        assert response.json()["queued"] == 1
        assert [job["kwargs"] for job in queued] == [{"domain": ACME}]

    def test_a_tag_nobody_carries_is_a_404(self, world: World) -> None:
        response = world.client.post(
            "/api/tags/ghost/deploy", json={"force": True}, headers=world.as_("alice")
        )

        assert response.status_code == 404

    def test_a_viewer_cannot_deploy_a_tag(self, world: World) -> None:
        world.client.put(
            f"/api/apps/{ACME}/tags", json={"tags": ["prod"]}, headers=world.as_("alice")
        )

        response = world.client.post("/api/tags/prod/deploy", json={}, headers=world.as_("dana"))

        assert response.status_code == 403


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def state_dir(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory = tmp_path / "state"
        directory.mkdir(mode=0o700)
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))

    def run(self, *args: str, code: int = 0) -> str:
        result = CliRunner().invoke(root_cli, list(args))
        assert result.exit_code == code, f"{result.output} {result.exception}"
        return f"{result.output}{result.exception or ''}"

    def test_tag_list_and_the_filter_of_list(self, store: NoustStore, runner: Any) -> None:
        self.run("app", "tag", "add", "a.example.com", "Prod", "eu")
        self.run("app", "tag", "add", "b.example.com", "staging")

        listed = json.loads(self.run("--json", "app", "tag", "list", "--tag", "prod"))
        by_list = json.loads(self.run("--json", "list", "--tag", "staging"))

        assert listed["applications"] == {"a.example.com": ["eu", "prod"]}
        assert [item["domain"] for item in by_list["items"]] == ["b.example.com"]

    def test_remove_and_set(self, store: NoustStore) -> None:
        self.run("app", "tag", "set", "a.example.com", "x", "y")
        self.run("app", "tag", "remove", "a.example.com", "x")
        assert AppTags(store).of("a.example.com") == ["y"]

        self.run("app", "tag", "set", "a.example.com")
        assert AppTags(store).of("a.example.com") == []

    def test_deploying_a_tag_nobody_carries_says_so(self, store: NoustStore) -> None:
        out = self.run("app", "tag", "deploy", "ghost", code=1)

        assert "No application carries the tag ghost" in out

    def test_a_bad_tag_is_refused(self, store: NoustStore) -> None:
        self.run("app", "tag", "add", "a.example.com", "not a tag", code=1)
