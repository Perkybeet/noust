# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What the console shows of the fleet's settings: for each setting, where each server stands.

A server's standing is two facts that the screen draws separately: where its value comes from
(``origin``: the fleet's, an exception the central set for it, an exception its own operator made,
or none because the server is independent) and whether it holds that value (``sync``). The state
is read from what each server says it manages and compared with what the central wants; nothing
secret is read from a server. What is pinned: every combination, a server that cannot be read is
said so and is not guessed at, and what the central no longer wants but a server still manages is
listed so it is not forgotten.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet.aggregate import FleetResult, NodeOutcome, set_aggregator
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.status import compose, read
from tests.support.fleet_actions import ADMIN
from tests.support.fleet_settings_node import ManagedNode
from tests.support.fleet_views_support import build_central

HELD = frozenset({"settings.manage"})


@pytest.fixture
def settings(store: NoustStore, secrets: Any) -> FleetSettings:
    from noust.core.store import NodeRecord

    for name in ("web-1", "web-2", "web-3", "web-4"):
        store.save_node(
            NodeRecord(
                name=name, ssh_host=name, ssh_port=22, ssh_user="u", host_key="k", console_port=1
            )
        )
    return FleetSettings(store, secrets)


def row(name: str, version: int, state: str = "managed", node: str = "web-1", **extra: Any) -> dict:
    return {
        "node": node,
        "resource": "variable",
        "name": name,
        "central": "nas",
        "version": version,
        "state": state,
        "change": "c0123456789ab",
        "applied_by": "carol",
        "applied_at": "2026-10-28T10:00:00+00:00",
        "restore_requested_at": None,
        **extra,
    }


def outcome(name: str, status: str = "ok", **extra: Any) -> NodeOutcome:
    return NodeOutcome(name=name, status=status, **extra)


def result(outcomes: list[NodeOutcome], items: list[dict[str, Any]]) -> FleetResult:
    return FleetResult("managed_settings", "2026-10-28T10:00:00+00:00", outcomes, items)


def save(settings: FleetSettings, name: str = "REGION", scope: str = "fleet", value: str = "eu"):
    return settings.save("variable", name, {"value": value}, scope=scope, person="carol", held=HELD)


def cell(view: dict[str, Any], setting: str, node: str) -> dict[str, Any]:
    entry = next(s for s in view["settings"] if s["name"] == setting)
    return next(c for c in entry["servers"] if c["node"] == node)


ALL = [outcome(n) for n in ("web-1", "web-2", "web-3", "web-4")]


