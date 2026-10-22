# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tags on databases and services, and restarting what carries one, over HTTP (checklist M-07).

Databases are reached through the two-team console, because who may see one is the point: a team
tags and filters its own and another team's are a 404. Services are the instance's, so a member of
a team never reaches them. The restart by tag is the one place the three kinds meet; it reports
what it did, what it left and why, and can be asked what it would do first.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import DatabaseNotFoundError, ServiceError
from noust.core.resource_tags import KIND_SERVICE, ResourceTags
from noust.web.api import app_tags
from noust.web.api import services as services_api
from tests.teams_support import World, build_world
from tests.test_web_databases_api import make_engine, wire
from tests.test_web_services_api import FakeServiceManager

ACME_APP = "shop.acme.example"
ACME_DB = "shop_db"
GLOBEX_DB = "blog_db"
GLOBEX_APP = "blog.globex.example"
DB = "/api/databases/databases/postgresql"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> World:
    """The two-team console with a PostgreSQL holding each team's database and a loose one."""
    built = build_world(tmp_path, monkeypatch)
    engine = make_engine(tmp_path / "pg", seeded=False)
    engine.dbs = {name: {"owner": None, "size": None} for name in (ACME_DB, GLOBEX_DB, "loose")}
    describe = engine.get_database_info

    def get_database_info(self: Any, name: str) -> Any:
        # What a real engine says of a database it does not have.
        if name not in type(self).dbs:
            raise DatabaseNotFoundError(f"No database named {name}")
        return describe(self, name)

    engine.get_database_info = get_database_info  # type: ignore[method-assign]
    wire(monkeypatch, [engine])
    return built


