# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Moving a database to another server, through the console's API and the terminal (checklist C-21).

The engine is ``test_fleet_database_transfer.py``'s. What is pinned here is that the two surfaces are
clients of it and nothing more: the same plan, the same refusals in the same words, sudo mode asked
by the node's own schema, a permission on the route, and a job to follow whose record never holds a
password.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.config import Config
from noust.core.runner import FakeRunner, set_runner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.web import fleet_jobs
from noust.web.auth import STATE_DIR_ENV
from noust.web.jobs import JobManager
from noust.web.permissions.registry import route_map
from noust.web.server import create_app, get_token_manager
from tests.fleet_views_support import Central, build_central
from tests.test_fleet_database_transfer import (
    DATABASE,
    DUMP_NAME,
    PAYLOAD,
    SECRET,
    Source,
    Target,
    access_row,
)
from tests.test_web_auth import make_config
from tests.test_web_central import elevate, sign_in

NAMES = ("db-1", "db-2", "db-3")
BODY = {"database": DATABASE, "engine": "postgresql", "from_node": "db-1", "to": "db-2"}


@pytest.fixture(autouse=True)
def default_runner_after_a_rehearsal() -> Iterator[None]:
    yield
    set_runner(None)


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv(STATE_DIR_ENV, str(state))
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr("noust.fleet.nodes.central_name", lambda: "nas")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", NAMES)
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
    monkeypatch.setattr(fleet_jobs, "POLL_SECONDS", 0)
    monkeypatch.setattr(
        "noust.core.update_checker.UpdateChecker.enabled", classmethod(lambda cls: False)
    )
    JobManager.reset_instance()
    yield built
    JobManager.reset_instance()
    set_aggregator(None)
    Config.reset_instance()
    NoustStore.reset_instance()


@pytest.fixture
def source(central: Central) -> Source:
    return Source(central.nodes["db-1"])


@pytest.fixture
def target(central: Central) -> Target:
    return Target(central.nodes["db-2"])


