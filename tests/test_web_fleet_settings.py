# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
``/api/fleet/settings`` over HTTP, on the real application.

The central is the real application with a signed-in operator; the nodes are fakes that answer as
a 4.1 node does. What is pinned: who may set what (sudo mode, and the permission of the thing),
what comes back (a secret never), that saving starts the job that applies it and says which, the
screen's data - the kinds, every setting in every scope, where each server stands - and the
errors with their codes: an unknown or not-yet kind, a value that is not the shape of one, a
server that is not in the fleet.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from noust.core.config import Config
from noust.core.runner import FakeRunner
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings.desired import FleetSettings
from noust.web.jobs import JobManager
from noust.web.server import create_app, get_token_manager
from tests.support.fleet_settings_node import ManagedNode
from tests.support.fleet_views_support import Central, build_central
from tests.support.web_auth import make_config
from tests.support.web_central import elevate, sign_in

CHANNEL: dict[str, Any] = {
    "type": "telegram",
    "enabled": True,
    "settings": {"chat_id": "-100", "token": "bot:s3cret"},
}
URL = "/api/fleet/settings"


@pytest.fixture
def central(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Central]:
    monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.delenv("NOUST_CENTRAL_ROLE", raising=False)
    Config.reset_instance()
    built = build_central(tmp_path / "fleet", ("web-2", "web-3"))
    for name in built.nodes:
        FleetSettings(built.store, built.manager.secrets).clear_resync(name)
    monkeypatch.setattr("noust.fleet.nodes.NodeManager", lambda *a, **k: built.manager)
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
def nodes(central: Central) -> dict[str, ManagedNode]:
    return {name: ManagedNode(node) for name, node in central.nodes.items()}


@pytest.fixture
def client(central: Central, nodes: Any, sandbox: Path, runner: FakeRunner) -> TestClient:
    return TestClient(create_app(make_config(sandbox)), client=("testclient", 50000))


@pytest.fixture
def operator(client: TestClient) -> TestClient:
    master = sign_in(client)
    elevate(client, master)
    return client


def wait(client: TestClient, job_id: str) -> dict[str, Any]:
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def set_channel(client: TestClient, name: str = "ops", **body: Any) -> Any:
    return client.put(f"{URL}/channel/{name}", json={"value": CHANNEL, **body})


class TestTheKindsAndWhatIsSet:
    def test_the_kinds_say_which_the_central_manages_and_why_the_rest_do_not_yet(
        self, operator: TestClient
    ) -> None:
        body = operator.get(URL).json()

        kinds = {k["key"]: k for k in body["kinds"]}
        assert [k for k, v in kinds.items() if v["available"]] == [
            "channel",
            "alert_rule",
            "backup_destination",
            "registry",
            "variable",
        ]
        assert kinds["channel"]["permission"] == "settings.manage"
        assert "PUT /api/notifications/channels/{name}" in kinds["channel"]["operations"]
        for key in ("secret_manager", "dns_provider", "source_integration"):
            assert kinds[key]["available"] is False
            assert kinds[key]["reason"]["code"] == "fleet_settings.kind_not_yet"
        assert body["items"] == []

    def test_a_setting_is_shown_with_its_secret_hidden_in_every_way_it_can_be_read(
        self, operator: TestClient
    ) -> None:
        saved = set_channel(operator).json()["setting"]

        for body in (
            operator.get(URL).json(),
            operator.get(f"{URL}/channel").json(),
            operator.get(f"{URL}/channel/ops").json(),
        ):
            assert "s3cret" not in str(body)
            item = body["items"][0]
            assert item["value"]["settings"]["token"] == "***"
            assert item["secrets"] == ["settings.token"]
            assert item["version"] == saved["version"]

    def test_a_setting_that_is_not_there_is_a_not_found_with_its_sentence(
        self, operator: TestClient
    ) -> None:
        response = operator.get(f"{URL}/channel/ghost")

        assert response.status_code == 404
        assert response.json()["coded"]["code"] == "fleet_settings.no_such_setting"


