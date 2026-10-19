# Copyright (c) 2024-2026 Yago Lopez Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An image, a command or arguments of the operator's own, over HTTP (4.0).

The engine is ``tests/test_database_custom_image.py`` and ``tests/test_database_launch.py``; the
terminal is ``tests/test_cli_db_instance_custom.py``. What is pinned here is the contract of the
routes: the price is in the plan and in the refusal (a 400 ``confirmation_required`` that carries
every warning, nothing queued), the acceptance is a field of the request and not a header, a word
is a JSON string and never a shell line, reading and planning ask for nothing, and changing the
words asks for sudo mode and runs as a job that is refused up front when it cannot succeed.
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
import noust.web.api.databases.instances as instances_module
import noust.web.api.databases.jobs as jobs_module
import noust.web.api.databases.launch as launch_module
from noust.managers.database import managed as managed_module
from noust.web.auth import CSRF_HEADER_NAME, SecurityConfig
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.database_instance_support import World, build_world, close_world

DIGEST = "sha256:" + "ab" * 32
OTHER = "sha256:" + "cd" * 32
IMAGE = f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
BASE = "/api/databases/instances"
BODY = {"name": "own", "engine": "postgresql", "version": "17", "memory_mb": 1024}


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    built = build_world(tmp_path, monkeypatch)
    monkeypatch.setattr(managed_module, "host_architecture", lambda: "amd64")
    yield built
    close_world()


