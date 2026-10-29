# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/databases/servers``: the model the new Databases screen draws (4.1, item 103).

The list joins the system's engines and the containers Noust runs into one answer, with each
engine's default and when its support ends. What is pinned over HTTP: the shape the console
reads, that choosing a default asks for the right permission and survives a request, that every
refusal carries a code the console translates, and that a member of one team counts only the
databases of its own teams.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.managers.database import managed as managed_module
from noust.managers.database.settings import Resources
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.database_instance_support import World, build_world, close_world


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    # A server with Docker and none of the engines' own clients: nothing is installed on the system.
    built.host.only_knows("docker")
    yield built
    close_world()


@pytest.fixture
def app(tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources", lambda: Resources(8 * 1024**3, 4)
    )
    return create_app(SecurityConfig(state_dir=tmp_path / "state", rate_limit_requests=5000))


def sign_in(app: FastAPI) -> TestClient:
    client = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = client.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    client.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    return client


@pytest.fixture
def admin(app: FastAPI) -> TestClient:
    return sign_in(app)


def make(world: World, name: str, engine: str, version: str, **kwargs: Any) -> None:
    world.managed.create(name, engine, version=version, memory="1g", **kwargs)


class TestTheList:
    def test_it_is_the_containers_noust_runs_with_what_the_screen_draws(
        self, admin: TestClient, world: World
    ) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "old", "mysql", "8.0", unsupported_ok=True)

        response = admin.get("/api/databases/servers")

        assert response.status_code == 200, response.text
        body = response.json()
        assert [s["key"] for s in body["servers"]] == ["mysql@old", "postgresql@pg16"]
        pg = next(s for s in body["servers"] if s["key"] == "postgresql@pg16")
        assert pg["runs_in"] == "noust_container" and pg["name"] == "pg16"
        assert (pg["flavour"], pg["engine"], pg["display_name"]) == (
            "postgresql",
            "postgresql",
            "PostgreSQL",
        )
        assert pg["version"] == "16" and pg["support"] == "supported"
        assert pg["end_of_life"] == "2028-11-09"
        assert pg["state"] == "running" and pg["running"] is True
        assert pg["port"] == 5433 and pg["memory_limit_mb"] == 1024
        assert pg["image"] == "postgres" and pg["digest"].startswith("sha256:")
        assert pg["default"] is True and pg["default_source"] == "only"
        assert pg["databases"] == 1
        assert {"stop", "other_version", "upgrade", "remove"} <= set(pg["actions"])
        assert body["problems"] == []

    def test_an_older_version_says_it_has_no_support(self, admin: TestClient, world: World) -> None:
        make(world, "old", "mysql", "8.0", unsupported_ok=True)

        [old] = admin.get("/api/databases/servers").json()["servers"]

        assert (old["support"], old["end_of_life"]) == ("ended", "2026-04-30")

    def test_one_engine_can_be_asked_for(self, admin: TestClient, world: World) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "old", "mysql", "8.0", unsupported_ok=True)

        body = admin.get("/api/databases/servers", params={"engine": "mysql"}).json()

        assert [s["key"] for s in body["servers"]] == ["mysql@old"]

    def test_a_server_has_its_own_route(self, admin: TestClient, world: World) -> None:
        make(world, "pg16", "postgresql", "16")

        response = admin.get("/api/databases/servers/postgresql@pg16")

        assert response.status_code == 200, response.text
        assert response.json()["key"] == "postgresql@pg16"

    def test_an_unknown_one_is_a_404_with_a_code(self, admin: TestClient) -> None:
        response = admin.get("/api/databases/servers/postgresql@nothing")

        assert response.status_code == 404
        assert response.json()["coded"] == {
            "code": "database_servers.unknown",
            "params": {"key": "postgresql@nothing", "command": "noust db servers"},
        }

    def test_a_key_that_is_not_one_is_a_400_naming_the_field(self, admin: TestClient) -> None:
        response = admin.get("/api/databases/servers/mysql@a;b")

        assert response.status_code == 400
        assert "server" in response.json()["fields"]


