# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The previews API and CLI beyond 2.2: making one by hand, the open pull requests,
one preview at a time, the variables previews are given and how they are made
(checklist B-32, B-33, B-36, B-37).
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core.preview_store import PreviewStore
from noust.core.store import Database, NoustStore
from noust.integrations.github import pulls
from noust.managers import previews
from tests.test_previews_variables_data import open_pull
from tests.test_web_previews import (
    BASE,
    PARENT,
    app,
    elevated,
    master_token,
    no_timer,
    preview,
    queued,
    session,
    store,
)

FIXTURES = (app, elevated, master_token, no_timer, queued, session, store)

CHILD = f"pr-7-shop-example-com.{BASE}"


@pytest.fixture
def on(elevated: TestClient, store: NoustStore) -> TestClient:
    """The console with previews turned on for the application."""
    response = elevated.put(f"/api/apps/{PARENT}/previews/settings", json={"base_domain": BASE})
    assert response.status_code == 200, response.text
    return elevated


class TestHowTheyAreMade:
    def test_new_settings_give_only_what_is_named(self, on: TestClient) -> None:
        body = on.get(f"/api/apps/{PARENT}/previews").json()["settings"]

        assert (body["inherit_env"], body["allow_env"]) == ("allowlist", [])
        assert body["data_mode"] == "shared"  # the fixture application has no database

    def test_the_listing_says_whether_the_application_has_a_database_to_isolate(
        self, on: TestClient, store: NoustStore
    ) -> None:
        before = on.get(f"/api/apps/{PARENT}/previews").json()
        app_row = store.get_app(PARENT)
        assert app_row is not None
        store.create_database(Database(app_id=app_row.id, name="shop", engine="postgresql"))
        after = on.get(f"/api/apps/{PARENT}/previews").json()

        assert (before["has_database"], after["has_database"]) == (False, True)

    def test_the_ways_round_trip(self, elevated: TestClient, store: NoustStore) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings",
            json={
                "base_domain": BASE,
                "inherit_env": "all",
                "allow_env": ["API_URL"],
                "data_mode": "empty",
            },
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["inherit_env"], body["allow_env"], body["data_mode"]) == (
            "all",
            ["API_URL"],
            "empty",
        )
        assert PreviewStore(store).options(PARENT).data_mode == "empty"

    def test_a_way_that_does_not_exist_is_a_400(self, elevated: TestClient) -> None:
        response = elevated.put(
            f"/api/apps/{PARENT}/previews/settings",
            json={"base_domain": BASE, "inherit_env": "everything"},
        )

        assert response.status_code == 400, response.text


class TestTheVariablesPreviewsAreGiven:
    def test_defined_listed_masked_and_removed(self, on: TestClient, store: NoustStore) -> None:
        put = on.put(
            f"/api/apps/{PARENT}/previews/variables/SANDBOX_KEY",
            json={"value": "sk_test_abcdefghijklmnop"},
        )
        plain = on.put(
            f"/api/apps/{PARENT}/previews/variables/API_URL",
            json={"value": "https://staging.api", "audience": "build"},
        )

        assert put.status_code == 200 and plain.status_code == 200, plain.text
        listed = on.get(f"/api/apps/{PARENT}/previews/variables").json()
        by_name = {v["name"]: v for v in listed["variables"]}
        assert by_name["API_URL"]["value"] == "https://staging.api"
        assert by_name["API_URL"]["audience"] == "build"
        assert "sk_test" not in by_name["SANDBOX_KEY"]["value"]
        assert by_name["SANDBOX_KEY"]["secret"] is True
        gone = on.delete(f"/api/apps/{PARENT}/previews/variables/API_URL")
        assert [v["name"] for v in gone.json()["variables"]] == ["SANDBOX_KEY"]
        assert on.delete(f"/api/apps/{PARENT}/previews/variables/API_URL").status_code == 404

    def test_clear_values_are_read_in_sudo_mode(self, on: TestClient, store: NoustStore) -> None:
        on.put(
            f"/api/apps/{PARENT}/previews/variables/SANDBOX_KEY",
            json={"value": "sk_test_abcdefghijklmnop"},
        )

        clear = on.get(f"/api/apps/{PARENT}/previews/variables", params={"unmask": "true"})

        assert clear.status_code == 200, clear.text
        assert clear.json()["variables"][0]["value"] == "sk_test_abcdefghijklmnop"

    def test_clear_values_are_refused_outside_sudo_mode(
        self, session: TestClient, store: NoustStore
    ) -> None:
        refused = session.get(f"/api/apps/{PARENT}/previews/variables", params={"unmask": "true"})

        assert refused.status_code == 403
        assert refused.json()["error"] == "elevation_required"

    def test_a_change_needs_sudo_mode(self, session: TestClient, store: NoustStore) -> None:
        response = session.put(f"/api/apps/{PARENT}/previews/variables/A", json={"value": "1"})

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"


