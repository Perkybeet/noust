# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/instances`` for a version without support and for an image pinned at creation.

What the new Databases screen draws from: the catalog says which versions are unsupported, since
when and for which processors; the plan says it before anything is queued; the request that
makes one carries ``unsupported_ok`` or is refused with the date and a code the console
translates; and an image given by registry and tag is accepted and pinned when it is made.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import noust.web.api.databases.common as db_common
from noust.managers.database import managed as managed_module
from noust.managers.database.settings import Resources
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.database_instance_support import World, build_world, close_world

OLD = {"name": "old", "engine": "mysql", "version": "5.7", "memory_mb": 1024}
DIGEST = "sha256:" + "ab" * 32


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield built
    close_world()


@pytest.fixture
def app(tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources", lambda: Resources(8 * 1024**3, 4)
    )
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


@pytest.fixture
def queued(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
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


class TestTheCatalog:
    def test_it_lists_the_versions_without_support_with_their_dates_and_processors(
        self, plain: TestClient
    ) -> None:
        response = plain.get("/api/databases/instances/catalog")

        assert response.status_code == 200, response.text
        engines = {entry["flavour"]: entry for entry in response.json()["engines"]}
        versions = {item["version"]: item for item in engines["mysql"]["versions"]}
        assert versions["5.7"]["unsupported"] is True
        assert versions["5.7"]["support"] == "ended"
        assert versions["5.7"]["end_of_life"] == "2023-10-31"
        assert versions["5.7"]["platforms"] == ["amd64"]
        assert versions["8.4"]["unsupported"] is False
        assert versions["8.4"]["default"] is True

    def test_once_the_engine_is_chosen_it_lists_only_that_one(self, plain: TestClient) -> None:
        response = plain.get("/api/databases/instances/catalog", params={"engine": "mariadb"})

        assert response.status_code == 200, response.text
        assert [entry["flavour"] for entry in response.json()["engines"]] == ["mariadb"]

    def test_an_alias_names_the_engine_too(self, plain: TestClient) -> None:
        response = plain.get("/api/databases/instances/catalog", params={"engine": "pg"})

        assert [entry["flavour"] for entry in response.json()["engines"]] == ["postgresql"]

    def test_an_engine_nobody_runs_as_a_container_is_a_400_that_lists_the_ones_that_run(
        self, plain: TestClient
    ) -> None:
        response = plain.get("/api/databases/instances/catalog", params={"engine": "oracle"})

        assert response.status_code == 400
        assert "mysql" in response.json()["hint"]


class TestThePlan:
    def test_it_says_the_date_and_that_the_confirmation_is_needed(self, plain: TestClient) -> None:
        response = plain.post("/api/databases/instances/plan", json=OLD)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["unsupported"] is True and body["requires_unsupported_ok"] is True
        assert (body["support"], body["end_of_life"]) == ("ended", "2023-10-31")
        assert body["unsupported_coded"] == {
            "code": "database_servers.unsupported_ended",
            "params": {"name": "MySQL", "version": "5.7", "end_of_life": "2023-10-31"},
        }
        assert any("2023-10-31" in warning for warning in body["warnings"])

    def test_a_current_version_needs_nothing(self, plain: TestClient) -> None:
        body = plain.post("/api/databases/instances/plan", json={**OLD, "version": "8.4"}).json()

        assert body["unsupported"] is False and body["unsupported_coded"] is None

    def test_an_image_given_by_its_tag_is_planned_with_the_digest_still_to_be_read(
        self, plain: TestClient
    ) -> None:
        response = plain.post(
            "/api/databases/instances/plan",
            json={
                "name": "own",
                "engine": "postgresql",
                "version": "17",
                "memory_mb": 1024,
                "image": "ghcr.io/acme/pg:17.2",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["digest_pending"] is True and body["digest"] == ""
        assert body["reference"] == "ghcr.io/acme/pg:17.2"
        assert body["requires_confirmation"] is True

    def test_an_image_without_a_tag_is_a_400_naming_the_field(self, plain: TestClient) -> None:
        response = plain.post(
            "/api/databases/instances/plan",
            json={
                "name": "own",
                "engine": "postgresql",
                "version": "17",
                "memory_mb": 1024,
                "image": "ghcr.io/acme/pg",
            },
        )

        assert response.status_code == 400
        assert "image" in response.json()["fields"]


class TestMakingOne:
    def test_without_the_confirmation_nothing_is_queued_and_the_answer_has_a_code(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post("/api/databases/instances", json=OLD)

        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "confirmation_required"
        assert body["fields"] == {"unsupported_ok": body["detail"]}
        assert body["coded"]["code"] == "database_servers.unsupported_ended"
        assert body["coded"]["params"]["end_of_life"] == "2023-10-31"
        assert "2023-10-31" in body["detail"]
        assert body["warnings"] == [body["detail"]]
        assert queued == []

    def test_with_it_the_job_is_queued_and_carries_it(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post("/api/databases/instances", json={**OLD, "unsupported_ok": True})

        assert response.status_code == 202, response.text
        [job] = queued
        assert job["kwargs"]["unsupported_ok"] is True
        assert job["kwargs"]["version"] == "5.7"

    def test_the_job_makes_the_instance_on_the_digest_the_catalog_pins(
        self, admin: TestClient, queued: list[dict[str, Any]], world: World
    ) -> None:
        admin.post("/api/databases/instances", json={**OLD, "unsupported_ok": True})

        queued[0]["func"](**queued[0]["kwargs"])

        [described] = world.managed.all()
        assert described["key"] == "mysql@old"
        assert described["support"] == "ended" and described["end_of_life"] == "2023-10-31"
        listed = admin.get("/api/databases/instances").json()["instances"]
        assert [item["name"] for item in listed] == ["old"]

    def test_an_image_by_its_tag_is_queued_and_pinned_by_the_job(
        self, admin: TestClient, queued: list[dict[str, Any]], world: World
    ) -> None:
        world.host.served["ghcr.io/acme/pg:17.2"] = DIGEST
        body = {
            "name": "own",
            "engine": "postgresql",
            "version": "17",
            "memory_mb": 1024,
            "image": "ghcr.io/acme/pg:17.2",
            "accept_untested": True,
        }

        response = admin.post("/api/databases/instances", json=body)
        assert response.status_code == 202, response.text
        queued[0]["func"](**queued[0]["kwargs"])

        [described] = world.managed.all()
        assert described["digest"] == DIGEST
        assert described["reference"] == f"ghcr.io/acme/pg:17.2@{DIGEST}"

    def test_the_two_confirmations_are_asked_one_at_a_time(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        body = {**OLD, "image": f"ghcr.io/acme/mysql:5.7@{DIGEST}"}

        first = admin.post("/api/databases/instances", json=body)
        second = admin.post("/api/databases/instances", json={**body, "unsupported_ok": True})

        assert first.json()["fields"].keys() == {"unsupported_ok"}
        assert second.json()["fields"].keys() == {"accept_untested"}
        assert queued == []
