# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Apply to the fleet": the bulk action that brings every server to the settings the central wants.

The engine is the real one (plan, batches, a record per server) and so is the central's store;
the nodes are fakes that answer as a 4.1 node does and refuse what it refuses. What is pinned:
each setting reaches the servers by their own typed route, signed with the person who approved the
change and the change itself, and then recorded as the fleet's; a second look finds nothing to do;
what a server's own operator changed is left unless the central is told to put it back; what the
central no longer wants is let go and never deleted; an independent server is left out; a server
that cannot or will not take a setting says so in its own words, with no secret in them, and does
not stop the rest; and a server held below ``admin``, or on an older Noust, is skipped with the
reason.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings.desired import FleetSettings
from noust.web import fleet_jobs
from noust.web.fleet_jobs import FleetRequest, plan
from tests.support.fleet_actions import ADMIN, _run
from tests.support.fleet_settings_node import ManagedNode
from tests.support.fleet_views_support import Central, build_central

HELD = frozenset({"settings.manage", "server.manage", "backups.manage", "apps.manage"})
CHANNEL: dict[str, Any] = {
    "type": "telegram",
    "enabled": True,
    "settings": {"chat_id": "-100", "token": "bot:s3cret"},
}


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2", "web-3"))
    # Adding a server notes it for a first look: these tests set that note themselves.
    for name in built.nodes:
        FleetSettings(built.store, built.manager.secrets).clear_resync(name)
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


@pytest.fixture
def nodes(central: Central) -> dict[str, ManagedNode]:
    return {name: ManagedNode(node) for name, node in central.nodes.items()}


@pytest.fixture
def settings(central: Central) -> FleetSettings:
    return FleetSettings(central.store, central.manager.secrets)


def save(settings: FleetSettings, name: str = "ops", resource: str = "channel", **kw: Any) -> Any:
    return settings.save(
        resource,
        name,
        kw.pop("value", CHANNEL),
        scope=kw.pop("scope", "fleet"),
        person=kw.pop("person", "carol"),
        held=HELD,
    )


def apply(central: Central, *names: str, **options: Any) -> dict[str, Any]:
    request = FleetRequest("settings_apply", nodes=list(names) or ["web-1", "web-2", "web-3"])
    request.options = options
    snapshot, _recorder, _job = _run(central, request)
    return snapshot


def node_items(snapshot: dict[str, Any], node: str) -> list[dict[str, Any]]:
    return next(n for n in snapshot["nodes"] if n["node"] == node)["items"]


def states(snapshot: dict[str, Any], node: str) -> list[tuple[str, str, str]]:
    return [(i["resource"], i["name"], i["state"]) for i in node_items(snapshot, node)]


class TestPushingASetting:
    def test_each_server_gets_it_by_its_own_route_and_records_it_as_the_fleets(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        saved = save(settings)

        snapshot = apply(central)

        assert snapshot["status"] == "succeeded"
        for name, node in nodes.items():
            assert states(snapshot, name) == [("channel", "ops", "created")]
            assert node.held[("channel", "ops")] == CHANNEL
            row = node.rows[("channel", "ops")]
            assert (row["version"], row["state"], row["applied_by"]) == (
                saved.version,
                "managed",
                "carol",
            )

    def test_every_write_is_signed_with_the_person_and_the_change_and_never_as_the_one_pressing(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        saved = save(settings, person="carol")

        apply(central, "web-1")

        writes = nodes["web-1"].writes
        assert [(w.method, w.path) for w in writes] == [
            ("PUT", "/api/notifications/channels/ops"),
            ("PUT", "/api/managed-settings/channel/ops"),
        ]
        for write in writes:
            assert write.actor == f"fleet-sync on behalf of carol (change {saved.change_id})"
            assert write.elevated == f"change:{saved.change_id}"
            assert write.scope == "admin"
        assert nodes["web-1"].unsigned == 0

    def test_the_secret_reaches_the_node_and_nothing_the_central_keeps_of_the_job(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)

        snapshot = apply(central)

        assert nodes["web-1"].held[("channel", "ops")]["settings"]["token"] == "bot:s3cret"
        assert "s3cret" not in json.dumps(snapshot)
        stored = central.store._get_connection().execute("SELECT * FROM fleet_job_nodes").fetchall()
        assert "s3cret" not in repr([tuple(row) for row in stored])

    def test_a_second_look_finds_nothing_to_do(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)
        before = [len(node.writes) for node in nodes.values()]

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("channel", "ops", "in_sync")]
        assert [len(node.writes) for node in nodes.values()] == before

    def test_a_changed_setting_is_pushed_again_with_the_new_change(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)

        again = save(settings, value={**CHANNEL, "enabled": False}, person="dave")
        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("channel", "ops", "updated")]
        assert nodes["web-1"].held[("channel", "ops")]["enabled"] is False
        assert nodes["web-1"].writes[-1].actor.endswith(f"(change {again.change_id})")
        assert "dave" in nodes["web-1"].writes[-1].actor

    def test_several_kinds_go_each_by_its_own_route(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "REGION", "variable", value={"value": "eu"})
        save(
            settings,
            "ghcr",
            "registry",
            value={"kind": "ghcr", "username": "bot", "password": "tok"},
        )
        save(settings, "disk", "alert_rule", value={"metric": "disk", "threshold": 90})

        snapshot = apply(central, "web-1")

        assert states(snapshot, "web-1") == [
            ("alert_rule", "disk", "created"),
            ("registry", "ghcr", "created"),
            ("variable", "REGION", "created"),
        ]
        paths = {w.path for w in nodes["web-1"].writes}
        assert "/api/alerts/rules/disk" in paths
        assert "/api/registries" in paths  # a login the node lacks is made with a POST
        assert "/api/variables/server/REGION" in paths