@pytest.fixture
def app(tmp_path: Path, world: World, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    def same_service(session: Any = None, logger: Any = None) -> Any:
        return world.managed.service

    for module in (instances_module, launch_module):
        monkeypatch.setattr(module, "service", same_service)
    monkeypatch.setattr(
        jobs_module, "_service", lambda job_context, step, actor: world.managed.service
    )
    monkeypatch.setattr(
        "noust.managers.database.managed.server_resources", world.managed._resources
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


def run(job: dict[str, Any]) -> dict[str, Any]:
    return job["func"](**job["kwargs"])


class TestThePlan:
    def test_an_image_of_the_operators_says_the_price_and_that_it_needs_accepting(
        self, plain: TestClient, world: World
    ) -> None:
        response = plain.post(f"{BASE}/plan", json={**BODY, "image": IMAGE})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["custom"] is True and body["requires_confirmation"] is True
        assert body["reference"] == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"
        assert any("not tested" in warning for warning in body["warnings"])
        assert world.managed.records.all() == []

    def test_words_are_a_json_list_and_come_back_as_one(self, plain: TestClient) -> None:
        response = plain.post(f"{BASE}/plan", json={**BODY, "args": ["-c", "max_connections=77"]})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["args"] == ["-c", "max_connections=77"] and body["command"] == []
        assert body["requires_confirmation"] is True

    def test_a_shell_line_is_not_words(self, plain: TestClient) -> None:
        response = plain.post(f"{BASE}/plan", json={**BODY, "args": "-c max_connections=77"})

        assert response.status_code in (400, 422)

    @pytest.mark.parametrize(
        "extra",
        [
            {"image": "ghcr.io/acme/pg-timescale:17.2"},
            {"image": "-v"},
            {"args": ["--requirepass", "x"]},
            {"args": ["a\nb"]},
            {"command": ["postgres"], "args": ["-c", "x=1"]},
            {"image": IMAGE, "variant": "pgvector"},
        ],
    )
    def test_what_could_not_be_had_is_refused_with_the_reason(
        self, plain: TestClient, extra: dict[str, Any]
    ) -> None:
        response = plain.post(f"{BASE}/plan", json={**BODY, **extra})

        assert response.status_code == 400, response.text

    def test_an_official_instance_asks_for_nothing(self, plain: TestClient) -> None:
        body = plain.post(f"{BASE}/plan", json=BODY).json()

        assert body["custom"] is False and body["requires_confirmation"] is False


class TestMakingOne:
    def test_without_the_acceptance_nothing_is_queued_and_the_warnings_come_back(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(BASE, json={**BODY, "image": IMAGE})

        assert response.status_code == 400
        assert response.json()["error"] == "confirmation_required"
        assert any("not tested" in warning for warning in response.json()["warnings"])
        assert queued == []

    def test_accepted_it_is_queued_with_what_the_operator_chose(
        self, admin: TestClient, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            BASE,
            json={**BODY, "image": IMAGE, "args": None, "accept_untested": True},
        )

        assert response.status_code == 202, response.text
        [job] = queued
        assert job["kwargs"]["image"] == IMAGE
        assert job["kwargs"]["accept_untested"] is True

    def test_the_job_makes_the_instance_from_that_image_and_those_words(
        self, admin: TestClient, world: World, queued: list[dict[str, Any]]
    ) -> None:
        admin.post(
            BASE,
            json={**BODY, "args": ["-c", "max_connections=77"], "accept_untested": True},
        )

        result = run(queued[0])["instance"]

        assert result["args"] == ["-c", "max_connections=77"] and result["customised"] is True
        assert world.managed.get("own").args == ("-c", "max_connections=77")

    def test_an_image_through_the_job_is_pinned_to_its_digest(
        self, admin: TestClient, world: World, queued: list[dict[str, Any]]
    ) -> None:
        admin.post(BASE, json={**BODY, "image": IMAGE, "accept_untested": True})

        result = run(queued[0])["instance"]

        assert result["custom_image"] is True
        assert result["reference"] == f"ghcr.io/acme/pg-timescale:17.2@{DIGEST}"

    def test_it_asks_for_sudo_mode(self, plain: TestClient, queued: list[dict[str, Any]]) -> None:
        response = plain.post(BASE, json={**BODY, "image": IMAGE, "accept_untested": True})

        assert response.status_code in (401, 403) and queued == []

    def test_the_listing_says_what_is_the_operators(self, plain: TestClient, world: World) -> None:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )

        [entry] = plain.get(BASE).json()["instances"]

        assert entry["custom_image"] is True and entry["customised"] is True
        assert entry["command"] == [] and entry["args"] == []


class TestUpgradingOne:
    @pytest.fixture
    def own(self, world: World) -> World:
        world.managed.create(
            "own", "postgresql", version="17", memory="1g", image=IMAGE, accept_untested=True
        )
        return world

    def test_an_instance_on_an_image_of_the_operators_needs_the_next_one_named(
        self, admin: TestClient, own: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(f"{BASE}/own/upgrade", params={"backup": False})

        assert response.status_code == 400
        assert "image" in response.text.lower()
        assert queued == []

    def test_naming_it_needs_the_acceptance_again(
        self, admin: TestClient, own: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            f"{BASE}/own/upgrade",
            params={"backup": False},
            json={"image": f"ghcr.io/acme/pg-timescale:17.3@{OTHER}"},
        )

        assert response.status_code == 400
        assert response.json()["error"] == "confirmation_required"
        assert queued == []

    def test_accepted_the_job_moves_it_to_exactly_that_digest(
        self, admin: TestClient, own: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.post(
            f"{BASE}/own/upgrade",
            params={"backup": False},
            json={"image": f"ghcr.io/acme/pg-timescale:17.3@{OTHER}", "accept_untested": True},
        )

        assert response.status_code == 202, response.text
        result = run(queued[0])["upgrade"]
        assert result["changed"] is True and result["to_digest"] == OTHER

    def test_a_catalog_instance_still_upgrades_with_no_body(
        self, admin: TestClient, world: World, queued: list[dict[str, Any]]
    ) -> None:
        world.managed.create("ref", "postgresql", version="17", memory="1g")

        response = admin.post(f"{BASE}/ref/upgrade", params={"backup": False})

        assert response.status_code == 202, response.text

    def test_a_catalog_instance_does_not_take_an_image(
        self, admin: TestClient, world: World, queued: list[dict[str, Any]]
    ) -> None:
        world.managed.create("ref", "postgresql", version="17", memory="1g")

        response = admin.post(f"{BASE}/ref/upgrade", json={"image": IMAGE, "accept_untested": True})

        assert response.status_code == 400 and queued == []


class TestLaunch:
    URL = f"{BASE}/pg/launch"

    @pytest.fixture
    def made(self, world: World) -> World:
        world.managed.create("pg", "postgresql", version="17", memory="1g")
        return world

    def test_reading_says_what_the_container_is_started_with(
        self, plain: TestClient, made: World
    ) -> None:
        response = plain.get(self.URL)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["key"] == "postgresql@pg" and body["customised"] is False
        assert body["effective"][0] == "postgres"

    def test_an_unknown_instance_is_a_404(self, plain: TestClient) -> None:
        assert plain.get(f"{BASE}/nope/launch").status_code == 404

    def test_a_plan_says_what_changes_and_changes_nothing(
        self, plain: TestClient, made: World
    ) -> None:
        response = plain.post(f"{self.URL}/plan", json={"args": ["-c", "work_mem=8MB"]})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["restarts"] is True and body["requires_confirmation"] is True
        assert body["changes"] == [{"setting": "args", "from": [], "to": ["-c", "work_mem=8MB"]}]
        assert body["effective"][-2:] == ["-c", "work_mem=8MB"]
        assert made.managed.get("pg").args == ()

    def test_saying_nothing_is_a_400(self, plain: TestClient, made: World) -> None:
        assert plain.post(f"{self.URL}/plan", json={}).status_code == 400

    def test_changing_asks_for_sudo_mode(
        self, plain: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        response = plain.put(self.URL, json={"args": ["-c", "x=1"], "accept_untested": True})

        assert response.status_code in (401, 403) and queued == []

    def test_without_the_acceptance_nothing_is_queued(
        self, admin: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.put(self.URL, json={"args": ["-c", "work_mem=8MB"]})

        assert response.status_code == 400
        assert response.json()["error"] == "confirmation_required"
        assert any("not tested" in warning for warning in response.json()["warnings"])
        assert queued == []

    def test_accepted_the_job_restarts_the_engine_on_the_words(
        self, admin: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.put(
            self.URL, json={"args": ["-c", "work_mem=8MB"], "accept_untested": True}
        )

        assert response.status_code == 202, response.text
        result = run(queued[0])
        assert result["args"] == ["-c", "work_mem=8MB"]
        assert made.managed.get("pg").args == ("-c", "work_mem=8MB")

    def test_taking_them_back_needs_no_acceptance(
        self, admin: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        admin.put(self.URL, json={"args": ["-c", "work_mem=8MB"], "accept_untested": True})
        run(queued[0])

        response = admin.put(self.URL, json={"reset": True})

        assert response.status_code == 202, response.text
        assert run(queued[1])["customised"] is False

    def test_what_cannot_succeed_is_refused_before_a_job_exists(
        self, admin: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        made.managed.records.set_state("pg", "upgrading")

        response = admin.put(self.URL, json={"args": ["-c", "x=1"], "accept_untested": True})

        assert response.status_code == 400 and "not ready" in response.text
        assert queued == []

    def test_a_command_for_an_instance_that_serves_tls_is_refused_up_front(
        self, admin: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        made.managed.records.set_tls_mode("pg", "optional")

        response = admin.put(self.URL, json={"command": ["postgres"], "accept_untested": True})

        assert response.status_code == 400 and "TLS" in response.text
        assert queued == []

    def test_a_secret_does_not_go_in_argv(
        self, admin: TestClient, made: World, queued: list[dict[str, Any]]
    ) -> None:
        response = admin.put(
            self.URL, json={"args": ["--requirepass=hunter2"], "accept_untested": True}
        )

        assert response.status_code == 400 and "hunter2" not in response.text
        assert queued == []


class TestInTheMaps:
    def test_each_route_has_its_permission(self) -> None:
        routes = route_map()

        assert routes[("GET", f"{BASE}/{{name}}/launch")] == "databases.read"
        assert routes[("POST", f"{BASE}/{{name}}/launch/plan")] == "databases.read"
        assert routes[("PUT", f"{BASE}/{{name}}/launch")] == "databases.manage"

    def test_changing_and_upgrading_ask_for_sudo_mode(self, app: FastAPI) -> None:
        paths = app.openapi()["paths"]

        assert paths[f"{BASE}/{{name}}/launch"]["put"]["x-noust-requires-elevation"] is True
        assert paths[f"{BASE}/{{name}}/upgrade"]["post"]["x-noust-requires-elevation"] is True
        assert "x-noust-requires-elevation" not in paths[f"{BASE}/{{name}}/launch"]["get"]
