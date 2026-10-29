# Copyright (c) 2024-2026 Yago López Prado
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The record of deliveries of the whole fleet: every server's, newest first, in one list.

Each server delivers its own notifications and keeps its own record of them (``notification_deliveries``
in its store, read at ``GET /api/notifications/deliveries``). Whether a message reached the place it
was meant for is the question after an outage, and the answer is on the server that sent it; the
central asks all of them, as it does for every other resource of the fleet. What is pinned: the rows
are the servers' own, each with its server and the page where it can be sent again; they are one
list, newest first; a filter by state or channel is the server's own; the counts per state are each
server's whole record and not the part that fits in the list; the central is one more server; and
a server that refuses the operator is an outcome that is grouped like every other view's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from noust.core.store import NoustStore
from noust.fleet import deliveries
from noust.fleet.aggregate import gather, set_aggregator
from tests.support.fleet_views_support import Central, build_central

PATH = "/api/notifications/deliveries"


def delivery(ident: int, created: float, **fields: Any) -> dict[str, Any]:
    return {
        "id": ident,
        "notification_id": f"n{ident}",
        "kind": "deploy_failed",
        "code": "deploy.failed",
        "subject": "shop.example.com",
        "channel": "ops",
        "channel_type": "slack",
        "state": "sent",
        "attempts": 1,
        "created_at": created,
        "updated_at": created,
        "next_attempt_at": None,
        "last_error": None,
        "can_retry": False,
        "held_for": None,
        **fields,
    }


@pytest.fixture
def central(tmp_path: Path):
    built = build_central(tmp_path, ("web-2", "web-3"))
    deliveries.register()
    built.nodes["web-2"].responses[PATH] = (
        200,
        {
            "items": [
                delivery(2, 300.0),
                delivery(1, 100.0, state="failed", last_error="HTTP 500"),
            ],
            "counts": {"sent": 40, "failed": 3},
        },
    )
    built.nodes["web-3"].responses[PATH] = (
        200,
        {
            "items": [delivery(7, 200.0, channel="pager", channel_type="pagerduty")],
            "counts": {"sent": 9},
        },
    )
    yield built
    set_aggregator(None)
    NoustStore.reset_instance()


def asked(central: Central, node: str) -> list[dict[str, str]]:
    return [dict(r.url.params) for r in central.nodes[node].requests if r.url.path == PATH]


class TestTheRows:
    def test_they_are_the_servers_own_labelled_with_the_server_and_its_page(
        self, central: Central
    ) -> None:
        rows = gather("deliveries", manager=central.manager).items

        first = next(r for r in rows if r["id"] == 2)
        assert first["node"] == "web-2"
        assert first["local"] is False
        assert first["page"] == "/settings/notifications"
        assert first["href"] == "/n/web-2/settings/notifications"
        assert first["channel"] == "ops" and first["state"] == "sent"

    def test_they_are_one_list_newest_first_across_the_servers(self, central: Central) -> None:
        rows = gather("deliveries", manager=central.manager).items

        assert [(r["node"], r["id"]) for r in rows] == [("web-2", 2), ("web-3", 7), ("web-2", 1)]

    def test_what_failed_says_why_in_the_destinations_own_words(self, central: Central) -> None:
        rows = gather("deliveries", manager=central.manager).items

        failed = next(r for r in rows if r["state"] == "failed")
        assert failed["last_error"] == "HTTP 500"

    def test_a_server_that_does_not_answer_leaves_the_others_in_the_list(
        self, central: Central
    ) -> None:
        central.nodes["web-3"].down = True

        result = gather("deliveries", manager=central.manager)

        assert {r["node"] for r in result.items} == {"web-2"}
        assert result.outcome("web-3") is not None and result.outcome("web-3").status != "ok"  # type: ignore[union-attr]


class TestTheParameters:
    def test_a_server_is_asked_for_a_hundred_by_default(self, central: Central) -> None:
        gather("deliveries", manager=central.manager)

        assert asked(central, "web-2") == [{"limit": "100"}]

    @pytest.mark.parametrize(("given", "sent"), [(0, "1"), (7, "7"), (9999, "500"), ("x", "100")])
    def test_the_limit_is_kept_inside_what_a_server_takes(
        self, central: Central, given: Any, sent: str
    ) -> None:
        gather("deliveries", {"limit": given}, manager=central.manager)

        assert asked(central, "web-2")[0]["limit"] == sent

    def test_a_state_and_a_channel_are_the_servers_to_filter_by(self, central: Central) -> None:
        gather("deliveries", {"state": "failed", "channel": "ops"}, manager=central.manager)

        assert asked(central, "web-2") == [{"limit": "100", "state": "failed", "channel": "ops"}]

    def test_nothing_else_is_passed_on(self, central: Central) -> None:
        gather("deliveries", {"state": "failed", "q": "x", "node": "y"}, manager=central.manager)

        assert set(asked(central, "web-2")[0]) == {"limit", "state"}


class TestTheCounts:
    def test_each_server_says_how_many_there_are_in_each_state(self, central: Central) -> None:
        rows = gather("delivery_counts", manager=central.manager).items

        assert {r["node"]: r["counts"] for r in rows} == {
            "web-2": {"sent": 40, "failed": 3},
            "web-3": {"sent": 9},
        }

    def test_they_are_asked_for_without_asking_for_the_list(self, central: Central) -> None:
        gather("delivery_counts", manager=central.manager)

        assert asked(central, "web-2") == [{"limit": "1"}]

    def test_a_server_that_does_not_answer_has_no_counts(self, central: Central) -> None:
        central.nodes["web-3"].down = True

        rows = gather("delivery_counts", manager=central.manager).items

        assert [r["node"] for r in rows] == ["web-2"]