class TestExceptionsAndIndependence:
    def test_a_server_with_an_exception_gets_its_own_value_and_the_others_the_fleets(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "REGION", "variable", value={"value": "eu"})
        save(settings, "REGION", "variable", value={"value": "us"}, scope="node:web-3")

        apply(central)

        assert nodes["web-1"].held[("variable", "REGION")]["value"] == "eu"
        assert nodes["web-3"].held[("variable", "REGION")]["value"] == "us"

    def test_taking_the_exception_away_puts_the_fleets_value_back_on_that_server(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "REGION", "variable", value={"value": "eu"})
        save(settings, "REGION", "variable", value={"value": "us"}, scope="node:web-3")
        apply(central)

        settings.remove("variable", "REGION", "node:web-3", person="carol", held=HELD)
        snapshot = apply(central)

        assert states(snapshot, "web-3") == [("variable", "REGION", "updated")]
        assert nodes["web-3"].held[("variable", "REGION")]["value"] == "eu"

    def test_an_independent_server_gets_nothing_and_is_let_go_of_what_it_had(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)

        settings.set_mode("web-2", "independent", person="carol")
        save(settings, value={**CHANNEL, "enabled": False})
        snapshot = apply(central)

        assert states(snapshot, "web-2") == [("channel", "ops", "released")]
        assert nodes["web-2"].rows == {}
        assert nodes["web-2"].held[("channel", "ops")]["enabled"] is True, (
            "its copy stays as it was"
        )
        assert nodes["web-1"].held[("channel", "ops")]["enabled"] is False


class TestWhatTheServerOwnerChanged:
    def test_it_is_left_as_its_operator_made_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)
        nodes["web-1"].edit_by_hand("channel", "ops", enabled=False)
        save(settings, value={**CHANNEL, "settings": {"chat_id": "-200", "token": "bot:s3cret"}})

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("channel", "ops", "overridden")]
        assert nodes["web-1"].held[("channel", "ops")]["enabled"] is False
        assert nodes["web-2"].held[("channel", "ops")]["settings"]["chat_id"] == "-200"

    def test_it_is_put_back_for_the_one_the_person_names(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)
        nodes["web-1"].edit_by_hand("channel", "ops", enabled=False)
        nodes["web-2"].edit_by_hand("channel", "ops", enabled=False)

        snapshot = apply(central, restore=["channel/ops"])

        assert states(snapshot, "web-1") == [("channel", "ops", "restored")]
        assert nodes["web-1"].rows[("channel", "ops")]["state"] == "managed"
        assert nodes["web-1"].held[("channel", "ops")]["enabled"] is True

    def test_it_is_put_back_for_a_server_when_the_option_names_everything(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)
        nodes["web-2"].edit_by_hand("channel", "ops", enabled=False)

        snapshot = apply(central, restore_all=True)

        assert states(snapshot, "web-2") == [("channel", "ops", "restored")]

    def test_it_is_put_back_when_the_servers_own_operator_asked(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)
        nodes["web-3"].edit_by_hand("channel", "ops", enabled=False)
        nodes["web-3"].ask_back("channel", "ops")

        snapshot = apply(central)

        assert states(snapshot, "web-3") == [("channel", "ops", "restored")]
        assert states(snapshot, "web-1") == [("channel", "ops", "in_sync")]


