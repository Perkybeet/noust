# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
"Apply to the fleet" for a channel that has a route: it is sent only where the route can match.

The engine, the central's store and the nodes are the ones of ``test_fleet_settings_actions``. What
is pinned: a channel naming servers is sent to those and to no other; one naming domains or tags is
sent to the servers where an application has one, decided from the applications each server lists;
nothing at all goes to a server that cannot match, so its credential does not travel there; what
the server already holds of it is left alone; the list of servers is the central's and never
reaches a server; a route that was narrowed away on the central is cleared on a server that had
it; and when the applications of a server cannot be read, nothing is sent and the record says why.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import set_aggregator
from noust.fleet.settings.desired import FleetSettings
from tests.support.fleet_settings_actions_support import CHANNEL, apply, node_items, save, states
from tests.support.fleet_settings_node import ManagedNode
from tests.support.fleet_views_support import Central, build_central

SHOP = {"domain": "shop.example.com", "tags": ["prod"]}
BLOG = {"domain": "blog.example.org", "tags": ["staging"]}


def routed(**route: Any) -> dict[str, Any]:
    return {**CHANNEL, "route": route}


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-1", "web-2", "web-3"))
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


def asked_apps(central: Central, node: str) -> int:
    return sum(1 for r in central.nodes[node].requests if r.url.path == "/api/apps")


