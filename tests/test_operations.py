# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The long operations that answer on the request and are not jobs, told to everyone else.

Starting an application, verifying a backup or rebooting the server takes a while and answers 200
when it is done: a promise of ``docs/api.md`` that no route may break. Only the browser that asked
sees it run. A dependency of the API now announces each one on the closed list of
``noust.web.operations`` as the ``operation`` event (a start and an end) and keeps the ones that
run for ``GET /api/operations/active``, so another operator, the console's tray and a central can
say what the server is doing. A team member sees only what concerns its own applications.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from noust.core import server_text
from noust.core.server_text import CATALOG
from noust.core.store import NoustStore
from noust.web import events as events_module
from noust.web import operations
from noust.web.auth import require_auth
from noust.web.operations import OPERATIONS, active_operations, track_operation
from noust.web.server import create_app
from tests.support.console_catalog import CONSOLE_CATALOGS, sentences_of
from tests.support.teams_support import World, build_world
from tests.support.web_auth import make_config

ACME = "shop.acme.example"
GLOBEX = "blog.globex.example"


@pytest.fixture(autouse=True)
def nothing_is_running() -> Iterator[None]:
    active_operations.clear()
    yield
    active_operations.clear()


class Hub:
    """Stands in for the stream hub and records what is published."""

    def __init__(self) -> None:
        self.published: list[tuple[str, dict[str, Any]]] = []

    def publish(self, name: str, payload: dict[str, Any]) -> None:
        self.published.append((name, payload))


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch) -> Hub:
    stand_in = Hub()
    monkeypatch.setattr(operations, "hub", stand_in)
    return stand_in


class TestTheClosedList:
    def test_each_operation_is_a_sentence_of_the_catalog_with_at_most_its_target(self) -> None:
        for (method, template), operation in OPERATIONS.items():
            assert operation.code in CATALOG, (method, template)
            expected = {"target"} if operation.target else set()
            assert server_text.values_of(operation.code) == expected, operation.code
            if operation.target:
                assert "{" + operation.target + "}" in template, (method, template)

    def test_the_catalog_holds_no_operation_that_the_list_does_not_have(self) -> None:
        listed = {operation.code for operation in OPERATIONS.values()}

        assert {code for code in CATALOG if code.startswith("operations.")} == listed

    def test_the_list_is_what_the_plan_names_and_nothing_else(self) -> None:
        named = {
            ("POST", "/api/apps/{domain}/start"),
            ("POST", "/api/apps/{domain}/stop"),
            ("POST", "/api/apps/{domain}/restart"),
            ("POST", "/api/services/{name}/start"),
            ("POST", "/api/services/{name}/stop"),
            ("POST", "/api/services/{name}/restart"),
            ("POST", "/api/databases/engines/{engine}/start"),
            ("POST", "/api/databases/engines/{engine}/stop"),
            ("POST", "/api/databases/engines/{engine}/restart"),
            ("POST", "/api/cron/{name}/run"),
            ("POST", "/api/backups/{backup_id}/verify"),
            ("POST", "/api/monitor/install"),
            ("POST", "/api/monitor/scan"),
            ("POST", "/api/probes/{name}/check"),
            ("POST", "/api/nodes/{node}/test"),
            ("POST", "/api/waf/install"),
            ("POST", "/api/compose/import"),
            ("POST", "/api/apps/{domain}/env/sync"),
            ("POST", "/api/apps/{domain}/move"),
            ("DELETE", "/api/apps/{domain}/build-cache"),
            ("PUT", "/api/apps/{domain}/build"),
            ("POST", "/api/server/power/reboot"),
            ("POST", "/api/server/power/shutdown"),
            ("POST", "/api/sites/reload"),
            ("POST", "/api/apps/{domain}/apply"),
            ("POST", "/api/fleet/settings/apply"),
            ("POST", "/api/backups/encryption/enable"),
            ("POST", "/api/backups/encryption/rotate"),
        }

        assert set(OPERATIONS) == named

    def test_every_one_is_a_route_that_answers_200_and_none_became_a_job(
        self, sandbox: Path, runner: Any
    ) -> None:
        # The contract of docs/api.md: no route changes its response code. These answered 200
        # with their result; an operation that is announced is still that.
        schema = create_app(make_config(sandbox)).openapi()

        for method, template in OPERATIONS:
            operation = schema["paths"][template][method.lower()]
            assert "200" in operation["responses"], (method, template)
            assert "202" not in operation["responses"], (method, template)

    def test_the_console_says_every_operation_in_english_and_in_spanish(self) -> None:
        codes = sorted({operation.code for operation in OPERATIONS.values()})

        for language, path in CONSOLE_CATALOGS.items():
            catalog = sentences_of(path)
            missing = [code for code in codes if code not in catalog]
            assert missing == [], f"{language} has no text for {missing}"
            for code in codes:
                said = set(re.findall(r"\{(\w+)\}", catalog[code]))
                assert said == server_text.values_of(code), (language, code)