class TestWhatTheCentralNoLongerWants:
    def test_the_server_stops_treating_it_as_the_fleets_and_keeps_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        apply(central)

        settings.remove("channel", "ops", person="carol", held=HELD)
        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("channel", "ops", "released")]
        assert nodes["web-1"].rows == {}
        assert ("channel", "ops") in nodes["web-1"].held
        released = nodes["web-1"].writes[-1]
        assert (released.method, released.path) == ("DELETE", "/api/managed-settings/channel/ops")
        assert released.actor.startswith("fleet-sync on behalf of carol")

    def test_what_another_central_manages_is_left_alone(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        nodes["web-1"].hold("channel", "theirs")
        nodes["web-1"].rows[("channel", "theirs")] = {
            "resource": "channel",
            "name": "theirs",
            "central": "other",
            "version": 3,
            "state": "managed",
            "restore_requested_at": None,
        }

        snapshot = apply(central, "web-1")

        assert node_items(snapshot, "web-1") == []
        assert ("channel", "theirs") in nodes["web-1"].rows


class TestWhatCannotBeDone:
    def test_a_setting_that_exists_on_the_server_and_is_not_the_fleets_is_not_taken_over(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        nodes["web-1"].hold("channel", "ops", type="slack", enabled=False)

        snapshot = apply(central, "web-1", "web-2")

        item = node_items(snapshot, "web-1")[0]
        assert item["state"] == "conflict"
        assert item["coded"]["code"] == "fleet_settings.item_conflict"
        assert nodes["web-1"].held[("channel", "ops")]["type"] == "slack"
        assert nodes["web-1"].writes == []
        assert states(snapshot, "web-2") == [("channel", "ops", "created")]

    def test_it_is_taken_over_when_the_person_says_so(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        nodes["web-1"].hold("channel", "ops", type="slack", enabled=False)

        snapshot = apply(central, "web-1", adopt=True)

        assert states(snapshot, "web-1") == [("channel", "ops", "updated")]
        assert nodes["web-1"].rows[("channel", "ops")]["state"] == "managed"

    def test_a_server_that_refuses_says_so_in_its_own_words_without_the_secret(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        save(settings, "REGION", "variable", value={"value": "eu"})
        nodes["web-2"].refusals[("PUT", "/api/notifications/channels/ops")] = {
            "error": "configerror",
            "detail": "Invalid token bot:s3cret for telegram",
            "hint": "A token looks like 123:ABC",
        }

        snapshot = apply(central)

        failed = next(i for i in node_items(snapshot, "web-2") if i["name"] == "ops")
        assert failed["state"] == "failed"
        assert "Invalid token" in json.dumps(failed)
        assert "s3cret" not in json.dumps(snapshot)
        assert ("channel", "ops") not in nodes["web-2"].rows
        # The rest of that server's settings, and the other servers, went on.
        assert ("variable", "REGION") in nodes["web-2"].rows
        assert states(snapshot, "web-1")[0][2] == "created"
        web2 = next(n for n in snapshot["nodes"] if n["node"] == "web-2")
        assert web2["state"] == "failed"
        assert snapshot["status"] == "failed"

    def test_a_kind_the_server_cannot_take_is_said_and_the_rest_goes_on(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "REGION", "variable", value={"value": "eu"})
        save(settings, "ghcr", "registry", value={"kind": "ghcr", "username": "b", "password": "p"})
        central.nodes["web-1"].schema.pop("/api/registries")

        snapshot = apply(central, "web-1")

        assert states(snapshot, "web-1") == [
            ("registry", "ghcr", "unsupported"),
            ("variable", "REGION", "created"),
        ]
        item = node_items(snapshot, "web-1")[0]
        assert item["coded"]["code"] == "fleet_settings.item_unsupported"

    def test_a_server_held_below_admin_is_skipped_and_the_plan_says_why(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        central.store.set_node_access("web-2", "deploy", False)

        the_plan = plan(
            FleetRequest("settings_apply", nodes=["web-1", "web-2"]),
            manager=central.manager,
            asker=ADMIN,
        )

        skipped = {n.node: (n.state, n.reason) for n in the_plan.nodes}
        assert skipped == {"web-1": ("queued", None), "web-2": ("skipped", "policy")}

    def test_a_server_on_an_older_noust_is_skipped_as_unsupported(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        central.nodes["web-3"].schema.pop("/api/managed-settings")

        the_plan = plan(
            FleetRequest("settings_apply", nodes=["web-3"]), manager=central.manager, asker=ADMIN
        )

        assert [(n.state, n.reason) for n in the_plan.nodes] == [("skipped", "unsupported")]

    def test_the_action_asks_no_sudo_mode_of_the_person_pressing_the_button(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)

        the_plan = plan(
            FleetRequest("settings_apply", nodes=["web-1"]), manager=central.manager, asker=ADMIN
        )

        assert the_plan.requires_elevation is False


class TestTheNoteOfASeverThatCameBack:
    def test_a_server_done_with_is_no_longer_waiting_for_a_look(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        settings.request_resync("web-1")
        settings.request_resync("web-2")

        apply(central, "web-1")

        assert settings.pending_resync() == ["web-2"]

    def test_a_server_that_failed_keeps_waiting(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings)
        settings.request_resync("web-2")
        nodes["web-2"].refusals[("PUT", "/api/notifications/channels/ops")] = {"detail": "no"}

        apply(central, "web-2")

        assert settings.pending_resync() == ["web-2"]


def test_the_action_is_in_the_registry_with_the_routes_it_uses() -> None:
    spec = fleet_jobs.all_actions()["settings_apply"]

    assert ("PUT", "/api/managed-settings/{resource}/{name}") in spec.operations
    assert spec.max_failures is None, "one server that refuses must not stop the others"