class TestServers:
    def test_a_channel_is_sent_to_the_servers_it_names_and_to_no_other(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=routed(servers=["web-1", "web-2"], min_severity="failed"))

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("channel", "ops", "created")]
        assert states(snapshot, "web-2") == [("channel", "ops", "created")]
        assert states(snapshot, "web-3") == [("channel", "ops", "out_of_reach")]
        assert snapshot["status"] == "succeeded"

    def test_nothing_is_sent_to_a_server_that_cannot_match_so_its_credential_stays_home(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=routed(servers=["web-1"]))

        snapshot = apply(central)

        assert nodes["web-3"].writes == []
        assert nodes["web-3"].held == {}
        assert nodes["web-3"].rows == {}
        assert "s3cret" not in json.dumps(
            [r.content.decode() for r in central.nodes["web-3"].requests if r.content]
        )
        item = node_items(snapshot, "web-3")[0]
        assert item["why"] == "server"
        assert item["coded"]["code"] == "fleet_settings.out_of_reach.server"
        assert item["coded"]["params"] == {"kind": "channel", "name": "ops"}

    def test_a_pattern_names_the_servers_of_a_kind(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=routed(servers=["*-1", "db-*", "web-3"]))

        # fnmatch, as a domain pattern is: a prefix, a suffix and a whole name all work.
        snapshot = apply(central)

        assert [states(snapshot, n)[0][2] for n in ("web-1", "web-2", "web-3")] == [
            "created",
            "out_of_reach",
            "created",
        ]

    def test_the_list_of_servers_stays_on_the_central_and_the_rest_of_the_route_goes(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        central.nodes["web-1"].apps = [SHOP]
        save(settings, value=routed(servers=["web-1"], min_severity="failed", apps=["*"]))

        apply(central, "web-1")

        sent = nodes["web-1"].writes[0].body
        assert sent["route"] == {"min_severity": "failed", "apps": ["*"]}
        assert "servers" not in json.dumps([w.body for w in nodes["web-1"].writes])

    def test_a_server_that_was_not_named_is_not_asked_for_its_applications(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=routed(servers=["web-1"], apps=["*.example.com"]))
        central.nodes["web-1"].apps = [SHOP]

        snapshot = apply(central)

        # web-1 is named, so its applications decide; web-3 is decided by its name alone.
        assert states(snapshot, "web-1") == [("channel", "ops", "created")]
        assert states(snapshot, "web-3") == [("channel", "ops", "out_of_reach")]
        assert node_items(snapshot, "web-3")[0]["why"] == "server"


class TestDomainsAndTags:
    def test_a_channel_for_a_domain_is_sent_where_an_application_has_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        central.nodes["web-1"].apps = [SHOP, BLOG]
        central.nodes["web-2"].apps = [BLOG]
        central.nodes["web-3"].apps = []
        save(settings, value=routed(apps=["*.example.com"]))

        snapshot = apply(central)

        assert states(snapshot, "web-1") == [("channel", "ops", "created")]
        assert states(snapshot, "web-2") == [("channel", "ops", "out_of_reach")]
        assert states(snapshot, "web-3") == [("channel", "ops", "out_of_reach")]
        assert node_items(snapshot, "web-2")[0]["why"] == "domain"
        assert node_items(snapshot, "web-2")[0]["coded"]["code"] == (
            "fleet_settings.out_of_reach.domain"
        )
        assert nodes["web-2"].writes == [] and nodes["web-3"].writes == []

    def test_a_channel_for_a_tag_is_sent_where_an_application_carries_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        central.nodes["web-1"].apps = [SHOP]
        central.nodes["web-2"].apps = [BLOG]
        save(settings, value=routed(tags=["prod"]))

        snapshot = apply(central, "web-1", "web-2")

        assert states(snapshot, "web-1") == [("channel", "ops", "created")]
        assert states(snapshot, "web-2") == [("channel", "ops", "out_of_reach")]
        assert node_items(snapshot, "web-2")[0]["why"] == "tag"

    def test_it_reaches_a_server_once_an_application_there_can_match(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        central.nodes["web-2"].apps = [BLOG]
        save(settings, value=routed(apps=["*.example.com"]))
        apply(central, "web-2")
        assert nodes["web-2"].held == {}

        central.nodes["web-2"].apps = [BLOG, {"domain": "api.example.com", "tags": []}]
        snapshot = apply(central, "web-2")

        assert states(snapshot, "web-2") == [("channel", "ops", "created")]
        assert nodes["web-2"].rows[("channel", "ops")]["state"] == "managed"

    def test_what_a_server_holds_is_left_as_it_is_when_the_route_stops_matching_there(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        central.nodes["web-1"].apps = [SHOP]
        save(settings, value=routed(apps=["*.example.com"]))
        apply(central, "web-1")
        row = dict(nodes["web-1"].rows[("channel", "ops")])
        written = len(nodes["web-1"].writes)

        central.nodes["web-1"].apps = [BLOG]
        save(settings, value={**routed(apps=["*.example.com"]), "enabled": False})
        snapshot = apply(central, "web-1")

        assert states(snapshot, "web-1") == [("channel", "ops", "out_of_reach")]
        assert nodes["web-1"].rows[("channel", "ops")] == row, "not let go, not deleted"
        assert len(nodes["web-1"].writes) == written, "and the new version was not sent"

    def test_the_applications_are_read_only_when_a_route_depends_on_them(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, "by-server", value=routed(servers=["web-1"]))
        save(settings, "by-severity", value=routed(min_severity="failed"))
        save(settings, "everything", value=routed(apps=["*"], server_events=True))

        apply(central, "web-1")

        assert asked_apps(central, "web-1") == 0

    def test_a_route_for_server_events_is_sent_everywhere_whatever_its_domains(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=routed(apps=["*.nowhere.example"], server_events=True))

        snapshot = apply(central)

        assert [states(snapshot, n)[0][2] for n in nodes] == ["created"] * 3


class TestWhenTheApplicationsCannotBeRead:
    def test_nothing_is_sent_and_the_server_says_why(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        central.nodes["web-2"].responses["/api/apps"] = (500, {"error": "boom"})
        central.nodes["web-1"].apps = [SHOP]
        save(settings, value=routed(apps=["*.example.com"]))
        save(settings, "REGION", "variable", value={"value": "eu"})

        snapshot = apply(central, "web-1", "web-2")

        failed = next(i for i in node_items(snapshot, "web-2") if i["name"] == "ops")
        assert failed["state"] == "failed"
        assert failed["code"] == "fleet_settings.reach_unknown"
        assert failed["coded"]["code"] == "fleet_settings.reach_unknown"
        assert ("channel", "ops") not in nodes["web-2"].held
        # The server's other settings went on, and the other server was not touched by it.
        assert ("variable", "REGION") in nodes["web-2"].rows
        assert states(snapshot, "web-1")[0] == ("channel", "ops", "created")
        assert snapshot["status"] == "failed"


class TestEditingARoute:
    def test_a_route_taken_away_on_the_central_is_cleared_on_the_server_that_had_it(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=routed(min_severity="failed"))
        apply(central, "web-1")
        assert nodes["web-1"].held[("channel", "ops")]["route"] == {"min_severity": "failed"}

        save(settings, value=CHANNEL)
        apply(central, "web-1")

        # A route that is simply not mentioned would be kept by the server: it is sent empty.
        assert nodes["web-1"].held[("channel", "ops")]["route"] == {}

    def test_a_route_that_was_never_there_is_not_sent_empty(
        self, central: Central, nodes: dict[str, ManagedNode], settings: FleetSettings
    ) -> None:
        save(settings, value=CHANNEL)

        apply(central, "web-1")

        assert nodes["web-1"].held[("channel", "ops")] == CHANNEL
