# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What a setting's line says in the console: who follows it, and what is still waiting for whom (item 166).

The status of the fleet's settings answers three things beyond where a server stands. A cell says
``waiting`` when the central holds a version for that server it has not seen the server hold, also
when the server cannot be read (``sync`` says why it cannot, and does not change). A setting says
how many of the servers it is meant for follow it (``following`` of ``of``), and the central is
never one of them. A server that cannot take a kind of setting, or whose published ceiling does not
let the central set it, is told apart from one that is only behind. What is pinned: each of those
facts, the servers that are not in the count, and what a server that is left out of the fleet's
settings would be given if it followed them again.
"""

from __future__ import annotations

from typing import Any

import pytest

from noust.core.store import NodeRecord, NoustStore
from noust.fleet.aggregate import FleetResult, NodeOutcome
from noust.fleet.settings.desired import FleetSettings
from noust.fleet.settings.status import compose

HELD = frozenset({"settings.manage"})
CHANGED = "2026-10-28T12:00:00+00:00"
BEFORE = "2026-10-28T11:00:00+00:00"
AFTER = "2026-10-28T13:00:00+00:00"
NAMES = ("web-1", "web-2", "web-3", "web-4")


@pytest.fixture
def settings(store: NoustStore, secrets: Any, monkeypatch: pytest.MonkeyPatch) -> FleetSettings:
    monkeypatch.setattr("noust.fleet.settings.desired.utc_now_iso", lambda *a, **k: CHANGED)
    for name in NAMES:
        store.save_node(
            NodeRecord(
                name=name,
                ssh_host=name,
                ssh_port=22,
                ssh_user="u",
                host_key="k",
                console_port=1,
                last_seen=AFTER,
            )
        )
    return FleetSettings(store, secrets)


def save(settings: FleetSettings, name: str = "REGION", scope: str = "fleet", value: str = "eu"):
    return settings.save("variable", name, {"value": value}, scope=scope, person="carol", held=HELD)


def row(name: str, version: int, node: str, state: str = "managed", **extra: Any) -> dict:
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


def kinds(node: str, *available: str) -> dict[str, Any]:
    """What a server says of the kinds it can be managed for: a row that is not a setting."""
    return {"node": node, "kinds": list(available)}


def outcome(name: str, status: str = "ok") -> NodeOutcome:
    return NodeOutcome(name=name, status=status)


def result(outcomes: list[NodeOutcome], items: list[dict[str, Any]]) -> FleetResult:
    return FleetResult("managed_settings", "2026-10-28T10:00:00+00:00", outcomes, items)


def cell(view: dict[str, Any], node: str, setting: str = "REGION") -> dict[str, Any]:
    return next(c for c in entry(view, setting)["servers"] if c["node"] == node)


def entry(view: dict[str, Any], setting: str = "REGION") -> dict[str, Any]:
    return next(s for s in view["settings"] if s["name"] == setting)


ALL = [outcome(n) for n in NAMES]


class TestAServerThatIsBehindIsWaiting:
    def test_a_server_that_does_not_hold_it_yet_is_waiting(self, settings: FleetSettings) -> None:
        save(settings)

        view = compose(settings, result(ALL, []))

        assert cell(view, "web-1")["sync"] == "pending"
        assert cell(view, "web-1")["waiting"] is True

    def test_a_server_at_an_older_version_is_waiting(self, settings: FleetSettings) -> None:
        old = save(settings)
        save(settings, value="us")

        view = compose(settings, result(ALL, [row("REGION", old.version, "web-1")]))

        assert cell(view, "web-1")["waiting"] is True

    def test_a_server_that_holds_it_is_not_waiting(self, settings: FleetSettings) -> None:
        wanted = save(settings)

        view = compose(settings, result(ALL, [row("REGION", wanted.version, "web-1")]))

        assert (cell(view, "web-1")["sync"], cell(view, "web-1")["waiting"]) == ("in_sync", False)

    def test_what_a_servers_own_operator_changed_is_not_waiting_for_anything(
        self, settings: FleetSettings
    ) -> None:
        old = save(settings)
        save(settings, value="us")

        view = compose(settings, result(ALL, [row("REGION", old.version, "web-1", "overridden")]))

        assert (cell(view, "web-1")["sync"], cell(view, "web-1")["waiting"]) == (
            "diverged",
            False,
        )


class TestAServerThatCannotBeReadStillSaysWhatWaits:
    """``unreachable`` hid ``pending``; ``sync`` is unchanged and ``waiting`` says the rest."""

    def unreachable(
        self, settings: FleetSettings, seen: str | None, status: str = "unreachable"
    ) -> dict[str, Any]:
        settings.store.save_node(
            NodeRecord(
                name="web-2",
                ssh_host="web-2",
                ssh_port=22,
                ssh_user="u",
                host_key="k",
                console_port=1,
                last_seen=seen,
            )
        )
        outcomes = [outcome("web-1"), outcome("web-2", status)]
        return compose(settings, result(outcomes, []), nodes=["web-1", "web-2"])

    def test_a_setting_that_changed_after_the_server_last_answered_is_waiting(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        view = self.unreachable(settings, BEFORE)

        assert (cell(view, "web-2")["sync"], cell(view, "web-2")["waiting"]) == (
            "unreachable",
            True,
        )

    def test_a_setting_that_was_already_there_when_it_last_answered_is_not(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        view = self.unreachable(settings, AFTER)

        assert (cell(view, "web-2")["sync"], cell(view, "web-2")["waiting"]) == (
            "unreachable",
            False,
        )

    def test_a_server_that_never_answered_is_waiting(self, settings: FleetSettings) -> None:
        save(settings)

        assert cell(self.unreachable(settings, None), "web-2")["waiting"] is True

    def test_a_time_that_cannot_be_read_is_taken_as_waiting(self, settings: FleetSettings) -> None:
        save(settings)

        assert cell(self.unreachable(settings, "yesterday"), "web-2")["waiting"] is True

    def test_a_time_without_an_offset_is_read_as_utc(self, settings: FleetSettings) -> None:
        save(settings)

        view = self.unreachable(settings, "2026-10-28T13:00:00")

        assert cell(view, "web-2")["waiting"] is False

    def test_a_server_that_refuses_to_be_read_says_the_same(self, settings: FleetSettings) -> None:
        save(settings)

        view = self.unreachable(settings, BEFORE, "forbidden")

        assert (cell(view, "web-2")["sync"], cell(view, "web-2")["waiting"]) == ("refused", True)

    def test_an_answer_that_was_an_error_says_the_same(self, settings: FleetSettings) -> None:
        save(settings)

        view = self.unreachable(settings, BEFORE, "error")

        assert (cell(view, "web-2")["sync"], cell(view, "web-2")["waiting"]) == ("unknown", True)


class TestNothingWaitsWhereNothingCanBePushed:
    def test_an_independent_server_waits_for_nothing(self, settings: FleetSettings) -> None:
        save(settings)
        settings.set_mode("web-4", "independent", person="carol")

        view = compose(settings, result(ALL, []))

        assert (cell(view, "web-4")["sync"], cell(view, "web-4")["waiting"]) == (
            "not_applicable",
            False,
        )

    def test_a_server_the_setting_cannot_match_on_waits_for_nothing(
        self, settings: FleetSettings
    ) -> None:
        from noust.fleet.settings.reach import Reach

        settings.save(
            "channel",
            "ops",
            {"type": "slack", "route": {"servers": ["web-1"]}},
            person="carol",
            held=HELD,
        )
        reach = {("web-2", "channel", "ops"): Reach(False, "server")}

        view = compose(settings, result(ALL, []), reach=reach)

        assert (cell(view, "web-2", "ops")["sync"], cell(view, "web-2", "ops")["waiting"]) == (
            "not_applicable",
            False,
        )

    def test_a_server_that_runs_a_noust_without_the_kind_waits_for_nothing(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        items = [kinds("web-1", "variable"), kinds("web-2", "channel", "alert_rule")]

        view = compose(settings, result(ALL, items))

        assert (cell(view, "web-1")["sync"], cell(view, "web-1")["waiting"]) == ("pending", True)
        assert (cell(view, "web-2")["sync"], cell(view, "web-2")["waiting"]) == (
            "unsupported",
            False,
        )

    def test_a_server_that_did_not_say_which_kinds_it_has_is_taken_to_have_them(
        self, settings: FleetSettings
    ) -> None:
        save(settings)

        assert cell(compose(settings, result(ALL, [])), "web-3")["sync"] == "pending"

    def test_the_servers_own_kinds_are_not_listed_as_something_it_manages(
        self, settings: FleetSettings
    ) -> None:
        view = compose(settings, result(ALL, [kinds("web-1", "variable")]))

        assert view["settings"] == []
        assert view["released"] == []


class TestTheCeilingTheServerPublished:
    def lower(self, settings: FleetSettings, node: str, level: str) -> None:
        settings.store.set_node_access(node, level, False)

    def test_a_server_whose_ceiling_does_not_let_the_central_set_it_is_refused(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        self.lower(settings, "web-1", "deploy")

        view = compose(settings, result(ALL, []))

        one = cell(view, "web-1")
        assert (one["sync"], one["waiting"]) == ("refused", True)
        assert cell(view, "web-2")["sync"] == "pending"

    def test_one_that_already_holds_it_stays_in_sync(self, settings: FleetSettings) -> None:
        wanted = save(settings)
        self.lower(settings, "web-1", "read")

        view = compose(settings, result(ALL, [row("REGION", wanted.version, "web-1")]))

        assert cell(view, "web-1")["sync"] == "in_sync"

    def test_a_ceiling_that_was_never_read_is_not_a_refusal(self, settings: FleetSettings) -> None:
        save(settings)

        assert cell(compose(settings, result(ALL, [])), "web-3")["sync"] == "pending"

    def test_the_admin_ceiling_is_not_one(self, settings: FleetSettings) -> None:
        save(settings)
        self.lower(settings, "web-1", "admin")

        assert cell(compose(settings, result(ALL, [])), "web-1")["sync"] == "pending"


class TestHowManyFollowIt:
    def test_the_servers_in_sync_or_behind_follow_it(self, settings: FleetSettings) -> None:
        wanted = save(settings)

        view = compose(settings, result(ALL, [row("REGION", wanted.version, "web-1")]))

        assert (entry(view)["following"], entry(view)["of"]) == (4, 4)

    def test_a_server_that_cannot_be_reached_and_has_no_change_of_its_own_follows_it(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        outcomes = [outcome("web-1"), outcome("web-2", "unreachable")]

        view = compose(settings, result(outcomes, []), nodes=["web-1", "web-2"])

        assert (entry(view)["following"], entry(view)["of"]) == (2, 2)

    def test_an_exception_a_change_of_its_own_and_independence_do_not_follow(
        self, settings: FleetSettings
    ) -> None:
        wanted = save(settings)
        other = save(settings, scope="node:web-2", value="us")
        settings.set_mode("web-4", "independent", person="carol")
        items = [
            row("REGION", wanted.version, "web-1"),
            row("REGION", other.version, "web-2"),
            row("REGION", wanted.version, "web-3", "overridden"),
        ]

        view = compose(settings, result(ALL, items))

        assert (entry(view)["following"], entry(view)["of"]) == (1, 4)

    def test_a_server_that_refuses_or_runs_an_older_noust_does_not_follow(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        settings.store.set_node_access("web-1", "read", False)
        items = [kinds("web-2", "channel")]
        outcomes = [
            outcome("web-1"),
            outcome("web-2"),
            outcome("web-3"),
            outcome("web-4", "forbidden"),
        ]

        view = compose(settings, result(outcomes, items))

        assert [cell(view, n)["sync"] for n in NAMES] == [
            "refused",
            "unsupported",
            "pending",
            "refused",
        ]
        assert (entry(view)["following"], entry(view)["of"]) == (1, 4)

    def test_a_server_the_setting_cannot_match_on_is_not_one_it_is_meant_for(
        self, settings: FleetSettings
    ) -> None:
        from noust.fleet.settings.reach import Reach

        settings.save(
            "channel",
            "ops",
            {"type": "slack", "route": {"servers": ["web-1", "web-2"]}},
            person="carol",
            held=HELD,
        )
        reach = {
            (n, "channel", "ops"): Reach(
                n in ("web-1", "web-2"), None if n in ("web-1", "web-2") else "server"
            )
            for n in NAMES
        }

        view = compose(settings, result(ALL, []), reach=reach)

        assert (entry(view, "ops")["following"], entry(view, "ops")["of"]) == (2, 2)

    def test_the_central_is_never_counted(self, settings: FleetSettings) -> None:
        save(settings)
        central = NodeOutcome(name="central", status="ok", local=True)

        view = compose(settings, result([central, *ALL], []))

        assert (entry(view)["following"], entry(view)["of"]) == (4, 4)
        assert "central" not in {c["node"] for c in entry(view)["servers"]}

    def test_a_setting_that_is_only_one_servers_exception_is_one_of_one(
        self, settings: FleetSettings
    ) -> None:
        save(settings, scope="node:web-2")

        view = compose(settings, result(ALL, []))

        assert (entry(view)["following"], entry(view)["of"]) == (0, 1)

    def test_a_fleet_with_no_server_is_none_of_none(self, settings: FleetSettings) -> None:
        save(settings)

        view = compose(settings, result([], []), nodes=[])

        assert (entry(view)["following"], entry(view)["of"]) == (0, 0)


class TestWhatAServerWouldBeGivenIfItFollowedAgain:
    def test_an_independent_server_says_what_following_would_do(
        self, settings: FleetSettings
    ) -> None:
        first = save(settings)
        save(settings, name="CITY", value="madrid")
        settings.set_mode("web-4", "independent", person="carol")
        items = [row("REGION", first.version, "web-4")]

        view = compose(settings, result(ALL, items))

        assert cell(view, "web-4", "REGION")["if_followed"] == "in_sync"
        assert cell(view, "web-4", "CITY")["if_followed"] == "pending"
        # While it does follow, there is nothing to ask.
        assert cell(view, "web-1", "CITY")["if_followed"] is None

    def test_what_its_own_operator_changed_stays_its_own(self, settings: FleetSettings) -> None:
        wanted = save(settings)
        settings.set_mode("web-4", "independent", person="carol")
        items = [row("REGION", wanted.version, "web-4", "overridden")]

        view = compose(settings, result(ALL, items))

        assert cell(view, "web-4")["if_followed"] == "diverged"

    def test_a_server_that_cannot_be_read_says_why_it_cannot(self, settings: FleetSettings) -> None:
        save(settings)
        settings.set_mode("web-4", "independent", person="carol")
        outcomes = [outcome("web-1"), outcome("web-4", "unreachable")]

        view = compose(settings, result(outcomes, []), nodes=["web-1", "web-4"])

        assert cell(view, "web-4")["if_followed"] == "unreachable"

    def test_following_it_would_not_count_it_nor_make_it_wait_before_it_does(
        self, settings: FleetSettings
    ) -> None:
        save(settings)
        settings.set_mode("web-4", "independent", person="carol")

        view = compose(settings, result(ALL, []))

        assert cell(view, "web-4")["waiting"] is False
        assert (entry(view)["following"], entry(view)["of"]) == (3, 4)