@pytest.fixture
def units(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A unit directory with two units in it, and the services API pointed at it."""
    directory = tmp_path / "units"
    directory.mkdir()
    for name in ("worker", "mailer"):
        (directory / f"{name}.service").write_text("[Service]\nExecStart=/bin/true\n")
    monkeypatch.setattr(services_api, "SYSTEMD_UNIT_DIR", directory, raising=False)
    FakeServiceManager.instances = []
    FakeServiceManager.statuses = {}
    FakeServiceManager.all_units = [{"name": "worker"}, {"name": "mailer"}]
    monkeypatch.setattr(services_api, "ServiceManager", FakeServiceManager, raising=False)
    return directory


def put(world: World, path: str, who: str, tags: list[str]) -> Any:
    return world.client.put(path, json={"tags": tags}, headers=world.as_(who))


class TestDatabaseTags:
    def test_a_database_is_tagged_listed_by_its_tag_and_filtered(self, world: World) -> None:
        tagged = put(world, f"{DB}/{ACME_DB}/tags", "admin", ["Prod", "eu"])
        read = world.client.get(f"{DB}/{ACME_DB}/tags", headers=world.as_("admin"))
        listing = world.client.get("/api/databases/databases", headers=world.as_("admin"))
        filtered = world.client.get("/api/databases/databases?tag=prod", headers=world.as_("admin"))

        assert tagged.status_code == 200, tagged.text
        assert tagged.json()["tags"] == ["eu", "prod"]
        assert read.json() == {
            "engine": "postgresql",
            "database": ACME_DB,
            "tags": ["eu", "prod"],
            "max_tags": 16,
        }
        by_name = {row["name"]: row["tags"] for row in listing.json()["databases"]}
        assert by_name == {ACME_DB: ["eu", "prod"], GLOBEX_DB: [], "loose": []}
        assert [row["name"] for row in filtered.json()["databases"]] == [ACME_DB]
        assert filtered.json()["total"] == 1

    def test_the_detail_of_a_database_carries_its_tags(self, world: World) -> None:
        put(world, f"{DB}/{ACME_DB}/tags", "admin", ["prod"])

        detail = world.client.get(f"{DB}/{ACME_DB}", headers=world.as_("admin"))

        assert detail.json()["tags"] == ["prod"]

    def test_a_team_tags_its_own_databases_and_the_other_teams_are_not_there(
        self, world: World
    ) -> None:
        mine = put(world, f"{DB}/{ACME_DB}/tags", "alice", ["prod"])
        theirs_write = put(world, f"{DB}/{GLOBEX_DB}/tags", "alice", ["prod"])
        theirs_read = world.client.get(f"{DB}/{GLOBEX_DB}/tags", headers=world.as_("alice"))

        assert mine.status_code == 200, mine.text
        assert theirs_write.status_code == 404
        assert theirs_read.status_code == 404
        assert ResourceTags(world.store).by_resource("database") == {"postgresql/shop_db": ["prod"]}

    def test_a_filter_covers_only_the_callers_databases(self, world: World) -> None:
        put(world, f"{DB}/{ACME_DB}/tags", "admin", ["prod"])
        put(world, f"{DB}/{GLOBEX_DB}/tags", "admin", ["prod"])

        alice = world.client.get("/api/databases/databases?tag=prod", headers=world.as_("alice"))
        bob = world.client.get("/api/databases/databases?tag=prod", headers=world.as_("bob"))

        assert [row["name"] for row in alice.json()["databases"]] == [ACME_DB]
        assert [row["name"] for row in bob.json()["databases"]] == [GLOBEX_DB]

    def test_a_viewer_reads_tags_and_cannot_change_them(self, world: World) -> None:
        put(world, f"{DB}/{ACME_DB}/tags", "admin", ["prod"])

        read = world.client.get(f"{DB}/{ACME_DB}/tags", headers=world.as_("dana"))
        write = put(world, f"{DB}/{ACME_DB}/tags", "dana", ["x"])

        assert read.json()["tags"] == ["prod"]
        assert write.status_code == 403
        assert ResourceTags(world.store).of("database", "postgresql/shop_db") == ["prod"]

    def test_a_database_that_is_not_there_is_not_tagged(self, world: World) -> None:
        response = put(world, f"{DB}/ghost/tags", "admin", ["prod"])

        assert response.status_code == 404
        assert ResourceTags(world.store).by_resource("database") == {}

    def test_a_bad_tag_or_too_many_is_a_400_and_changes_nothing(self, world: World) -> None:
        put(world, f"{DB}/{ACME_DB}/tags", "admin", ["prod"])

        bad = put(world, f"{DB}/{ACME_DB}/tags", "admin", ["not a tag"])
        many = put(world, f"{DB}/{ACME_DB}/tags", "admin", [f"t{i}" for i in range(17)])

        assert bad.status_code == 400 and many.status_code == 400
        assert ResourceTags(world.store).of("database", "postgresql/shop_db") == ["prod"]

    def test_a_bad_tag_in_the_filter_is_a_400(self, world: World) -> None:
        response = world.client.get(
            "/api/databases/databases?tag=no%20tag", headers=world.as_("admin")
        )

        assert response.status_code == 400


class TestServiceTags:
    def test_a_service_is_tagged_listed_by_its_tag_and_filtered(
        self, world: World, units: Path
    ) -> None:
        tagged = put(world, "/api/services/worker/tags", "admin", ["Prod"])
        read = world.client.get("/api/services/worker/tags", headers=world.as_("admin"))
        listing = world.client.get("/api/services", headers=world.as_("admin"))
        filtered = world.client.get("/api/services?tag=prod", headers=world.as_("admin"))

        assert tagged.status_code == 200, tagged.text
        assert read.json() == {"service": "worker", "tags": ["prod"], "max_tags": 16}
        by_name = {row["name"]: row["tags"] for row in listing.json()["services"]}
        assert by_name == {"worker": ["prod"], "mailer": []}
        assert [row["name"] for row in filtered.json()["services"]] == ["worker"]
        assert filtered.json()["total"] == 1

    def test_a_unit_that_does_not_exist_is_not_tagged(self, world: World, units: Path) -> None:
        response = put(world, "/api/services/ghost/tags", "admin", ["prod"])

        assert response.status_code == 404
        assert ResourceTags(world.store).by_resource(KIND_SERVICE) == {}

    @pytest.mark.parametrize("name", ["..%2f..%2fevil", "evil%20name", "evil%0Aname"])
    def test_a_name_that_leaves_the_unit_directory_is_refused(
        self, world: World, units: Path, name: str
    ) -> None:
        response = put(world, f"/api/services/{name}/tags", "admin", ["prod"])

        assert response.status_code in (400, 404, 422)
        assert ResourceTags(world.store).by_resource(KIND_SERVICE) == {}

    def test_the_servers_units_are_not_a_teams(self, world: World, units: Path) -> None:
        for who, method in (("alice", "get"), ("alice", "put"), ("bob", "get")):
            response = world.client.request(
                method.upper(),
                "/api/services/worker/tags",
                headers=world.as_(who),
                json={"tags": ["x"]} if method == "put" else None,
            )
            assert response.status_code == 403, (who, method, response.text)
        assert ResourceTags(world.store).by_resource(KIND_SERVICE) == {}

    def test_a_viewer_of_the_instance_reads_and_cannot_change(
        self, world: World, units: Path
    ) -> None:
        put(world, "/api/services/worker/tags", "admin", ["prod"])
        auditor = world.as_("aud")

        read = world.client.get("/api/services/worker/tags", headers=auditor)
        write = world.client.put("/api/services/worker/tags", json={"tags": []}, headers=auditor)

        assert read.status_code == 200, read.text
        assert write.status_code == 403

    def test_a_bad_tag_in_the_filter_is_a_400(self, world: World, units: Path) -> None:
        response = world.client.get("/api/services?tag=no%20tag", headers=world.as_("admin"))

        assert response.status_code == 400


class Recorder:
    """Stands in for the two restarts, to see what the route asked for."""

    def __init__(self) -> None:
        self.apps: list[str] = []
        self.units: list[str] = []
        self.fail: set[str] = set()

    def application(self, domain: str) -> None:
        if domain in self.fail:
            raise ServiceError(f"{domain} would not restart", details="systemd said no")
        self.apps.append(domain)

    def unit(self, name: str) -> None:
        if name in self.fail:
            raise ServiceError(f"{name} would not restart", details="systemd said no")
        self.units.append(name)


@pytest.fixture
def restarts(monkeypatch: pytest.MonkeyPatch, units: Path) -> Recorder:
    recorder = Recorder()
    monkeypatch.setattr(app_tags, "restart_application", recorder.application)
    monkeypatch.setattr(app_tags, "restart_unit", recorder.unit)
    return recorder


def tagged_everywhere(world: World) -> None:
    """The production tag on both teams' applications and databases, and on a unit."""
    from noust.core.app_tags import AppTags

    tags = AppTags(world.store)
    tags.set(ACME_APP, ["prod"])
    tags.set(GLOBEX_APP, ["prod", "eu"])
    resource = ResourceTags(world.store)
    resource.set("database", f"postgresql/{ACME_DB}", ["prod"])
    resource.set("database", f"postgresql/{GLOBEX_DB}", ["prod"])
    resource.set("service", "worker", ["prod"])


def outcomes(response: Any) -> dict[tuple[str, str], str]:
    return {(row["kind"], row["name"]): row["outcome"] for row in response.json()["results"]}


class TestRestartingATag:
    def test_the_instance_restarts_the_applications_and_the_units_and_says_what_it_left(
        self, world: World, restarts: Recorder
    ) -> None:
        tagged_everywhere(world)

        response = world.client.post("/api/tags/prod/restart", json={}, headers=world.as_("admin"))

        assert response.status_code == 200, response.text
        assert outcomes(response) == {
            ("application", ACME_APP): "restarted",
            ("application", GLOBEX_APP): "restarted",
            ("service", "worker"): "restarted",
            ("database", f"postgresql/{ACME_DB}"): "skipped",
            ("database", f"postgresql/{GLOBEX_DB}"): "skipped",
        }
        assert sorted(restarts.apps) == sorted([ACME_APP, GLOBEX_APP])
        assert restarts.units == ["worker"]
        assert response.json()["restarted"] == 3
        skipped = [row for row in response.json()["results"] if row["kind"] == "database"]
        assert all("engine" in row["detail"] for row in skipped)

    def test_asking_first_restarts_nothing(self, world: World, restarts: Recorder) -> None:
        tagged_everywhere(world)

        response = world.client.post(
            "/api/tags/prod/restart", json={"dry_run": True}, headers=world.as_("admin")
        )

        assert response.status_code == 200, response.text
        assert response.json()["dry_run"] is True
        assert response.json()["restarted"] == 0
        assert outcomes(response)[("application", ACME_APP)] == "would_restart"
        assert outcomes(response)[("service", "worker")] == "would_restart"
        assert restarts.apps == [] and restarts.units == []

    def test_a_team_reaches_its_own_applications_and_nothing_else(
        self, world: World, restarts: Recorder
    ) -> None:
        tagged_everywhere(world)

        response = world.client.post("/api/tags/prod/restart", json={}, headers=world.as_("alice"))

        assert response.status_code == 200, response.text
        assert outcomes(response) == {
            ("application", ACME_APP): "restarted",
            ("database", f"postgresql/{ACME_DB}"): "skipped",
        }
        assert restarts.apps == [ACME_APP] and restarts.units == []
        assert GLOBEX_APP not in response.text and "worker" not in response.text

    def test_a_tag_only_another_team_carries_is_a_404_for_this_one(
        self, world: World, restarts: Recorder
    ) -> None:
        tagged_everywhere(world)

        response = world.client.post("/api/tags/eu/restart", json={}, headers=world.as_("alice"))

        assert response.status_code == 404
        assert restarts.apps == []

    def test_an_operator_may_and_a_viewer_may_not(self, world: World, restarts: Recorder) -> None:
        tagged_everywhere(world)

        operator = world.client.post("/api/tags/prod/restart", json={}, headers=world.as_("omar"))
        viewer = world.client.post("/api/tags/prod/restart", json={}, headers=world.as_("dana"))

        assert operator.status_code == 200, operator.text
        assert viewer.status_code == 403
        assert restarts.apps == [ACME_APP]

    def test_one_that_fails_does_not_stop_the_others_and_says_why(
        self, world: World, restarts: Recorder
    ) -> None:
        tagged_everywhere(world)
        restarts.fail = {ACME_APP}

        response = world.client.post("/api/tags/prod/restart", json={}, headers=world.as_("admin"))

        assert response.status_code == 200, response.text
        found = {(row["kind"], row["name"]): row for row in response.json()["results"]}
        assert found[("application", ACME_APP)]["outcome"] == "failed"
        assert "would not restart" in found[("application", ACME_APP)]["detail"]
        assert found[("application", GLOBEX_APP)]["outcome"] == "restarted"
        assert restarts.units == ["worker"]
        assert response.json()["restarted"] == 2

    def test_noust_s_own_unit_is_left_alone_with_the_reason(
        self, world: World, monkeypatch: pytest.MonkeyPatch, units: Path
    ) -> None:
        # The real refusal of the services API, not a stand-in: the console and the monitor are
        # units a tag must not be able to restart.
        restarted: list[str] = []
        monkeypatch.setattr(app_tags, "restart_application", restarted.append)
        (units / "noust-web.service").write_text("[Service]\nExecStart=/bin/true\n")
        ResourceTags(world.store).set("service", "noust-web", ["prod"])
        from noust.core.app_tags import AppTags

        AppTags(world.store).set(ACME_APP, ["prod"])

        response = world.client.post("/api/tags/prod/restart", json={}, headers=world.as_("admin"))

        found = {(row["kind"], row["name"]): row for row in response.json()["results"]}
        assert found[("service", "noust-web")]["outcome"] == "skipped"
        assert "own units" in found[("service", "noust-web")]["detail"]
        assert found[("application", ACME_APP)]["outcome"] == "restarted"
        assert restarted == [ACME_APP]

    def test_a_bad_tag_is_a_400_and_a_tag_nobody_carries_a_404(
        self, world: World, restarts: Recorder
    ) -> None:
        bad = world.client.post("/api/tags/no%20tag/restart", json={}, headers=world.as_("admin"))
        nobody = world.client.post("/api/tags/ghost/restart", json={}, headers=world.as_("admin"))

        assert bad.status_code == 400
        assert nobody.status_code == 404

    def test_it_is_a_call_a_pipeline_can_make_with_a_token_that_may_operate(
        self, world: World, restarts: Recorder
    ) -> None:
        tagged_everywhere(world)

        response = world.client.post("/api/tags/prod/restart", headers=world.as_("omar"))

        assert response.status_code == 200, response.text
        assert restarts.apps == [ACME_APP]