def tiny_app(*, fail: int | None = None) -> FastAPI:
    """An app with the dependency and two routes the closed list names, for the dependency alone."""
    app = FastAPI(dependencies=[Depends(track_operation)])

    async def signed_in() -> dict[str, Any]:
        return {"type": "master", "id": "abcdef0123456789", "token_name": "master"}

    app.dependency_overrides[require_auth] = signed_in

    @app.post("/api/apps/{domain}/restart")
    def restart(domain: str) -> dict[str, str]:
        if fail is not None:
            raise HTTPException(status_code=fail, detail="no")
        return {"domain": domain}

    @app.post("/api/sites/reload")
    def reload() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/api/not/on/the/list")
    def other() -> dict[str, bool]:
        return {"ok": True}

    return app


class TestTheDependency:
    def test_a_listed_route_announces_its_start_and_its_end_and_answers_as_before(
        self, hub: Hub
    ) -> None:
        response = TestClient(tiny_app()).post("/api/apps/shop.example.com/restart")

        assert response.status_code == 200 and response.json() == {"domain": "shop.example.com"}
        assert [name for name, _ in hub.published] == ["operation", "operation"]
        start, end = (payload for _, payload in hub.published)
        assert start["phase"] == "start" and end["phase"] == "end"
        assert start["id"] == end["id"] and start["id"].startswith("op-")
        assert start["title"] == {
            "code": "operations.app_restart",
            "params": {"target": "shop.example.com"},
        }
        assert start["target"] == start["domain"] == "shop.example.com"
        assert start["actor"] == "master" or start["actor"]
        assert start["at"] and "result" not in start
        assert end["result"] == "ok" and end["at"] >= start["at"]

    def test_it_is_in_the_list_of_what_runs_only_while_it_runs(self, hub: Hub) -> None:
        seen: list[list[str]] = []
        app = tiny_app()

        @app.post("/api/server/power/reboot")
        def reboot() -> dict[str, bool]:
            seen.append([item.id for item in active_operations.active()])
            return {"ok": True}

        TestClient(app).post("/api/server/power/reboot")

        assert len(seen) == 1 and len(seen[0]) == 1
        assert active_operations.active() == []

    def test_a_route_that_fails_ends_as_failed_and_still_answers_its_error(self, hub: Hub) -> None:
        response = TestClient(tiny_app(fail=409)).post("/api/apps/shop.example.com/restart")

        assert response.status_code == 409 and response.json() == {"detail": "no"}
        assert [p["phase"] for _, p in hub.published] == ["start", "end"]
        assert hub.published[-1][1]["result"] == "failed"
        assert active_operations.active() == []

    def test_an_error_nobody_planned_ends_it_too(self, hub: Hub) -> None:
        app = tiny_app()

        @app.post("/api/waf/install")
        def broken() -> None:
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            TestClient(app).post("/api/waf/install")

        assert hub.published[-1][1]["result"] == "failed"
        assert active_operations.active() == []

    def test_a_route_that_is_not_on_the_list_announces_nothing(self, hub: Hub) -> None:
        response = TestClient(tiny_app()).post("/api/not/on/the/list")

        assert response.status_code == 200
        assert hub.published == [] and active_operations.active() == []

    def test_a_route_with_no_subject_says_none_and_a_long_one_is_cut(self, hub: Hub) -> None:
        TestClient(tiny_app()).post("/api/sites/reload")
        long_name = "a" * 400
        TestClient(tiny_app()).post(f"/api/apps/{long_name}/restart")

        reload_start = hub.published[0][1]
        assert reload_start["target"] is None and reload_start["title"]["params"] == {}
        assert len(hub.published[2][1]["target"]) == operations.MAX_TARGET

    def test_a_subject_with_a_line_break_cannot_start_another_line_anywhere(self, hub: Hub) -> None:
        TestClient(tiny_app()).post("/api/apps/a%0Ab/restart")

        target = hub.published[0][1]["target"]
        assert "\n" not in target and "\r" not in target

    def test_what_is_published_is_json_a_stream_can_write(self, hub: Hub) -> None:
        TestClient(tiny_app()).post("/api/apps/shop.example.com/restart")

        for _, payload in hub.published:
            frame = events_module.format_event("operation", payload)
            assert frame.startswith("event: operation\ndata: ") and frame.endswith("\n\n")
            assert json.loads(frame.split("data: ", 1)[1])["id"] == payload["id"]

    def test_an_operation_that_never_ends_is_forgotten_after_a_long_time(
        self, hub: Hub, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        running = active_operations.begin(OPERATIONS[("POST", "/api/sites/reload")], {}, "ana")
        assert [item.id for item in active_operations.active()] == [running.id]

        monkeypatch.setattr(operations.time, "monotonic", lambda: 10**9)

        assert active_operations.active() == []


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, runner: Any) -> Any:
    built = build_world(tmp_path, monkeypatch)
    try:
        yield built
    finally:
        NoustStore.reset_instance()


def start(world: World, key: tuple[str, str], target: dict[str, str], actor: str) -> Any:
    return active_operations.begin(OPERATIONS[key], target, actor)


