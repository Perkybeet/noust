# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The databases area on a machine that only has Docker (items 187 and 188).

The central of a NAS runs the engines it makes in containers (``database_instances``) and has none of the system's
own: no PostgreSQL, no package manager to install one with. The table of functions says that, and the area used to
answer it with a 409 on every listing but the instances', so the console had nothing to draw and did not offer
Databases there. Here the reads answer with what there is (the instances, their databases and their users), and say
why there is no engine of the system in a sentence with a code, and what is made in a container is made, listed and
read from end to end against the double of the machine.
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
from noust.core.config import Config
from noust.managers.database.settings import Resources
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.server import create_app, get_token_manager
from tests.support.database_instance_support import World, build_world, close_world

ARCHIVE = "PGDMP\x01\x0e\x00 a custom archive\n"
LISTING = "; Archive created\n1262; 1262 16384 DATABASE - shop postgres\n"
BODY = {"name": "pg16", "engine": "postgresql", "version": "16", "memory_mb": 512}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setenv("NOUST_CENTRAL_ROLE", "hub")
    monkeypatch.setenv("NOUST_DEPLOY_HERE", "containers")
    Config.reset_instance()
    yield build_world(tmp_path, monkeypatch)
    close_world()
    Config.reset_instance()


@pytest.fixture(autouse=True)
def the_machine_of_a_nas(monkeypatch: pytest.MonkeyPatch) -> None:
    """Docker and nothing else: what the table of functions reads of the machine."""
    from noust.central.machine import MachineFacts

    monkeypatch.setattr(
        "noust.central.machine.read_machine_facts",
        lambda *a, **k: MachineFacts(
            systemd=False, web_server=False, docker=True, root=False, journal=False
        ),
    )


@pytest.fixture(autouse=True)
def where_the_dumps_go(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from noust.managers.database.postgres import PostgresManager

    monkeypatch.setattr(PostgresManager, "BACKUP_DIR", tmp_path / "dumps")


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


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    found = TestClient(app, client=("testclient", 50000), follow_redirects=False)
    token = get_token_manager().generate_master_token()
    login = found.post("/api/auth/login", json={"token": token})
    assert login.status_code == 200, login.text
    found.headers[CSRF_HEADER_NAME] = login.json()["csrf_token"]
    assert found.post("/api/auth/elevate", json={"token": token}).status_code == 200
    return found


def run(job: dict[str, Any]) -> dict[str, Any]:
    return job["func"](**job["kwargs"])


READS = ("/api/databases/servers", "/api/databases/databases", "/api/databases/engines")


class TestTheReadsAnswerWithWhatThereIs:
    @pytest.mark.parametrize("path", READS)
    def test_a_machine_with_no_instance_answers_an_empty_list_and_not_a_409(
        self, client: TestClient, path: str
    ) -> None:
        response = client.get(path)

        assert response.status_code == 200, response.text
        body = response.json()
        assert (
            body[
                {"servers": "servers", "databases": "databases", "engines": "engines"}[
                    path.rsplit("/", 1)[1]
                ]
            ]
            == []
        )

    @pytest.mark.parametrize("path", READS)
    def test_it_says_why_there_is_no_engine_of_the_system_with_the_tables_own_sentence(
        self, client: TestClient, path: str
    ) -> None:
        system = client.get(path).json()["system"]

        assert system["available"] is False
        assert system["reason"]["code"] == "features.containers_only"
        assert system["reason"]["text"]

    def test_an_instance_is_listed_by_every_read_with_its_databases(
        self, client: TestClient, world: World
    ) -> None:
        world.managed.create("pg16", "postgresql", version="16", memory="1g")

        servers = client.get("/api/databases/servers").json()
        engines = client.get("/api/databases/engines").json()
        databases = client.get("/api/databases/databases").json()

        assert [s["key"] for s in servers["servers"]] == ["postgresql@pg16"]
        assert servers["servers"][0]["runs_in"] == "noust_container"
        assert [e["name"] for e in engines["engines"]] == ["postgresql@pg16"]
        assert [d["name"] for d in databases["databases"]] == ["appdb"]
        assert databases["databases"][0]["engine"] == "postgresql@pg16"
        assert client.get("/api/databases/instances").status_code == 200

    def test_what_is_only_the_systems_is_still_refused_in_the_tables_words(
        self, client: TestClient
    ) -> None:
        # Installing an engine with the package manager is what a machine that only has Docker does not do.
        response = client.post("/api/databases/engines/postgresql/install", json={})

        assert response.status_code in (403, 409), response.text
        assert "containers" in response.text.lower() or "container" in response.text.lower()

    def test_a_server_that_has_the_engines_says_so(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NOUST_CENTRAL_ROLE", "server")
        monkeypatch.delenv("NOUST_DEPLOY_HERE", raising=False)
        Config.reset_instance()
        from noust.central.machine import MachineFacts

        monkeypatch.setattr(
            "noust.central.machine.read_machine_facts",
            lambda *a, **k: MachineFacts(
                systemd=True, web_server=True, docker=True, root=True, journal=True
            ),
        )

        system = client.get("/api/databases/servers").json()["system"]

        assert system == {"available": True, "reason": None}


class TestMadeCopiedAndRestoredFromEndToEnd:
    """What a machine that only has Docker makes in a container, it makes (items 187 and 188)."""

    def make_the_instance(
        self, client: TestClient, queued: list[dict[str, Any]], world: World
    ) -> None:
        response = client.post("/api/databases/instances", json=BODY)

        assert response.status_code == 202, response.text
        run(queued[-1])
        assert "noust-db-pg16" in world.host.containers

    def test_an_engine_a_database_a_user_a_copy_and_its_restoration(
        self, client: TestClient, queued: list[dict[str, Any]], world: World
    ) -> None:
        self.make_the_instance(client, queued, world)
        key = "postgresql@pg16"
        # What pg_dump writes to its output starts the way a custom-format dump does, and what
        # pg_restore --list finds in it is what the check of the copy counts.
        world.host.script(["docker", "exec"], stdout=ARCHIVE)
        world.host.exec_stdout = LISTING

        made = client.post("/api/databases/databases", json={"name": "shop", "engine": key})
        assert made.status_code == 200, made.text
        assert made.json()["engine"] == key

        user = client.post(
            "/api/databases/users",
            json={
                "username": "shop_rw",
                "engine": key,
                "database": "shop",
                "profile": "read_write",
            },
        )
        assert user.status_code == 200, user.text
        assert user.json()["password"]

        users = client.get(f"/api/databases/users/{key}")
        assert users.status_code == 200, users.text
        assert "shop_rw" in [u["username"] for u in users.json()["users"]]

        copy = client.post(
            "/api/databases/backups", json={"database": "shop", "engine": key, "compress": True}
        )
        assert copy.status_code == 202, copy.text
        done = run(queued[-1])
        name = done.get("backup_name") or done.get("name") or done.get("file")
        assert name, done

        listed = client.get("/api/databases/backups", params={"engine": key})
        assert listed.status_code == 200, listed.text
        assert name in [b["name"] for b in listed.json()["backups"]]

        restored = client.post(
            "/api/databases/backups/restore",
            json={"database": "shop", "engine": key, "backup_name": name, "new_name": "shop_again"},
        )
        assert restored.status_code == 202, restored.text
        run(queued[-1])

        # Nothing a package manager does was asked for: a machine that only has Docker has none.
        assert not any(call[0] in ("apt-get", "dnf", "zypper", "yum") for call in world.host.calls)
