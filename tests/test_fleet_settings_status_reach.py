# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console shows of a channel whose route cannot match on some servers.

Every cell says whether the setting can match anything on that server (``reach``). A server it
cannot match on is not a server that is behind: the cell is ``not_applicable`` and says what
cannot match (the server is not named, no application has a domain or a tag the route names), and
what the server still holds of it is shown as it is. The applications are asked of the servers
only when a route depends on them, and a server whose applications could not be read is neither
said to be reached nor not to be.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import FleetResult, NodeOutcome, set_aggregator
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.status import compose, reach_of, read
from tests.support.fleet_actions import ADMIN
from tests.support.fleet_settings_node import ManagedNode
from tests.support.fleet_views_support import build_central

HELD = frozenset({"settings.manage"})
CHANNEL: dict[str, Any] = {"type": "slack", "settings": {"channel": "#ops"}}
SHOP = {"domain": "shop.example.com", "tags": ["prod"]}
BLOG = {"domain": "blog.example.org", "tags": []}


@pytest.fixture
def settings(store: NoustStore, secrets: Any) -> FleetSettings:
    from noust.core.store import NodeRecord

    for name in ("web-1", "web-2", "web-3"):
        store.save_node(
            NodeRecord(
                name=name, ssh_host=name, ssh_port=22, ssh_user="u", host_key="k", console_port=1
            )
        )
    return FleetSettings(store, secrets)


def save(settings: FleetSettings, name: str = "ops", **route: Any) -> Any:
    value = {**CHANNEL, "route": route} if route else CHANNEL
    return settings.save("channel", name, value, person="carol", held={"settings.manage"})


def seen(names: tuple[str, ...], items: list[dict[str, Any]] | None = None) -> FleetResult:
    outcomes = [NodeOutcome(name=n, status="ok") for n in names]
    return FleetResult("managed_settings", "2026-10-28T10:00:00+00:00", outcomes, items or [])


def held(node: str, name: str, version: int) -> dict[str, Any]:
    return {
        "node": node,
        "resource": "channel",
        "name": name,
        "central": "nas",
        "version": version,
        "state": "managed",
        "change": "c0123456789ab",
        "applied_by": "carol",
        "applied_at": "2026-10-28T10:00:00+00:00",
        "restore_requested_at": None,
    }


def cell(view: dict[str, Any], node: str, setting: str = "ops") -> dict[str, Any]:
    entry = next(s for s in view["settings"] if s["name"] == setting)
    return next(c for c in entry["servers"] if c["node"] == node)


class TestTheCellSaysWhetherTheRouteReaches:
    def test_every_cell_says_it_and_a_setting_without_a_route_reaches_everywhere(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        view = compose(settings, seen(("web-1", "web-2", "web-3")))

        for node in ("web-1", "web-2", "web-3"):
            assert cell(view, node)["reach"] == {"reaches": True, "why": None}
            assert cell(view, node)["sync"] == "pending"

    def test_a_server_the_route_cannot_match_on_is_not_behind(
        self, settings: FleetSettings
    ) -> None:
        wanted = save(settings, servers=["web-1"])
        names = ("web-1", "web-2", "web-3")

        view = compose(settings, seen(names), reach=reach_of(settings, names, None))

        assert cell(view, "web-1")["sync"] == "pending"
        other = cell(view, "web-2")
        assert (other["sync"], other["origin"], other["wanted_version"]) == (
            "not_applicable",
            "fleet",
            wanted.version,
        )
        assert other["reach"] == {"reaches": False, "why": "server"}

    def test_what_a_server_still_holds_is_shown_though_it_is_not_sent_any_more(
        self, settings: FleetSettings
    ) -> None:
        old = save(settings)
        save(settings, servers=["web-1"])
        names = ("web-1", "web-2")

        view = compose(
            settings,
            seen(names, [held("web-2", "ops", old.version)]),
            nodes=list(names),
            reach=reach_of(settings, names, None),
        )

        other = cell(view, "web-2")
        assert other["sync"] == "not_applicable"
        assert other["applied_version"] == old.version
        assert other["state"] == "managed"

    def test_a_route_by_domain_depends_on_the_applications_of_each_server(
        self, settings: FleetSettings
    ) -> None:
        save(settings, apps=["*.example.com"])
        names = ("web-1", "web-2", "web-3")
        apps = {"web-1": [SHOP, BLOG], "web-2": [BLOG], "web-3": None}

        view = compose(settings, seen(names), reach=reach_of(settings, names, apps))

        assert cell(view, "web-1")["reach"] == {"reaches": True, "why": None}
        assert cell(view, "web-2")["reach"] == {"reaches": False, "why": "domain"}
        assert cell(view, "web-2")["sync"] == "not_applicable"
        # web-3 did not say: neither reached nor not reached, and it is not guessed at.
        assert cell(view, "web-3")["reach"] == {"reaches": None, "why": None}
        assert cell(view, "web-3")["sync"] == "pending"

    def test_an_independent_server_is_independent_whatever_the_route_says(
        self, settings: FleetSettings
    ) -> None:
        save(settings, servers=["web-1"])
        settings.set_mode("web-2", "independent", person="carol")
        names = ("web-1", "web-2")

        view = compose(
            settings, seen(names), nodes=list(names), reach=reach_of(settings, names, None)
        )

        assert cell(view, "web-2")["origin"] == "independent"


class TestAskingTheServers:
    @pytest.fixture
    def built(self, tmp_path: Path):
        central = build_central(tmp_path, ("web-1", "web-2"))
        store = central.store
        yield central, FleetSettings(store, central.manager.secrets)
        set_aggregator(None)
        NoustStore.reset_instance()

    def test_the_applications_are_asked_only_when_a_route_depends_on_them(self, built) -> None:
        central, settings = built
        nodes = {n: ManagedNode(node) for n, node in central.nodes.items()}
        save(settings, servers=["web-1"])

        view = read(settings, asker=ADMIN, manager=central.manager)

        assert cell(view, "web-2")["reach"] == {"reaches": False, "why": "server"}
        assert all(
            r.url.path != "/api/apps" for node in central.nodes.values() for r in node.requests
        )
        assert nodes

    def test_a_route_by_domain_is_judged_by_what_each_server_lists(self, built) -> None:
        central, settings = built
        for node in central.nodes.values():
            ManagedNode(node)
        central.nodes["web-1"].apps = [SHOP]
        central.nodes["web-2"].apps = [BLOG]
        save(settings, apps=["*.example.com"])

        view = read(settings, asker=ADMIN, manager=central.manager)

        assert cell(view, "web-1")["reach"] == {"reaches": True, "why": None}
        assert cell(view, "web-2")["reach"] == {"reaches": False, "why": "domain"}

    def test_a_server_that_does_not_list_its_applications_is_not_guessed_at(self, built) -> None:
        central, settings = built
        for node in central.nodes.values():
            ManagedNode(node)
        central.nodes["web-1"].apps = [SHOP]
        central.nodes["web-2"].responses["/api/apps"] = (500, {"error": "boom"})
        save(settings, apps=["*.example.com"])

        view = read(settings, asker=ADMIN, manager=central.manager)

        assert cell(view, "web-1")["reach"] == {"reaches": True, "why": None}
        assert cell(view, "web-2")["reach"] == {"reaches": None, "why": None}
