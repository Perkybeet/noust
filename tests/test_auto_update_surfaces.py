# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The automatic update's two doors, ``noust self-update`` and ``/api/system/update/auto``: they
translate to the manager (tests/test_auto_update.py) and nothing else.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from noust.cli.app import cli as root_cli
from noust.core import audit
from noust.managers import auto_update
from noust.managers.self_update import SelfUpdateRefused
from tests.test_auto_update import (  # noqa: F401 - the fixture
    FakeUpdater,
    Harness,
    check,
    config_path,
)

pytestmark = pytest.mark.usefixtures("config_path")


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Harness:
    harness = Harness(tmp_path, found=check(installable="3.4.1"))
    monkeypatch.setattr("noust.cli.commands.self_update.AutoUpdate", lambda: harness.manager)
    monkeypatch.setattr("noust.cli.commands.self_update.check_root", lambda: True)
    monkeypatch.setattr(auto_update, "AutoUpdate", lambda: harness.manager)
    return harness


def run(*args: str):
    return CliRunner().invoke(root_cli, ["self-update", *args])


class TestCommandLine:
    def test_says_how_to_turn_it_on_while_it_is_off(self, machine: Harness) -> None:
        result = run()
        assert result.exit_code == 0, result.output
        assert "does not install Noust's new releases by itself" in result.output
        assert "noust self-update set --enable" in result.output

    def test_turns_it_on_with_its_limits_and_says_them(self, machine: Harness) -> None:
        result = run(
            "set", "--enable", "--channel", "security", "--window", "01:00-02:30", "--days", "sun"
        )
        assert result.exit_code == 0, result.output
        assert "security releases between 01:00-02:30, sun" in result.output
        assert machine.manager.settings().to_dict() == {
            "enabled": True,
            "channel": "security",
            "window": "01:00-02:30",
            "days": "sun",
        }
        assert machine.scheduler.installed[0]["calendar"] == "Sun *-*-* 01,02:00/10:00"

    def test_shows_the_settings_and_when_the_window_next_opens(self, machine: Harness) -> None:
        run("set", "--enable")
        result = run("show")
        assert "Installing patch releases between 03:00-05:00 (daily)" in result.output
        assert "The window next opens 2026-10-14T03:30:00" in result.output

    def test_refuses_a_window_that_is_not_one_and_changes_nothing(self, machine: Harness) -> None:
        result = run("set", "--enable", "--window", "tonight")
        assert result.exit_code != 0
        assert "not a window" in str(result.exception) or "not a window" in result.output
        assert not machine.manager.settings().enabled

    def test_refuses_without_root(self, machine: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("noust.cli.commands.self_update.check_root", lambda: False)
        result = run("set", "--enable")
        assert result.exit_code != 0
        assert "needs root" in str(result.exception)

    def test_the_timers_own_command_obeys_the_window_and_a_persons_does_not(
        self, machine: Harness
    ) -> None:
        run("set", "--enable")
        machine.now = Harness(Path("."), now="14T12:00").now  # noon: the window is closed
        scheduled = run("run", "--scheduled", "--json")
        by_hand = run("run", "--json")

        assert json.loads(scheduled.output)["reason"] == "outside_window"
        assert json.loads(by_hand.output)["outcome"] == "updated"

    def test_a_failed_update_leaves_with_a_failure_status_and_the_tools_words(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        refusal = SelfUpdateRefused(
            "preflight_failed",
            "Only 80 MB are free; nothing was installed",
            "Free some space.",
            output="df says 80M",
        )
        harness = Harness(
            tmp_path, found=check(installable="3.4.1"), updater=FakeUpdater(start_refusal=refusal)
        )
        monkeypatch.setattr("noust.cli.commands.self_update.AutoUpdate", lambda: harness.manager)
        monkeypatch.setattr("noust.cli.commands.self_update.check_root", lambda: True)
        harness.turn_on()

        result = run("run", "--scheduled")

        assert result.exit_code == 1
        assert "Only 80 MB are free" in result.output
        assert "df says 80M" in result.output

    def test_what_the_timer_runs_is_a_command_that_exists_and_is_audited(
        self, machine: Harness
    ) -> None:
        run("set", "--enable", "--reason", "weekly patches")
        events = [event["action"] for event in audit.get_log().read()]
        assert "cli.command.start" in events


class TestApi:
    @pytest.fixture
    def client(self, machine: Harness) -> TestClient:
        from noust.web.api import system
        from noust.web.api.auth import get_current_session
        from noust.web.api.deps import require_elevated

        app = FastAPI()
        app.include_router(system.router, prefix="/api/system")
        app.dependency_overrides[get_current_session] = lambda: {"type": "session"}
        app.dependency_overrides[require_elevated] = lambda: {"type": "session"}
        return TestClient(app)

    def test_says_the_settings_and_what_the_timer_and_the_last_run_are(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/system/update/auto").json()
        assert body["settings"] == {
            "enabled": False,
            "channel": "patch",
            "window": "03:00-05:00",
            "days": "daily",
        }
        assert body["supported"] is True
        assert body["last_run"] is None
        assert body["timer"]["installed"] is False

    def test_changes_the_limits_and_audits_the_change(self, client: TestClient) -> None:
        response = client.put(
            "/api/system/update/auto",
            json={"enabled": True, "channel": "stable", "window": "02:00-04:00", "days": "mon,thu"},
        )

        assert response.status_code == 200, response.text
        assert response.json()["settings"] == {
            "enabled": True,
            "channel": "stable",
            "window": "02:00-04:00",
            "days": "mon,thu",
        }
        assert [
            event
            for event in audit.get_log().read()
            if event["action"] == "system.auto_update_settings"
        ]

    def test_leaves_alone_what_the_body_does_not_name(self, client: TestClient) -> None:
        client.put("/api/system/update/auto", json={"enabled": True, "channel": "security"})
        body = client.put("/api/system/update/auto", json={"window": "04:00-05:00"}).json()
        assert body["settings"]["channel"] == "security"
        assert body["settings"]["window"] == "04:00-05:00"

    @pytest.mark.parametrize("body", [{"window": "soon"}, {"channel": "next"}, {"days": "funday"}])
    def test_refuses_a_value_the_rules_refuse(
        self, client: TestClient, body: dict[str, str]
    ) -> None:
        response = client.put("/api/system/update/auto", json=body)
        assert response.status_code in (400, 422)
        assert client.get("/api/system/update/auto").json()["settings"]["enabled"] is False

    def test_its_permissions_are_mapped(self) -> None:
        from noust.web.permissions.routes_system import ROUTES

        assert ROUTES[("GET", "/api/system/update/auto")] == "server.read"
        assert ROUTES[("PUT", "/api/system/update/auto")] == "server.manage"