class TestWhereEachServerStands:
    def test_a_server_that_holds_the_fleets_version_is_in_sync(
        self, settings: FleetSettings
    ) -> None:
        wanted = save(settings)

        view = compose(settings, result(ALL, [row("REGION", wanted.version)]))

        assert cell(view, "REGION", "web-1") == {
            "node": "web-1",
            "origin": "fleet",
            "sync": "in_sync",
            "waiting": False,
            "if_followed": None,
            "wanted_version": wanted.version,
            "applied_version": wanted.version,
            "state": "managed",
            "applied_by": "carol",
            "applied_at": "2026-10-28T10:00:00+00:00",
            "change": "c0123456789ab",
            "restore_requested": False,
            "reach": {"reaches": True, "why": None},
        }

    def test_a_server_that_has_not_got_it_yet_is_pending(self, settings: FleetSettings) -> None:
        wanted = save(settings)

        view = compose(settings, result(ALL, []))

        one = cell(view, "REGION", "web-2")
        assert (one["origin"], one["sync"], one["wanted_version"], one["applied_version"]) == (
            "fleet",
            "pending",
            wanted.version,
            None,
        )

    def test_a_server_at_an_older_version_is_pending_too(self, settings: FleetSettings) -> None:
        old = save(settings)
        new = save(settings, value="us")

        view = compose(settings, result(ALL, [row("REGION", old.version)]))

        one = cell(view, "REGION", "web-1")
        assert (one["sync"], one["applied_version"], one["wanted_version"]) == (
            "pending",
            old.version,
            new.version,
        )

    def test_a_server_with_an_exception_from_the_central_is_marked_so(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        own = save(settings, scope="node:web-3", value="us")

        view = compose(settings, result(ALL, [row("REGION", own.version, node="web-3")]))

        three = cell(view, "REGION", "web-3")
        assert (three["origin"], three["sync"], three["wanted_version"]) == (
            "central_exception",
            "in_sync",
            own.version,
        )
        assert cell(view, "REGION", "web-1")["origin"] == "fleet"
        entry = next(s for s in view["settings"] if s["name"] == "REGION")
        assert entry["exceptions"] == ["web-3"]

    def test_a_server_whose_operator_changed_it_is_an_exception_of_its_own(
        self, settings: FleetSettings
    ) -> None:
        wanted = save(settings)

        view = compose(
            settings,
            result(
                ALL,
                [
                    row(
                        "REGION",
                        wanted.version,
                        "overridden",
                        restore_requested_at="2026-10-28T11:00:00Z",
                    )
                ],
            ),
        )

        one = cell(view, "REGION", "web-1")
        assert (one["origin"], one["sync"], one["state"]) == (
            "local_exception",
            "diverged",
            "overridden",
        )
        assert one["restore_requested"] is True

    def test_an_independent_server_follows_nothing(self, settings: FleetSettings) -> None:
        save(settings)
        settings.set_mode("web-4", "independent", person="carol")

        view = compose(settings, result(ALL, []))

        four = cell(view, "REGION", "web-4")
        assert (four["origin"], four["sync"], four["wanted_version"]) == (
            "independent",
            "not_applicable",
            None,
        )
        server = next(s for s in view["servers"] if s["node"] == "web-4")
        assert server["mode"] == "independent"

    def test_an_independent_server_that_still_manages_something_is_waiting_to_let_go(
        self, settings: FleetSettings
    ) -> None:
        wanted = save(settings)
        settings.set_mode("web-4", "independent", person="carol")

        view = compose(settings, result(ALL, [row("REGION", wanted.version, node="web-4")]))

        assert cell(view, "REGION", "web-4")["sync"] == "release_pending"


class TestAServerThatCannotBeRead:
    @pytest.mark.parametrize(
        ("status", "sync"),
        [
            ("unreachable", "unreachable"),
            ("unsupported", "unsupported"),
            ("forbidden", "refused"),
            ("error", "unknown"),
        ],
    )
    def test_it_is_said_and_not_guessed(
        self, settings: FleetSettings, status: str, sync: str
    ) -> None:
        save(settings)
        outcomes = [outcome("web-1"), outcome("web-2", status, message="Why", code="why")]

        view = compose(settings, result(outcomes, []), nodes=["web-1", "web-2"])

        two = cell(view, "REGION", "web-2")
        assert (two["sync"], two["origin"]) == (sync, "fleet")
        assert two["applied_version"] is None
        server = next(s for s in view["servers"] if s["node"] == "web-2")
        assert (server["outcome"], server["message"], server["code"]) == (status, "Why", "why")

    def test_a_server_the_central_has_but_that_was_not_asked_is_unknown(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        view = compose(settings, result([outcome("web-1")], []), nodes=["web-1", "web-2"])

        assert cell(view, "REGION", "web-2")["sync"] == "unknown"


class TestWhatIsLeftOver:
    def test_what_a_server_manages_that_the_central_no_longer_wants_is_listed(
        self, settings: FleetSettings
    ) -> None:
        view = compose(settings, result(ALL, [row("OLD", 3, node="web-2")]))

        assert view["settings"] == []
        assert [(o["resource"], o["name"]) for o in view["released"]] == [("variable", "OLD")]
        assert view["released"][0]["servers"] == [
            {"node": "web-2", "state": "managed", "applied_version": 3, "central": "nas"}
        ]

    def test_a_server_that_is_marked_to_be_looked_at_says_so(self, settings: FleetSettings) -> None:
        save(settings)
        settings.request_resync("web-2")

        view = compose(settings, result(ALL, []))

        assert {s["node"]: s["pending_look"] for s in view["servers"]} == {
            "web-1": False,
            "web-2": True,
            "web-3": False,
            "web-4": False,
        }


class TestReadingTheServers:
    def test_the_servers_are_asked_what_they_manage_through_the_aggregator(
        self, tmp_path: Path
    ) -> None:
        central = build_central(tmp_path, ("web-1", "web-2"))
        try:
            fleet = FleetSettings(central.store, central.manager.secrets)
            wanted = save(fleet)
            one = ManagedNode(central.nodes["web-1"])
            one.held[("variable", "REGION")] = {"value": "eu"}
            one.rows[("variable", "REGION")] = {
                "resource": "variable",
                "name": "REGION",
                "central": "nas",
                "version": wanted.version,
                "state": "managed",
                "change": wanted.change_id,
                "applied_by": "carol",
                "applied_at": "t",
                "restore_requested_at": None,
            }
            central.nodes["web-2"].down = True

            view = read(fleet, asker=ADMIN, manager=central.manager)

            assert cell(view, "REGION", "web-1")["sync"] == "in_sync"
            assert cell(view, "REGION", "web-2")["sync"] == "unreachable"
            assert view["partial"] is True
        finally:
            set_aggregator(None)
            NoustStore.reset_instance()
