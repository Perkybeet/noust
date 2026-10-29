# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The central's address in the links of every server's notifications, as a setting of the fleet.

A central that manages a server sets ``notifications.console_url`` on it (so the server's links
open its pages inside the central's console). It is a setting of the fleet like a channel: saved
once, applied to every server by the server's own route, followed by the server unless it has an
exception or is independent, and put back when its operator changed it. What differs is that the
address the central saves is its own and each server gets it with its own name in it
(``https://central/n/web-2``), which only the central knows. What is pinned: the kind and its one
name, the address each server is sent, how a server's holding of it is read and written, and the
reconciliation end to end against fake servers.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.exceptions import ValidationError
from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings import snapshots
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.kinds import AVAILABLE, kind_of
from noust.fleet.settings.push import find, push
from tests.support.fleet_settings_node import ManagedNode
from tests.support.fleet_views_support import Central, build_central
from tests.test_fleet_settings_actions import apply, node_items, save, states

CENTRAL = "https://central.example.com"


class TestTheKind:
    def test_it_is_one_of_the_kinds_the_fleet_manages(self) -> None:
        assert "console_link" in AVAILABLE
        kind = kind_of("console_link")
        assert kind.permission == "settings.manage"
        assert ("PUT", "/api/notifications/console-url") in kind.operations

    def test_there_is_one_and_it_has_one_name(self) -> None:
        kind = kind_of("console_link")

        assert kind.check_name("console") == "console"
        with pytest.raises(ValidationError) as caught:
            kind.check_name("main")
        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.single_name"

    def test_the_value_is_the_address_and_nothing_else(self) -> None:
        kind = kind_of("console_link")

        assert kind.check_value({"url": CENTRAL}) == {"url": CENTRAL}
        for bad in ({}, {"url": CENTRAL, "label": "x"}):
            with pytest.raises(ValidationError):
                kind.check_value(bad)
        with pytest.raises(ValidationError) as caught:
            kind.check_value({"url": 5})
        assert caught.value.coded is not None
        assert caught.value.coded.code == "fleet_settings.field_not_text"

    def test_it_holds_no_credential(self) -> None:
        assert kind_of("console_link").secret_paths("console", {"url": CENTRAL}) == ()


class TestTheAddressEachServerIsSent:
    @pytest.mark.parametrize(
        ("given", "node", "sent"),
        [
            (CENTRAL, "web-2", f"{CENTRAL}/n/web-2"),
            (CENTRAL + "/", "web-2", f"{CENTRAL}/n/web-2"),
            (CENTRAL + "/noust", "db-1", f"{CENTRAL}/noust/n/db-1"),
        ],
    )
    def test_it_names_the_server_inside_the_centrals_console(
        self, given: str, node: str, sent: str
    ) -> None:
        assert kind_of("console_link").for_node({"url": given}, node) == {"url": sent}

    def test_the_other_kinds_are_sent_as_they_are(self) -> None:
        value = {"type": "slack"}
        assert kind_of("channel").for_node(value, "web-2") == value

    def test_the_stored_value_is_not_changed(self) -> None:
        value = {"url": CENTRAL}

        kind_of("console_link").for_node(value, "web-2")

        assert value == {"url": CENTRAL}


class TestWhatAServerHolds:
    def test_it_reads_the_address_from_the_configuration(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from noust.core.config import Config

        monkeypatch.setattr("noust.core.config.DEFAULT_CONFIG_PATH", tmp_path / "config.yaml")
        Config.reset_instance()
        try:
            assert snapshots.take("console_link", "console") is None
            config = Config()
            config.set("notifications.console_url", f"{CENTRAL}/n/web-2")
            config.write()
            assert snapshots.take("console_link", "console") == {"url": f"{CENTRAL}/n/web-2"}
        finally:
            Config.reset_instance()


class FakeNode:
    def __init__(self, url: str = "") -> None:
        self.url = url
        self.calls: list[tuple[str, str, Any]] = []

    def call(
        self, method: str, path: str, *, body: Any = None, params: Any = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, body))
        if method == "PUT":
            self.url = body["url"]
        return {"url": self.url}


class TestWritingIt:
    def test_a_server_without_one_has_none(self) -> None:
        assert find(FakeNode(), "console_link", "console") is None

    def test_a_server_with_one_is_found(self) -> None:
        found = find(FakeNode(f"{CENTRAL}/n/web-2"), "console_link", "console")

        assert found is not None and found["url"] == f"{CENTRAL}/n/web-2"

    def test_it_is_written_by_the_servers_own_route(self) -> None:
        node = FakeNode()

        how = push(node, "console_link", "console", {"url": f"{CENTRAL}/n/web-2"}, None)

        assert how == "created"
        assert node.calls[-1] == (
            "PUT",
            "/api/notifications/console-url",
            {"url": f"{CENTRAL}/n/web-2"},
        )

    def test_changing_it_is_an_update(self) -> None:
        node = FakeNode(f"{CENTRAL}/n/web-2")

        found = find(node, "console_link", "console")
        how = push(
            node, "console_link", "console", {"url": "https://other.example.com/n/web-2"}, found
        )

        assert how == "updated"


HELD = frozenset({"settings.manage"})


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2"))
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


class TestTheReconciliation:
    def test_each_server_gets_the_address_with_its_own_name_in_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "console", "console_link", value={"url": CENTRAL})

        snapshot = apply(central, "web-1", "web-2")

        assert snapshot["status"] == "succeeded"
        assert nodes["web-1"].held[("console_link", "console")] == {"url": f"{CENTRAL}/n/web-1"}
        assert nodes["web-2"].held[("console_link", "console")] == {"url": f"{CENTRAL}/n/web-2"}
        assert states(snapshot, "web-1") == [("console_link", "console", "created")]

    def test_the_write_is_signed_like_every_other_push(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        saved = save(settings, "console", "console_link", value={"url": CENTRAL}, person="carol")

        apply(central, "web-1")

        writes = nodes["web-1"].writes
        assert [(w.method, w.path) for w in writes] == [
            ("PUT", "/api/notifications/console-url"),
            ("PUT", "/api/managed-settings/console_link/console"),
        ]
        assert writes[0].actor == f"fleet-sync on behalf of carol (change {saved.change_id})"
        assert nodes["web-1"].unsigned == 0

    def test_a_second_look_finds_it_in_sync(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "console", "console_link", value={"url": CENTRAL})
        apply(central, "web-1", "web-2")

        snapshot = apply(central, "web-1", "web-2")

        assert states(snapshot, "web-2") == [("console_link", "console", "in_sync")]

    def test_a_server_may_have_an_address_of_its_own_as_an_exception(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "console", "console_link", value={"url": CENTRAL})
        save(
            settings,
            "console",
            "console_link",
            value={"url": "https://eu.example.com"},
            scope="node:web-2",
        )

        apply(central, "web-1", "web-2")

        assert nodes["web-1"].held[("console_link", "console")] == {"url": f"{CENTRAL}/n/web-1"}
        assert nodes["web-2"].held[("console_link", "console")] == {
            "url": "https://eu.example.com/n/web-2"
        }

    def test_an_address_the_server_already_had_is_not_taken_over_unasked(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        nodes["web-1"].hold("console_link", "console", url="https://web-1.example.com")
        save(settings, "console", "console_link", value={"url": CENTRAL})

        snapshot = apply(central, "web-1")

        item = node_items(snapshot, "web-1")[0]
        assert item["state"] == "conflict"
        assert (
            nodes["web-1"].held[("console_link", "console")]["url"] == "https://web-1.example.com"
        )

    def test_a_server_that_refuses_the_address_says_so_in_its_own_words(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "console", "console_link", value={"url": "http://central.example.com"})
        nodes["web-1"].refusals[("PUT", "/api/notifications/console-url")] = {
            "error": "configerror",
            "detail": "notifications.console_url must be an absolute https:// URL",
        }

        snapshot = apply(central, "web-1")

        item = node_items(snapshot, "web-1")[0]
        assert item["state"] == "failed"
        assert "notifications.console_url must be an absolute https:// URL" in item["message"]
        assert ("console_link", "console") not in nodes["web-1"].held


def test_the_status_lists_it_like_any_setting(
    central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
) -> None:
    from noust.fleet.settings.status import read
    from tests.support.fleet_actions import ADMIN

    saved = save(settings, "console", "console_link", value={"url": CENTRAL})
    apply(central, "web-1", "web-2")

    view = read(settings, asker=ADMIN, manager=central.manager)

    entry = next(s for s in view["settings"] if s["resource"] == "console_link")
    assert entry["version"] == saved.version
    assert {c["node"]: c["sync"] for c in entry["servers"]} == {
        "web-1": "in_sync",
        "web-2": "in_sync",
    }
