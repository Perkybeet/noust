# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances``: the engines Noust runs in containers (checklist C-03, C-04).

The contract: what could not succeed is refused when the request arrives, not in a
failed job; making, upgrading and removing need sudo mode and run as jobs; removing
needs the operator to accept that the data goes, and is refused outright while an
application uses the instance; no response carries the superuser's password; and
an instance, once made, is listed by the engines' own route under its key.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.web.api.databases.common as db_common
from noust.managers.database.settings import Resources
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.database_instance_support import World, build_world, close_world

BODY = {"name": "pg16", "engine": "postgresql", "version": "16", "memory_mb": 512}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    yield build_world(tmp_path, monkeypatch)
    close_world()


@pytest.fixture
def app(tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources", lambda: Resources(8 * 1024**3, 4)
    )
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Stand in for the job manager: what is queued is kept, and run by the test."""
    created: list[dict[str, Any]] = []

    def create_job(**kwargs: Any) -> SimpleNamespace:
        created.append(kwargs)
        return SimpleNamespace(
            id=f"job-{len(created)}",
            status=SimpleNamespace(value="pending"),
            to_dict=lambda: {"id": f"job-{len(created)}", "status": "pending"},
        )

    monkeypatch.setattr(
        db_common, "get_job_manager", lambda: SimpleNamespace(create_job=create_job)
    )
    return created


def sign_in(app: FastAPI, *, elevate: bool) -> TestClient:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    if elevate:
        assert client.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return client


@pytest.fixture
def plain(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=False)


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return sign_in(app, elevate=True)


def run(job: dict[str, Any]) -> dict[str, Any]:
    """Run a queued job the way the job manager would."""
    return job["func"](**job["kwargs"])


class TestWhatCouldNotSucceedIsRefusedWhenTheRequestArrives:
    def test_the_plan_says_everything_and_changes_nothing(
        self,
        plain: TestClient,
        world: World,
    ) -> None:
        response = plain.post("/api/databases/instances/plan", json=BODY)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["key"] == "postgresql@pg16"
        assert body["host_port"] == 5433
        assert body["reference"].startswith("postgres:16@sha256:")
        assert any("127.0.0.1" in warning for warning in body["warnings"])
        assert world.store.list_apps() == [] and not any(world.units.iterdir())

    def test_a_missing_memory_limit_is_a_422_naming_the_field_or_a_400_with_the_fix(
        self, plain: TestClient
    ) -> None:
        response = plain.post(
            "/api/databases/instances/plan",
            json={k: v for k, v in BODY.items() if k != "memory_mb"},
        )

        assert response.status_code in (400, 422)
        assert "memory" in response.text

    def test_a_version_the_catalog_lacks_lists_the_ones_it_has(self, plain: TestClient) -> None:
        response = plain.post("/api/databases/instances/plan", json={**BODY, "version": "9.6"})

        assert response.status_code == 400
        assert "15, 16, 17, 18" in response.text

    def test_a_name_that_cannot_be_a_unit_is_refused(self, plain: TestClient) -> None:
        response = plain.post("/api/databases/instances/plan", json={**BODY, "name": "../etc"})

        assert response.status_code in (400, 422)

    def test_without_docker_it_says_what_to_install(
        self,
        plain: TestClient,
        world: World,
    ) -> None:
        world.host.only_knows("systemctl")

        response = plain.post("/api/databases/instances/plan", json=BODY)

        assert response.status_code == 400
        assert "Docker is not installed" in response.text

    def test_the_plan_of_a_variant_names_the_projects_image_and_its_tag(
        self, plain: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.managers.database.managed.host_architecture", lambda: "amd64")

        response = plain.post(
            "/api/databases/instances/plan", json={**BODY, "name": "vec", "variant": "pgvector"}
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["variant"] == "pgvector" and body["tag"] == "pg16"
        assert body["image"] == "pgvector/pgvector"
        assert body["reference"].startswith("pgvector/pgvector:pg16@sha256:")

    def test_a_variant_the_engine_does_not_have_is_a_400_listing_the_ones_it_has(
        self, plain: TestClient
    ) -> None:
        response = plain.post(
            "/api/databases/instances/plan", json={**BODY, "variant": "timescaledb"}
        )

        assert response.status_code == 400
        assert "pgvector, postgis" in response.text

    def test_an_image_for_another_processor_is_refused_with_the_reason(
        self, plain: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("noust.managers.database.managed.host_architecture", lambda: "arm64")

        response = plain.post("/api/databases/instances/plan", json={**BODY, "variant": "postgis"})

        assert response.status_code == 400
        assert "amd64 only" in response.text

    def test_it_needs_a_session(self, app: FastAPI) -> None:
        anonymous = TestClient(app, client=("testclient", 50001))
        assert anonymous.get("/api/databases/instances").status_code in (401, 403)


class TestMaking:
    def test_it_needs_sudo_mode(self, plain: TestClient, queued: list) -> None:
        response = plain.post("/api/databases/instances", json=BODY)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"
        assert queued == []

    def test_a_request_that_could_not_succeed_queues_nothing(
        self, admin: TestClient, queued: list
    ) -> None:
        response = admin.post("/api/databases/instances", json={**BODY, "memory_mb": 64})

        assert response.status_code == 400
        assert queued == []

    def test_it_is_a_job_that_makes_the_instance_and_returns_no_password(
        self,
        admin: TestClient,
        queued: list,
        world: World,
    ) -> None:
        response = admin.post("/api/databases/instances", json=BODY)

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["kwargs"]["action"] == "create"
        assert job["kwargs"]["engine"] == "postgresql"
        assert job["kwargs"]["memory_mb"] == 512
        result = run(job)
        assert result["instance"]["key"] == "postgresql@pg16"
        assert result["instance"]["variant"] is None
        assert result["instance"]["state"] == "ready"
        password = world.password("pg16")
        assert password not in json.dumps(result)
        assert password not in response.text

    def test_a_variant_is_made_by_the_job_from_the_projects_image(
        self,
        admin: TestClient,
        queued: list,
        world: World,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("noust.managers.database.managed.host_architecture", lambda: "amd64")

        response = admin.post(
            "/api/databases/instances", json={**BODY, "name": "vec", "variant": "pgvector"}
        )

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["kwargs"]["variant"] == "pgvector"
        result = run(job)
        assert result["instance"]["variant"] == "pgvector"
        assert result["instance"]["image"] == "pgvector/pgvector"
        assert result["instance"]["image_tag"] == "pg16"

    def test_scripts_travel_to_the_job_and_are_written_when_it_runs(
        self, admin: TestClient, queued: list, world: World
    ) -> None:
        body = {
            **BODY,
            "init_scripts": [{"name": "schema.sql", "content": "CREATE TABLE t (id int);"}],
        }

        response = admin.post("/api/databases/instances", json=body)

        assert response.status_code == 202, response.text
        (job,) = queued
        assert job["kwargs"]["init_scripts"] == body["init_scripts"]
        result = run(job)
        assert result["instance"]["init_scripts"] == ["schema.sql"]
        from noust.managers.database import instance_layout as layout

        assert (
            layout.init_directory("pg16") / "schema.sql"
        ).read_text() == "CREATE TABLE t (id int);"

    def test_a_script_that_is_not_sql_is_refused_before_a_job_exists(
        self, admin: TestClient, queued: list
    ) -> None:
        body = {**BODY, "init_scripts": [{"name": "setup.sh", "content": "rm -rf /"}]}

        response = admin.post("/api/databases/instances", json=body)

        assert response.status_code == 400
        assert "not the name of an initialisation script" in response.text
        assert queued == []

    def test_the_plan_names_the_scripts(self, plain: TestClient) -> None:
        body = {**BODY, "init_scripts": [{"name": "schema.sql", "content": "SELECT 1;"}]}

        response = plain.post("/api/databases/instances/plan", json=body)

        assert response.status_code == 200, response.text
        assert response.json()["init_scripts"] == ["schema.sql"]

    def test_the_job_records_who_asked(
        self,
        admin: TestClient,
        queued: list,
        world: World,
    ) -> None:
        admin.post("/api/databases/instances", json=BODY)

        (job,) = queued
        assert job["kwargs"]["actor"]


class TestUsingIt:
    @pytest.fixture
    def made(self, admin: TestClient, queued: list, world: World) -> TestClient:
        admin.post("/api/databases/instances", json=BODY)
        run(queued.pop())
        return admin

    def test_it_is_listed_with_the_memory_the_instances_hold(self, made: TestClient) -> None:
        body = made.get("/api/databases/instances").json()

        (instance,) = body["instances"]
        assert instance["key"] == "postgresql@pg16" and instance["running"] is True
        assert instance["address"] == "127.0.0.1"
        assert instance["memory_limit_mb"] == 512
        assert body["budget"]["allocated_mb"] == 512
        assert "password" not in json.dumps(body).lower()

    def test_it_is_one_engine_among_the_others_under_its_key(self, made: TestClient) -> None:
        engines = made.get("/api/databases/engines").json()["engines"]

        (entry,) = [item for item in engines if item["name"] == "postgresql@pg16"]
        assert entry["kind"] == "container"
        assert entry["managed"] == "pg16"
        assert entry["pinned_version"] == "16"
        assert entry["memory_limit_mb"] == 512
        assert entry["engine_type"] == "postgresql"

    def test_its_databases_are_listed_with_the_engines_own_route(self, made: TestClient) -> None:
        body = made.get("/api/databases/databases", params={"engine": "postgresql@pg16"}).json()

        assert [db["name"] for db in body["databases"]] == ["appdb"]
        assert body["databases"][0]["engine"] == "postgresql@pg16"

    def test_one_is_described(self, made: TestClient) -> None:
        body = made.get("/api/databases/instances/pg16").json()

        assert body["name"] == "pg16" and body["status"] == "running"
        assert body["volume"] == "noust-db-pg16-data"
        assert made.get("/api/databases/instances/ghost").status_code == 404

    def test_the_catalog_lists_every_engine_with_digests(self, made: TestClient) -> None:
        body = made.get("/api/databases/instances/catalog").json()

        flavours = {entry["flavour"] for entry in body["engines"]}
        assert flavours == {
            "postgresql",
            "mysql",
            "mariadb",
            "redis",
            "valkey",
            "keydb",
            "mongodb",
            "clickhouse",
        }
        postgres = next(entry for entry in body["engines"] if entry["flavour"] == "postgresql")
        assert all(item["digest"].startswith("sha256:") for item in postgres["versions"])
        assert [variant["name"] for variant in postgres["variants"]] == ["pgvector", "postgis"]
        pgvector = postgres["variants"][0]
        assert pgvector["image"] == "pgvector/pgvector" and pgvector["platforms"] == [
            "amd64",
            "arm64",
        ]
        assert {item["version"]: item["tag"] for item in pgvector["versions"]}["17"] == "pg17"

    def test_stopping_is_immediate_and_starting_is_a_job(
        self,
        made: TestClient,
        queued: list,
        world: World,
    ) -> None:
        stopped = made.post("/api/databases/instances/pg16/stop")
        assert stopped.status_code == 200 and "stopped" in stopped.json()["message"]
        assert made.get("/api/databases/instances/pg16").json()["status"] == "stopped"

        started = made.post("/api/databases/instances/pg16/start")

        assert started.status_code == 202
        result = run(queued[-1])
        assert result["instance"]["running"] is True

    def test_an_upgrade_needs_sudo_mode_and_is_a_job(
        self,
        app: FastAPI,
        made: TestClient,
        queued: list,
        world: World,
    ) -> None:
        plain = sign_in(app, elevate=False)
        assert plain.post("/api/databases/instances/pg16/upgrade").status_code == 403

        accepted = made.post("/api/databases/instances/pg16/upgrade", params={"backup": "false"})

        assert accepted.status_code == 202
        job = queued[-1]
        assert job["kwargs"] == {
            "action": "upgrade",
            "name": "pg16",
            "backup": False,
            "actor": job["kwargs"]["actor"],
        }

    def test_a_removal_says_what_it_deletes_and_deletes_nothing_without_confirm(
        self, made: TestClient, queued: list
    ) -> None:
        plan = made.get("/api/databases/instances/pg16/remove-plan").json()
        assert plan["databases"] == ["appdb"] and plan["blockers"] == []

        refused = made.delete("/api/databases/instances/pg16")

        assert refused.status_code in (400, 409)
        assert "no way back" in refused.text
        assert queued == []

    def test_a_confirmed_removal_is_a_job_that_removes_everything(
        self,
        made: TestClient,
        queued: list,
        world: World,
    ) -> None:
        accepted = made.delete("/api/databases/instances/pg16", params={"confirm": "true"})

        assert accepted.status_code == 202
        result = run(queued[-1])
        assert result["removed"]["name"] == "pg16"
        assert world.host.volumes == set()
        assert made.get("/api/databases/instances").json()["instances"] == []

    def test_an_application_that_uses_it_stops_the_removal_before_a_job_is_queued(
        self,
        made: TestClient,
        queued: list,
        world: World,
    ) -> None:
        from noust.core.store import App
        from noust.managers.database.records import DatabaseLink

        app = world.store.create_app(App(domain="shop.example.com", app_path="/srv/shop"))
        world.service.records.save_link(
            DatabaseLink(
                app_id=app.id or 0,
                engine="postgresql@pg16",
                db_name="appdb",
                username="app",
                env_var="DATABASE_URL",
            )
        )

        refused = made.delete("/api/databases/instances/pg16", params={"confirm": "true"})

        assert refused.status_code == 400
        assert "shop.example.com" in refused.text
        assert queued == []