class TestSetting:
    def test_setting_one_applies_it_and_says_which_job_does(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        response = set_channel(operator)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["setting"]["scope"] == "fleet"
        person = body["setting"]["changed_by"]
        assert person and person != "unknown"
        assert body["apply"]["nodes"] == ["web-2", "web-3"]
        job = wait(operator, body["apply"]["job_id"])
        assert job["status"] == "completed", job
        for node in nodes.values():
            assert node.held[("channel", "ops")] == CHANNEL
            row = node.rows[("channel", "ops")]
            assert (row["version"], row["applied_by"]) == (body["setting"]["version"], person)

    def test_the_progress_of_each_server_is_the_fleet_job(self, operator: TestClient) -> None:
        body = set_channel(operator).json()
        wait(operator, body["apply"]["job_id"])

        job = operator.get(f"/api/fleet/jobs/{body['apply']['job_id']}").json()

        assert job["action"] == "settings_apply"
        assert job["status"] == "succeeded"
        assert {n["node"]: n["state"] for n in job["nodes"]} == {
            "web-2": "succeeded",
            "web-3": "succeeded",
        }
        assert job["nodes"][0]["items"][0]["state"] == "created"
        assert "s3cret" not in str(job)

    def test_it_can_be_set_without_applying_it(self, operator: TestClient) -> None:
        body = set_channel(operator, apply=False).json()

        assert body["apply"] is None

    def test_an_exception_is_applied_to_its_server_only(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        body = operator.put(
            f"{URL}/variable/REGION", json={"value": {"value": "us"}, "scope": "node:web-3"}
        ).json()

        assert body["apply"]["nodes"] == ["web-3"]
        wait(operator, body["apply"]["job_id"])
        assert ("variable", "REGION") in nodes["web-3"].held
        assert ("variable", "REGION") not in nodes["web-2"].held

    def test_sudo_mode_is_asked(self, client: TestClient) -> None:
        sign_in(client)

        response = set_channel(client)

        assert response.status_code == 403
        assert response.json()["error"] == "elevation_required"

    def test_a_credential_that_only_reads_cannot_set_one(self, client: TestClient) -> None:
        issued = get_token_manager().create_api_token("reader", "read")
        headers = {"Authorization": f"Bearer {issued['token']}"}

        assert client.get(URL, headers=headers).status_code == 200
        response = client.put(f"{URL}/channel/ops", json={"value": CHANNEL}, headers=headers)
        assert response.status_code == 403

    def test_the_permission_of_the_thing_is_the_managers_to_ask(
        self, operator: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Whoever manages the fleet but does not manage backups does not thereby manage the
        # backup destinations of every server: the route hands the manager what the person holds.
        monkeypatch.setattr(
            "noust.web.api.fleet_settings.permissions_of",
            lambda session: frozenset({"fleet.manage", "fleet.read"}),
        )

        response = operator.put(
            f"{URL}/backup_destination/nas", json={"value": {"backend": "s3", "fields": {}}}
        )

        assert response.status_code == 403
        assert response.json()["coded"]["code"] == "fleet_settings.permission_missing"

    @pytest.mark.parametrize(
        ("path", "body", "code", "field"),
        [
            ("channel/Bad%20Name", {"value": CHANNEL}, None, "name"),
            ("channel/ops", {"value": {"enabled": True}}, "fleet_settings.missing_field", "type"),
            (
                "channel/ops",
                {"value": {**CHANNEL, "colour": "red"}},
                "fleet_settings.unknown_field",
                "colour",
            ),
            (
                "channel/ops",
                {"value": CHANNEL, "scope": "all"},
                "fleet_settings.bad_scope",
                "scope",
            ),
            (
                "channel/ops",
                {"value": CHANNEL, "scope": "node:web-9"},
                "fleet_settings.unknown_node",
                "scope",
            ),
            ("firewall/x", {"value": {}}, "fleet_settings.unknown_kind", "resource"),
            ("dns_provider/x", {"value": {}}, "fleet_settings.kind_not_yet", "resource"),
        ],
    )
    def test_a_setting_that_cannot_be_set_says_which_field_and_why(
        self, operator: TestClient, path: str, body: dict[str, Any], code: str | None, field: str
    ) -> None:
        response = operator.put(f"{URL}/{path}", json=body)

        assert response.status_code == 400, response.text
        answer = response.json()
        assert field in (answer["fields"] or {})
        if code is not None:
            assert answer["coded"]["code"] == code

    def test_the_audit_trail_says_what_changed_and_never_a_value(
        self, operator: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[dict[str, Any]] = []
        monkeypatch.setattr(
            "noust.core.audit.record", lambda event, **kw: events.append({"event": event, **kw})
        )

        set_channel(operator, apply=False)

        recorded = [e for e in events if e["event"] == "fleet.setting.set"]
        assert len(recorded) == 1
        assert recorded[0]["target"] == "channel:ops"
        assert recorded[0]["details"]["secrets"] == 1
        assert "s3cret" not in str(recorded[0])


class TestRemoving:
    def test_taking_it_away_lets_the_servers_go_of_it_and_they_keep_it(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        wait(operator, set_channel(operator).json()["apply"]["job_id"])

        body = operator.delete(f"{URL}/channel/ops").json()

        assert body["removed"] is True
        wait(operator, body["apply"]["job_id"])
        for node in nodes.values():
            assert node.rows == {}
            assert ("channel", "ops") in node.held

    def test_taking_away_what_is_not_there_is_not_an_error(self, operator: TestClient) -> None:
        assert operator.delete(f"{URL}/channel/ghost").json() == {"removed": False, "apply": None}

    def test_an_exception_is_taken_away_alone(self, operator: TestClient) -> None:
        operator.put(f"{URL}/variable/REGION", json={"value": {"value": "eu"}, "apply": False})
        operator.put(
            f"{URL}/variable/REGION",
            json={"value": {"value": "us"}, "scope": "node:web-3", "apply": False},
        )

        body = operator.delete(
            f"{URL}/variable/REGION", params={"scope": "node:web-3", "apply": "false"}
        ).json()

        assert body["removed"] is True
        remaining = operator.get(f"{URL}/variable/REGION").json()["items"]
        assert [i["scope"] for i in remaining] == ["fleet"]


class TestWhereEachServerStands:
    def test_the_status_says_it_for_every_setting_and_server(
        self, operator: TestClient, central: Central
    ) -> None:
        wait(operator, set_channel(operator).json()["apply"]["job_id"])
        central.nodes["web-3"].down = True

        body = operator.get(f"{URL}/status").json()

        assert body["partial"] is True
        cells = {c["node"]: c for c in body["settings"][0]["servers"]}
        assert (cells["web-2"]["origin"], cells["web-2"]["sync"]) == ("fleet", "in_sync")
        assert cells["web-3"]["sync"] == "unreachable"
        servers = {s["node"]: s for s in body["servers"]}
        assert servers["web-3"]["outcome"] == "unreachable"
        assert servers["web-2"]["mode"] == "managed"

    def test_what_a_servers_operator_changed_shows_as_its_own_exception(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        wait(operator, set_channel(operator).json()["apply"]["job_id"])
        nodes["web-2"].edit_by_hand("channel", "ops", enabled=False)

        body = operator.get(f"{URL}/status").json()

        cells = {c["node"]: c for c in body["settings"][0]["servers"]}
        assert (cells["web-2"]["origin"], cells["web-2"]["sync"]) == (
            "local_exception",
            "diverged",
        )


class TestAServerThatLeavesTheFleetsSettings:
    def test_it_is_made_independent_and_let_go_of_what_it_had(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        wait(operator, set_channel(operator).json()["apply"]["job_id"])

        body = operator.put(
            "/api/fleet/servers/web-2/settings-mode", json={"mode": "independent"}
        ).json()

        assert (body["node"], body["mode"]) == ("web-2", "independent")
        wait(operator, body["apply"]["job_id"])
        assert nodes["web-2"].rows == {}
        assert ("channel", "ops") in nodes["web-2"].held
        status = operator.get(f"{URL}/status").json()
        assert {s["node"]: s["mode"] for s in status["servers"]}["web-2"] == "independent"

    def test_a_mode_that_is_not_one_and_a_server_that_is_not_there_are_refused(
        self, operator: TestClient
    ) -> None:
        assert (
            operator.put(
                "/api/fleet/servers/web-2/settings-mode", json={"mode": "paused"}
            ).status_code
            == 400
        )
        refused = operator.put(
            "/api/fleet/servers/web-9/settings-mode", json={"mode": "independent"}
        )
        assert refused.status_code == 400


class TestApplyingToTheFleet:
    def test_it_is_planned_before_it_runs(self, operator: TestClient) -> None:
        set_channel(operator, apply=False)

        body = operator.post(f"{URL}/apply", json={"plan": True}).json()

        assert body["job"] is None
        assert body["plan"]["action"] == "settings_apply"
        assert [n["node"] for n in body["plan"]["nodes"]] == ["web-2", "web-3"]
        assert body["plan"]["requires_elevation"] is False

    def test_it_runs_on_every_server_and_the_job_says_how_each_went(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        set_channel(operator, apply=False)

        body = operator.post(f"{URL}/apply", json={}).json()

        wait(operator, body["job"]["job_id"])
        assert all(("channel", "ops") in n.rows for n in nodes.values())

    def test_it_puts_back_what_it_is_told_to(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        wait(operator, set_channel(operator).json()["apply"]["job_id"])
        nodes["web-2"].edit_by_hand("channel", "ops", enabled=False)

        body = operator.post(
            f"{URL}/apply", json={"nodes": ["web-2"], "restore": ["channel/ops"]}
        ).json()

        wait(operator, body["job"]["job_id"])
        assert nodes["web-2"].held[("channel", "ops")]["enabled"] is True

    def test_taking_over_or_putting_back_asks_sudo_mode_and_plain_applying_does_not(
        self, client: TestClient
    ) -> None:
        sign_in(client)
        set_channel_without_sudo = client.post(f"{URL}/apply", json={"plan": True})
        assert set_channel_without_sudo.status_code == 200

        for option in ({"adopt": True}, {"restore": ["channel/ops"]}, {"restore_all": True}):
            response = client.post(f"{URL}/apply", json=option)
            assert response.status_code == 403, option
            assert response.json()["error"] == "elevation_required"
        # Only planning what they would do asks nothing.
        assert client.post(f"{URL}/apply", json={"adopt": True, "plan": True}).status_code == 200

    def test_a_bad_option_or_server_is_refused(self, operator: TestClient) -> None:
        assert operator.post(f"{URL}/apply", json={"nodes": ["web-9"]}).status_code == 400
        assert operator.post(f"{URL}/apply", json={"restore": ["nope"]}).status_code == 400

    def test_putting_one_back_is_one_call(
        self, operator: TestClient, nodes: dict[str, ManagedNode]
    ) -> None:
        wait(operator, set_channel(operator).json()["apply"]["job_id"])
        nodes["web-3"].edit_by_hand("channel", "ops", enabled=False)

        body = operator.post("/api/fleet/servers/web-3/settings/channel/ops/restore").json()

        assert body["nodes"] == ["web-3"]
        wait(operator, body["job_id"])
        assert nodes["web-3"].rows[("channel", "ops")]["state"] == "managed"

    def test_putting_back_what_the_fleet_does_not_give_that_server_is_a_not_found(
        self, operator: TestClient
    ) -> None:
        response = operator.post("/api/fleet/servers/web-3/settings/channel/ops/restore")

        assert response.status_code == 404
        assert response.json()["coded"]["code"] == "fleet_settings.no_such_setting"