class TestMakingOneByHand:
    def test_from_a_branch(self, on: TestClient, queued: list[dict[str, Any]]) -> None:
        response = on.post(f"/api/apps/{PARENT}/previews", json={"branch": "feature/login"})

        assert response.status_code == 202, response.text
        body = response.json()
        assert body["preview"]["kind"] == "branch"
        assert body["preview"]["ref"] == "feature/login"
        assert body["preview"]["domain"] == f"x-1-shop-example-com.{BASE}"
        assert body["job_id"] == "job-1" and len(queued) == 1

    def test_from_a_pull_request(
        self, on: TestClient, queued: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(pulls, "get", lambda repository, number, installation=None: open_pull())

        response = on.post(f"/api/apps/{PARENT}/previews", json={"pull_request": 42})

        assert response.status_code == 202, response.text
        assert response.json()["preview"]["number"] == 42
        assert response.json()["preview"]["kind"] == "pr"

    def test_nothing_or_two_things_is_a_400(self, on: TestClient) -> None:
        assert on.post(f"/api/apps/{PARENT}/previews", json={}).status_code == 400
        both = on.post(f"/api/apps/{PARENT}/previews", json={"branch": "a", "tag": "b"})
        assert both.status_code == 400

    def test_it_needs_sudo_mode(self, session: TestClient, store: NoustStore) -> None:
        assert session.post(f"/api/apps/{PARENT}/previews", json={"branch": "a"}).status_code == 403

    def test_the_open_pull_requests(
        self, on: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        preview(store, number=42)
        monkeypatch.setattr(
            pulls,
            "list_open",
            lambda repository, installation=None: [open_pull(), open_pull(number=43)],
        )

        body = on.get(f"/api/apps/{PARENT}/previews/pull-requests").json()

        assert body["total"] == 2
        assert (body["pull_requests"][0]["number"], body["pull_requests"][0]["status"]) == (
            42,
            "ready",
        )
        assert body["pull_requests"][1]["preview"] is None

    def test_without_the_app_it_says_what_to_do(self, on: TestClient) -> None:
        response = on.get(f"/api/apps/{PARENT}/previews/pull-requests")

        assert response.status_code == 502
        assert "GitHub App" in response.text


class TestOneAtATime:
    def test_detail(
        self, on: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.web.api import previews as api

        preview(store)
        monkeypatch.setattr(
            api,
            "ServiceManager",
            lambda verbose=False: type(
                "M", (), {"get_status": lambda self, name: {"active": True}}
            )(),
        )

        body = on.get(f"/api/apps/{PARENT}/previews/7").json()

        assert body["preview"]["domain"] == CHILD
        assert body["preview"]["status"] == "ready"
        assert on.get(f"/api/apps/{PARENT}/previews/99").status_code == 404

    def test_redeploy_queues_an_update(
        self, on: TestClient, store: NoustStore, queued: list[dict[str, Any]]
    ) -> None:
        preview(store)
        store.create_app(
            __import__("noust.core.store", fromlist=["App"]).App(
                domain=CHILD, app_type="nodejs", port=3107, app_path="/x"
            )
        )

        response = on.post(f"/api/apps/{PARENT}/previews/7/redeploy")

        assert response.status_code == 202, response.text
        assert response.json()["preview"]["status"] == "pending"
        assert queued[0]["job_type"].name == "UPDATE"

    def test_stop_and_start(
        self, on: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.managers import service_manager

        class Manager:
            def __init__(self, verbose: bool = False) -> None:
                pass

            def service_exists(self, name: str) -> bool:
                return True

            def stop(self, name: str) -> None: ...

            def start(self, name: str) -> None: ...

        monkeypatch.setattr(service_manager, "ServiceManager", Manager)
        preview(store)

        stopped = on.post(f"/api/apps/{PARENT}/previews/7/stop")
        started = on.post(f"/api/apps/{PARENT}/previews/7/start")

        assert stopped.json()["status"] == "stopped"
        assert started.json()["status"] == "ready"

    def test_logs(self, on: TestClient, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.web.api import previews as api

        preview(store)
        monkeypatch.setattr(
            api,
            "ServiceManager",
            lambda verbose=False: type(
                "M", (), {"logs": lambda self, name, lines=50: "listening"}
            )(),
        )
        monkeypatch.setattr(previews, "last_build_log", lambda domain, tail=20000: "built it")

        body = on.get(f"/api/apps/{PARENT}/previews/7/logs?lines=20").json()

        assert (body["logs"], body["build"], body["lines"]) == ("listening", "built it", 20)

    def test_the_ones_that_change_something_need_sudo_mode(
        self, session: TestClient, store: NoustStore
    ) -> None:
        preview(store)

        for action in ("redeploy", "stop", "start"):
            response = session.post(f"/api/apps/{PARENT}/previews/7/{action}")
            assert response.status_code == 403, action

    def test_a_preview_of_another_application_is_not_found(
        self, on: TestClient, store: NoustStore
    ) -> None:
        assert on.get(f"/api/apps/{PARENT}/previews/7/logs").status_code == 404
        assert on.post(f"/api/apps/{PARENT}/previews/7/stop").status_code == 404


class TestTheCommandLine:
    @pytest.fixture(autouse=True)
    def wired(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        from noust.web.auth import STATE_DIR_ENV

        directory = tmp_path / "state"
        directory.mkdir(mode=0o700, exist_ok=True)
        monkeypatch.setenv(STATE_DIR_ENV, str(directory))

    def run(self, *args: str, code: int = 0) -> str:
        result = CliRunner().invoke(root_cli, list(args))
        assert result.exit_code == code, f"{result.output} {result.exception}"
        return f"{result.output}{result.exception or ''}"

    def test_enable_shows_how_previews_are_made(self, store: NoustStore) -> None:
        out = self.run(
            "preview",
            "enable",
            PARENT,
            "--domain",
            BASE,
            "--allow-env",
            "API_URL",
            "--data",
            "shared",
        )

        assert "Copied from the app" in out and "API_URL" in out
        listed = json.loads(self.run("--json", "preview", "list", PARENT))
        assert listed["settings"]["base_domain"] == BASE

    def test_create_records_and_builds_here(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        built: list[tuple[str, int]] = []
        monkeypatch.setattr(
            previews,
            "build_in_process",
            lambda parent, number, logger: built.append((parent, number)) or {},
        )
        self.run("preview", "enable", PARENT, "--domain", BASE, "--data", "shared")

        out = self.run("preview", "create", PARENT, "--branch", "feature/login")

        assert built == [(PARENT, 1_000_001)]
        assert "x-1-shop-example-com" in out

    def test_create_with_no_wait_only_records(
        self, store: NoustStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            previews, "build_in_process", lambda *a, **k: pytest.fail("must not build")
        )
        self.run("preview", "enable", PARENT, "--domain", BASE, "--data", "shared")

        out = json.loads(
            self.run("--json", "preview", "create", PARENT, "--tag", "v1", "--no-wait")
        )

        assert out["status"] == "pending" and out["kind"] == "tag"

    def test_create_wants_exactly_one_thing(self, store: NoustStore) -> None:
        self.run("preview", "enable", PARENT, "--domain", BASE, "--data", "shared")

        self.run("preview", "create", PARENT, code=1)

    def test_the_pull_requests(self, store: NoustStore, monkeypatch: pytest.MonkeyPatch) -> None:
        self.run("preview", "enable", PARENT, "--domain", BASE, "--data", "shared")
        monkeypatch.setattr(pulls, "list_open", lambda repository, installation=None: [open_pull()])

        out = self.run("preview", "pulls", PARENT)

        assert "#42" in out and "feature/search" in out

    def test_variables_for_previews(self, store: NoustStore) -> None:
        self.run("preview", "enable", PARENT, "--domain", BASE, "--data", "shared")
        self.run("preview", "vars", "set", PARENT, "API_URL", "https://staging.api")
        self.run("preview", "vars", "set", PARENT, "SANDBOX_KEY", "--stdin")  # empty stdin

        shown = json.loads(self.run("--json", "preview", "vars", "list", PARENT))
        self.run("preview", "vars", "unset", PARENT, "API_URL")

        assert {v["name"] for v in shown["variables"]} == {"API_URL", "SANDBOX_KEY"}
        self.run("preview", "vars", "unset", PARENT, "API_URL", code=1)

    def test_stop_start_logs_need_a_preview(self, store: NoustStore) -> None:
        self.run("preview", "stop", PARENT, code=1)
        self.run("preview", "logs", PARENT, code=1)
