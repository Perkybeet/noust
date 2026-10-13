# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Updating a group of applications with one call, and from the command line (J-08).

One request for what a pipeline would otherwise loop over: every application of
a team, a project or an environment, each through its own health gate, each
held to the caller's permission where it lives, and none of them stopping the
others.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from noust.cli.app import cli as root_cli
from noust.client import ApiClient
from noust.core.store import NoustStore
from noust.core.teams import Scope
from noust.web.jobs import Job, JobType
from noust.web.permissions.access import AccessManager
from tests.client_support import Answer, Wire
from tests.teams_support import World, build_world, make_app, make_person

SHOP = "shop.acme.example"
MOBILE = "m.acme.example"
STAGING = "s.acme.example"
BLOG = "blog.globex.example"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Iterator[World]:
    """Two teams; acme has a second project with a staging environment; updates are queued, not run."""
    built = build_world(tmp_path, monkeypatch)
    built.teams.create_project("acme", "Mobile", slug="mobile")
    built.teams.create_environment("acme", "mobile", "Staging", slug="staging")
    mobile = built.teams.resolve_target(Scope.everything(), team="acme", project="mobile")
    staging = built.teams.resolve_target(
        Scope.everything(), team="acme", project="mobile", environment="staging"
    )
    make_app(built.store, MOBILE, mobile, tmp_path)
    make_app(built.store, STAGING, staging, tmp_path)
    queued: list[dict[str, Any]] = []
    built.queued = queued  # type: ignore[attr-defined]

    class Jobs:
        def create_job(self, **kwargs: Any) -> Job:
            queued.append(kwargs)
            return Job(
                id=f"job{len(queued)}",
                type=JobType.UPDATE,
                name=kwargs["name"],
                description=kwargs["description"],
                metadata=kwargs["metadata"],
                actor=kwargs["actor"],
            )

    monkeypatch.setattr("noust.web.api.jobs.get_job_manager", lambda: Jobs())
    monkeypatch.setattr("noust.web.api.jobs.check_upstream", lambda domain: None)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def update_many(world: World, who: str, **body: Any) -> Any:
    """
    Args:
        world: The server.
        who: One of its people.
        **body: The request.

    Returns:
        The response.
    """
    return world.client.post("/api/jobs/update-many", json=body, headers=world.as_(who))


def domains_queued(world: World) -> set[str]:
    """
    Args:
        world: The server.

    Returns:
        The applications a job was queued for.
    """
    return {call["metadata"]["domain"] for call in world.queued}  # type: ignore[attr-defined]