class TestTheApi:
    @pytest.fixture
    def client(self, central: Central, sandbox: Path, runner: FakeRunner) -> TestClient:
        return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))

    def wait(self, client: TestClient, job_id: str) -> dict[str, Any]:
        for _ in range(400):
            job = client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in ("completed", "failed"):
                return job
            time.sleep(0.05)
        raise AssertionError(f"job {job_id} did not finish")

    def test_it_is_a_fleet_managers_route(self) -> None:
        assert route_map()[("POST", "/api/fleet/databases/transfer")] == "fleet.manage"

    def test_it_is_in_the_catalog_of_bulk_actions(self, client: TestClient) -> None:
        sign_in(client)

        names = {a["name"] for a in client.get("/api/fleet/actions").json()["actions"]}

        assert "database_transfer" in names

    def test_a_plan_dumps_nothing_and_says_what_will_happen(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        sign_in(client)

        response = client.post("/api/fleet/databases/transfer", json={**BODY, "plan": True})

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["job"] is None
        assert body["plan"]["batches"] == [["db-2"]]
        assert body["plan"]["requires_elevation"] is True
        assert source.dumps == [] and target.uploads == []

    def test_sudo_mode_is_asked_because_the_target_marks_its_import_as_needing_it(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        master = sign_in(client)

        refused = client.post("/api/fleet/databases/transfer", json=BODY)
        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"
        assert source.dumps == []

        elevate(client, master)
        accepted = client.post("/api/fleet/databases/transfer", json=BODY)
        assert accepted.status_code == 200, accepted.text
        # The move it queued runs in the console's job thread: left running, it ended in the next
        # test and put the database on that test's target before its own move began.
        self.wait(client, accepted.json()["job"]["job_id"])

    def test_the_move_runs_as_a_job_and_ends_on_the_target(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        master = sign_in(client)
        elevate(client, master)
        source.access = [access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        response = client.post("/api/fleet/databases/transfer", json={**BODY, "accounts": True})

        assert response.status_code == 200, response.text
        job = self.wait(client, response.json()["job"]["job_id"])
        assert job["status"] == "completed", job
        assert bytes(target.received) == PAYLOAD
        assert target.databases == [DATABASE]
        assert [body["username"] for body in target.created_users] == ["shop_rw"]
        assert source.deleted_dumps == [DUMP_NAME]
        states = {n["node"]: n["state"] for n in job["result"]["nodes"]}
        assert states == {"db-2": "succeeded"}
        # The operator's sudo mode travels to both servers, which check it themselves.
        asked = next(r for r in central_requests(source) if r.url.path.endswith("/data"))
        assert asked.headers["X-Noust-Elevated"] == "1"

    def test_a_password_is_in_no_answer_the_console_can_read(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        master = sign_in(client)
        elevate(client, master)
        source.access = [access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        response = client.post("/api/fleet/databases/transfer", json={**BODY, "accounts": True})
        job = self.wait(client, response.json()["job"]["job_id"])

        assert SECRET not in response.text
        assert SECRET not in json.dumps(job)
        assert SECRET not in client.get(f"/api/jobs/{job['id']}/log").text
        listed = client.get("/api/fleet/jobs").text
        assert SECRET not in listed

    def test_a_database_the_source_does_not_have_is_a_400_with_the_reason(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        sign_in(client)
        source.databases = []

        response = client.post("/api/fleet/databases/transfer", json={**BODY, "plan": True})

        assert response.status_code == 400
        assert "not a database of postgresql on db-1" in response.text

    def test_an_engine_that_does_not_travel_as_a_dump_is_a_400(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        sign_in(client)

        response = client.post(
            "/api/fleet/databases/transfer", json={**BODY, "engine": "redis", "plan": True}
        )

        assert response.status_code == 400
        assert "do not move to another server as a dump" in response.text

    def test_a_read_token_cannot_move_a_database(
        self, client: TestClient, source: Source, target: Target
    ) -> None:
        sign_in(client)
        issued = get_token_manager().create_api_token("reader", "read")

        response = client.post(
            "/api/fleet/databases/transfer",
            json=BODY,
            headers={"Authorization": f"Bearer {issued['token']}"},
        )

        assert response.status_code == 403
        assert source.dumps == []


def central_requests(source: Source) -> list[Any]:
    return list(source.node.requests)


class TestTheTerminal:
    def invoke(self, *args: str):
        return CliRunner().invoke(root_cli, ["db", "transfer", *args])

    def base(self, *extra: str) -> list[str]:
        return [DATABASE, "--from", "db-1", "--to", "db-2", *extra]

    def test_the_plan_is_shown_and_nothing_is_dumped(
        self, central: Central, source: Source, target: Target
    ) -> None:
        result = self.invoke(*self.base("--plan", "--json"))

        assert result.exit_code == 0, result.output
        plan = json.loads(result.output.splitlines()[0])["plan"]
        assert plan["batches"] == [["db-2"]]
        assert source.dumps == [] and target.uploads == []

    def test_a_rehearsal_dumps_nothing_either(
        self, central: Central, source: Source, target: Target
    ) -> None:
        result = CliRunner().invoke(root_cli, ["--dry-run", "db", "transfer", *self.base()])

        assert result.exit_code == 0, result.output
        assert source.dumps == [] and target.uploads == []

    def test_it_runs_and_exits_clean(
        self, central: Central, source: Source, target: Target
    ) -> None:
        result = self.invoke(*self.base("-y"))

        assert result.exit_code == 0, result.output
        assert bytes(target.received) == PAYLOAD
        assert target.databases == [DATABASE]
        assert source.dropped == []

    def test_without_saying_yes_it_asks_and_a_no_runs_nothing(
        self, central: Central, source: Source, target: Target
    ) -> None:
        result = CliRunner().invoke(root_cli, ["db", "transfer", *self.base()], input="n\n")

        assert result.exit_code == 0, result.output
        assert source.dumps == []

    def test_the_options_reach_the_request(
        self, central: Central, source: Source, target: Target
    ) -> None:
        source.access = [access_row("shop_rw")]
        source.set_passwords({"shop_rw": SECRET})

        result = self.invoke(
            *self.base("--as", "shop_copy", "--accounts", "--drop-source", "-y", "--json")
        )

        assert result.exit_code == 0, result.output
        assert target.imports[0]["database"] == "shop_copy"
        assert [body["database"] for body in target.created_users] == ["shop_copy"]
        assert len(source.dropped) == 1
        assert SECRET not in result.output

    def test_a_failed_move_makes_the_exit_status_one(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.fail_import = "disk full"

        result = self.invoke(*self.base("-y"))

        assert result.exit_code == 1, result.output

    def test_a_refusal_before_anything_is_dumped_says_why(
        self, central: Central, source: Source, target: Target
    ) -> None:
        target.databases = [DATABASE]

        result = self.invoke(*self.base("-y"))

        assert result.exit_code != 0
        assert source.dumps == []
        assert "already on db-2" in (result.output + str(result.exception))

    def test_an_alias_is_read_as_the_engine_it_names(
        self, central: Central, source: Source, target: Target
    ) -> None:
        result = self.invoke(*self.base("-e", "pg", "--plan", "--json"))

        assert result.exit_code == 0, result.output
        assert source.dumps == []