class TestInTheRealApplication:
    def test_a_real_route_of_the_list_is_announced_and_the_ones_refused_are_not(
        self, world: World, hub: Hub
    ) -> None:
        refused = world.client.post("/api/sites/reload", headers=world.as_("dana"))
        allowed = world.client.post("/api/sites/reload", headers=world.master)

        assert refused.status_code in (403, 404)
        assert allowed.status_code == 200, allowed.text
        assert [p["phase"] for _, p in hub.published] == ["start", "end"]
        assert hub.published[0][1]["title"]["code"] == "operations.sites_reload"
        assert hub.published[1][1]["result"] == "ok"
        assert world.client.get("/api/operations/active", headers=world.master).json() == {
            "operations": []
        }


class TestWhatIsRunning:
    def test_it_lists_what_runs_now_with_who_started_it(self, world: World) -> None:
        start(world, ("POST", "/api/apps/{domain}/restart"), {"domain": ACME}, "alice")

        body = world.client.get("/api/operations/active", headers=world.master).json()

        assert len(body["operations"]) == 1
        item = body["operations"][0]
        assert item["title"] == {"code": "operations.app_restart", "params": {"target": ACME}}
        assert item["target"] == ACME and item["domain"] == ACME
        assert item["actor"] == "alice" and item["started_at"]

    def test_nothing_running_is_an_empty_list(self, world: World) -> None:
        body = world.client.get("/api/operations/active", headers=world.master).json()

        assert body == {"operations": []}

    def test_a_team_member_sees_only_what_concerns_its_own_applications(self, world: World) -> None:
        start(world, ("POST", "/api/apps/{domain}/restart"), {"domain": ACME}, "omar")
        start(world, ("POST", "/api/apps/{domain}/stop"), {"domain": GLOBEX}, "bob")
        start(world, ("POST", "/api/server/power/reboot"), {}, "root-admin")
        start(world, ("POST", "/api/services/{name}/restart"), {"name": "nginx"}, "root-admin")

        mine = world.client.get("/api/operations/active", headers=world.as_("alice")).json()
        theirs = world.client.get("/api/operations/active", headers=world.as_("bob")).json()
        everything = world.client.get("/api/operations/active", headers=world.master).json()

        assert [o["target"] for o in mine["operations"]] == [ACME]
        assert [o["target"] for o in theirs["operations"]] == [GLOBEX]
        assert len(everything["operations"]) == 4

    def test_an_administrator_of_the_instance_sees_the_machine_too(self, world: World) -> None:
        start(world, ("POST", "/api/server/power/reboot"), {}, "root-admin")

        body = world.client.get("/api/operations/active", headers=world.as_("admin")).json()

        assert [o["title"]["code"] for o in body["operations"]] == ["operations.power_reboot"]

    def test_the_route_declares_its_permission_and_its_scope(self) -> None:
        from noust.web.permissions import Permission
        from noust.web.permissions.registry import permission_for_route
        from noust.web.permissions.scopes import SCOPED, rule_for

        assert permission_for_route("GET", "/api/operations/active") == Permission.APPS_READ
        rule = rule_for("GET", "/api/operations/active")
        assert rule is not None and rule.scoping == SCOPED

    def test_an_anonymous_request_is_refused(self, world: World) -> None:
        assert world.client.get("/api/operations/active").status_code == 401


class TestTheStream:
    """The same filter a team member's stream applies to a job applies to an operation."""

    def operation_frames(self, session: dict[str, Any] | None) -> list[dict[str, Any]]:
        """Open a stream as a principal, publish three operations and read what it carries."""

        class Request:
            async def is_disconnected(self) -> bool:
                return False

        async def exercise() -> list[dict[str, Any]]:
            stream = events_module._stream(Request(), session)  # type: ignore[arg-type]
            await stream.__anext__()
            for index, domain in enumerate((ACME, GLOBEX, None), start=1):
                events_module.hub.publish(
                    "operation", {"id": f"op-{index}", "domain": domain, "phase": "start"}
                )
            found: list[dict[str, Any]] = []
            while True:
                try:
                    frame = await asyncio.wait_for(stream.__anext__(), timeout=0.4)
                except asyncio.TimeoutError:
                    break
                if frame.startswith("event: operation\n"):
                    found.append(json.loads(frame.split("data: ", 1)[1]))
            await stream.aclose()
            return found

        return asyncio.run(exercise())

    def team_session(self, world: World, slug: str) -> dict[str, Any]:
        team = world.teams.get_team(slug)
        assert team is not None
        return {"type": "api_token", "account_id": 1, "team_roles": {team.id: "admin"}}

    def test_a_team_member_receives_the_operations_of_its_applications_and_no_others(
        self, world: World
    ) -> None:
        frames = self.operation_frames(self.team_session(world, "acme"))

        assert [frame["id"] for frame in frames] == ["op-1"]

    def test_the_instance_receives_all_of_them(self, world: World) -> None:
        frames = self.operation_frames(None)

        assert [frame["id"] for frame in frames] == ["op-1", "op-2", "op-3"]