class TestOneCallForAGroup:
    def test_a_project_is_updated_with_one_job_each(self, world: World) -> None:
        response = update_many(world, "omar", project="mobile", force=True)

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["queued"] == 2 and body["skipped"] == 0
        assert {i["domain"]: i["status"] for i in body["items"]} == {
            MOBILE: "queued",
            STAGING: "queued",
        }
        assert domains_queued(world) == {MOBILE, STAGING}

    def test_an_environment_across_every_project_of_the_callers_teams(self, world: World) -> None:
        response = update_many(world, "omar", environment="production", force=True)

        assert {i["domain"] for i in response.json()["items"]} == {SHOP, MOBILE}

    def test_a_team_is_every_application_in_it(self, world: World) -> None:
        response = update_many(world, "omar", team="acme", force=True)

        assert {i["domain"] for i in response.json()["items"]} == {SHOP, MOBILE, STAGING}

    def test_domains_may_be_named_and_an_unknown_one_is_said(self, world: World) -> None:
        response = update_many(world, "omar", domains=[SHOP, "nope.example"], force=True)

        items = {i["domain"]: i for i in response.json()["items"]}
        assert items[SHOP]["status"] == "queued"
        assert items["nope.example"]["status"] == "skipped"
        assert items["nope.example"]["reason"] == "no such application"

    def test_nothing_to_choose_by_is_refused_rather_than_updating_everything(
        self, world: World
    ) -> None:
        response = update_many(world, "omar")

        assert response.status_code == 400
        assert "Say which applications" in response.json()["detail"]
        assert domains_queued(world) == set()

    def test_a_group_is_bounded(self, world: World, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("noust.web.api.jobs.MAX_GROUP", 1)

        response = update_many(world, "omar", team="acme", force=True)

        assert response.status_code == 400
        assert "at most 1" in response.json()["detail"]

    def test_what_has_nothing_new_is_skipped_with_the_reason_and_the_rest_goes_on(
        self, world: World, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Upstream:
            def __init__(self, new: bool) -> None:
                self.has_new_commits = new
                self.summary = "The branch has nothing new"

        monkeypatch.setattr(
            "noust.web.api.jobs.check_upstream", lambda domain: Upstream(domain == MOBILE)
        )

        response = update_many(world, "omar", project="mobile")

        items = {i["domain"]: i for i in response.json()["items"]}
        assert items[MOBILE]["status"] == "queued"
        assert items[STAGING]["status"] == "skipped"
        assert "nothing new" in items[STAGING]["reason"]


class TestWhoMayAndWhat:
    def test_another_teams_applications_are_not_in_the_group(self, world: World) -> None:
        response = update_many(world, "omar", environment="production", force=True)
        assert BLOG not in {i["domain"] for i in response.json()["items"]}

        named = update_many(world, "omar", domains=[BLOG], force=True).json()["items"]
        assert named == [
            {"domain": BLOG, "status": "skipped", "job_id": None, "reason": "no such application"}
        ]

    def test_a_viewer_may_not_update_anything(self, world: World) -> None:
        response = update_many(world, "dana", team="acme", force=True)

        assert response.status_code == 403
        assert domains_queued(world) == set()

    def test_what_the_caller_may_not_deploy_where_it_lives_is_skipped_not_refused(
        self, world: World
    ) -> None:
        # An operator of the mobile project only, and a reader of the rest of the team.
        person = make_person(world.teams, "pia", "viewer", team="acme", team_role="viewer")
        world.people["pia"] = person
        AccessManager(world.store).bind("pia", "operator", team="acme", project="mobile")
        from noust.web.server import get_token_manager

        account = get_token_manager().accounts.find("pia")
        issued = get_token_manager().create_api_token(
            "pia-fresh", "admin", None, owner=account, allow_elevated=True
        )
        headers = {"Authorization": f"Bearer {issued['token']}"}

        response = world.client.post(
            "/api/jobs/update-many", json={"team": "acme", "force": True}, headers=headers
        )

        assert response.status_code == 202, response.text
        items = {i["domain"]: i for i in response.json()["items"]}
        assert items[MOBILE]["status"] == "queued" and items[STAGING]["status"] == "queued"
        assert items[SHOP]["status"] == "skipped"
        assert items[SHOP]["reason"] == "you may not deploy it"
        assert domains_queued(world) == {MOBILE, STAGING}


class TestFromTheCommandLine:
    @pytest.fixture(autouse=True)
    def home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
        monkeypatch.setattr("time.sleep", lambda seconds: None)

    @pytest.fixture
    def wire(self, monkeypatch: pytest.MonkeyPatch) -> Wire:
        scripted = Wire()
        monkeypatch.setattr(ApiClient, "_network", staticmethod(lambda ca_file: scripted))
        return scripted

    @staticmethod
    def run(*args: str, code: int = 0) -> str:
        result = CliRunner().invoke(
            root_cli,
            ["--remote", "https://noust.test", "--token-env", "T", *args],
            env={"T": "noust_tok_x"},
        )
        output = f"{result.output}{result.exception or ''}"
        assert result.exit_code == code, output
        return output

    def test_a_group_is_queued_in_one_call_and_each_job_is_waited_for(self, wire: Wire) -> None:
        wire.on(
            "POST",
            "/api/jobs/update-many",
            Answer(
                202,
                {
                    "message": "Queued 2 update(s), skipped 0",
                    "queued": 2,
                    "skipped": 0,
                    "items": [
                        {"domain": "a.test", "status": "queued", "job_id": "j1"},
                        {"domain": "b.test", "status": "queued", "job_id": "j2"},
                    ],
                },
            ),
        )
        wire.on("GET", "/api/jobs/j1", Answer(200, {"id": "j1", "state": "succeeded"}))
        wire.on("GET", "/api/jobs/j2", Answer(200, {"id": "j2", "state": "succeeded"}))

        output = self.run("update", "--project", "web", "--env", "staging")

        assert wire.seen[0][3] == {
            "team": None,
            "project": "web",
            "environment": "staging",
            "domains": [],
            "force": False,
        }
        assert "a.test: succeeded" in output and "b.test: succeeded" in output

    def test_one_that_fails_makes_the_exit_one_and_the_others_still_finish(
        self, wire: Wire
    ) -> None:
        wire.on(
            "POST",
            "/api/jobs/update-many",
            Answer(
                202,
                {
                    "message": "Queued 2",
                    "queued": 2,
                    "skipped": 0,
                    "items": [
                        {"domain": "a.test", "status": "queued", "job_id": "j1"},
                        {"domain": "b.test", "status": "queued", "job_id": "j2"},
                    ],
                },
            ),
        )
        wire.on(
            "GET",
            "/api/jobs/j1",
            Answer(200, {"id": "j1", "state": "failed", "error": "probe refused"}),
        )
        wire.on("GET", "/api/jobs/j2", Answer(200, {"id": "j2", "state": "succeeded"}))

        output = self.run("update", "a.test", "b.test", code=1)

        assert "a.test: failed" in output and "probe refused" in output
        assert "b.test: succeeded" in output

    def test_serial_stops_at_the_first_failure(self, wire: Wire) -> None:
        wire.on(
            "GET",
            "/api/apps?project=web",
            Answer(200, {"apps": [{"domain": "a.test"}, {"domain": "b.test"}]}),
        )
        wire.on("POST", "/api/jobs/update", Answer(202, {"job": {"id": "j1"}}))
        wire.on(
            "GET", "/api/jobs/j1", Answer(200, {"id": "j1", "state": "failed", "error": "boom"})
        )
        wire.on(
            "GET", "/api/jobs/j1/log?tail=20000", Answer(200, {"content": "", "truncated": False})
        )

        self.run("update", "--project", "web", "--serial", code=1)

        updates = [s for s in wire.seen if s[0] == "POST"]
        assert [u[3]["domain"] for u in updates] == ["a.test"]

    def test_keep_going_does_the_rest(self, wire: Wire) -> None:
        wire.on(
            "GET",
            "/api/apps?project=web",
            Answer(200, {"apps": [{"domain": "a.test"}, {"domain": "b.test"}]}),
        )
        wire.on(
            "POST",
            "/api/jobs/update",
            Answer(202, {"job": {"id": "j1"}}),
            Answer(202, {"job": {"id": "j2"}}),
        )
        wire.on(
            "GET", "/api/jobs/j1", Answer(200, {"id": "j1", "state": "failed", "error": "boom"})
        )
        wire.on("GET", "/api/jobs/j2", Answer(200, {"id": "j2", "state": "succeeded"}))
        for job in ("j1", "j2"):
            wire.on(
                "GET",
                f"/api/jobs/{job}/log?tail=20000",
                Answer(200, {"content": "", "truncated": False}),
            )

        self.run("update", "--project", "web", "--serial", "--keep-going", code=1)

        updates = [s for s in wire.seen if s[0] == "POST"]
        assert [u[3]["domain"] for u in updates] == ["a.test", "b.test"]

    def test_nothing_named_is_a_usage_error(self, wire: Wire) -> None:
        output = self.run("update", code=2)

        assert "Name an application" in output

    def test_json_is_the_servers_answer_with_the_finished_jobs(self, wire: Wire) -> None:
        wire.on(
            "POST",
            "/api/jobs/update-many",
            Answer(
                202,
                {
                    "message": "Queued 1",
                    "queued": 1,
                    "skipped": 0,
                    "items": [{"domain": "a.test", "status": "queued", "job_id": "j1"}],
                },
            ),
        )
        wire.on("GET", "/api/jobs/j1", Answer(200, {"id": "j1", "state": "succeeded"}))

        body = json.loads(self.run("--json", "update", "--team", "acme"))

        assert body["queued"] == 1 and body["jobs"][0]["state"] == "succeeded"