class TestTheDefault:
    def test_choosing_it_is_kept_and_listed(self, admin: TestClient, world: World) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "pg17", "postgresql", "17")
        before = admin.get("/api/databases/servers/postgresql@pg16").json()
        assert before["default"] is True and before["default_source"] == "oldest"

        response = admin.put("/api/databases/servers/postgresql@pg17/default")

        assert response.status_code == 200, response.text
        assert (response.json()["default"], response.json()["default_source"]) == (True, "chosen")
        listed = {s["key"]: s for s in admin.get("/api/databases/servers").json()["servers"]}
        assert listed["postgresql@pg16"]["default"] is False
        assert listed["postgresql@pg17"]["default"] is True
        assert "clear_default" in listed["postgresql@pg17"]["actions"]

    def test_taking_it_back_goes_to_the_automatic_one(
        self, admin: TestClient, world: World
    ) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "pg17", "postgresql", "17")
        admin.put("/api/databases/servers/postgresql@pg17/default")

        response = admin.delete("/api/databases/servers/postgresql@pg17/default")

        assert response.status_code == 200, response.text
        assert (response.json()["default"], response.json()["default_source"]) == (False, None)
        first = admin.get("/api/databases/servers/postgresql@pg16").json()
        assert first["default"] is True and first["default_source"] == "oldest"

    def test_a_database_made_by_the_bare_engine_goes_to_the_default_server(
        self, admin: TestClient, world: World
    ) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "pg17", "postgresql", "17")
        admin.put("/api/databases/servers/postgresql@pg17/default")

        response = admin.post(
            "/api/databases/databases", json={"name": "shop", "engine": "postgresql"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["engine"] == "postgresql@pg17"

    def test_naming_a_server_by_its_key_is_not_overridden(
        self, admin: TestClient, world: World
    ) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "pg17", "postgresql", "17")
        admin.put("/api/databases/servers/postgresql@pg17/default")

        response = admin.post(
            "/api/databases/databases", json={"name": "shop", "engine": "postgresql@pg16"}
        )

        assert response.status_code == 200, response.text
        assert response.json()["engine"] == "postgresql@pg16"

    def test_an_unknown_server_cannot_be_chosen(self, admin: TestClient) -> None:
        response = admin.put("/api/databases/servers/postgresql@nothing/default")

        assert response.status_code == 404
        assert response.json()["coded"]["code"] == "database_servers.unknown"

    def test_removing_the_chosen_server_forgets_the_choice(
        self, admin: TestClient, world: World
    ) -> None:
        make(world, "pg16", "postgresql", "16")
        make(world, "pg17", "postgresql", "17")
        admin.put("/api/databases/servers/postgresql@pg17/default")

        world.managed.remove("pg17", confirm=True)

        from noust.core.db_server_defaults import DbServerDefaultStore

        assert DbServerDefaultStore(world.store).all() == {}
        remaining = admin.get("/api/databases/servers").json()["servers"]
        assert [(s["key"], s["default_source"]) for s in remaining] == [("postgresql@pg16", "only")]


class TestWhoMayDoWhat:
    def test_every_route_declares_its_permission_and_its_scope(self) -> None:
        from noust.web.permissions.registry import route_map
        from noust.web.permissions.scopes import rule_for

        for method, path in (
            ("GET", "/api/databases/servers"),
            ("GET", "/api/databases/servers/{key}"),
            ("PUT", "/api/databases/servers/{key}/default"),
            ("DELETE", "/api/databases/servers/{key}/default"),
        ):
            assert (method, path) in route_map(), (method, path)
            assert rule_for(method, path) is not None, (method, path)

    def test_reading_is_a_database_read_and_choosing_is_managing(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.registry import route_map

        assert route_map()[("GET", "/api/databases/servers")] == Permission.DATABASES_READ
        assert (
            route_map()[("PUT", "/api/databases/servers/{key}/default")]
            == Permission.DATABASES_MANAGE
        )
        assert (
            route_map()[("DELETE", "/api/databases/servers/{key}/default")]
            == Permission.DATABASES_MANAGE
        )

    def test_choosing_is_the_servers_not_a_teams(self) -> None:
        from noust.web.permissions.scopes import INSTANCE, SCOPED, rule_for

        read = rule_for("GET", "/api/databases/servers")
        choose = rule_for("PUT", "/api/databases/servers/{key}/default")
        assert read is not None and read.scoping == SCOPED
        assert choose is not None and choose.scoping == INSTANCE
